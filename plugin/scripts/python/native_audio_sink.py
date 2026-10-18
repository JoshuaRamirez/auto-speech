"""NativeAudioSink: Synchronous, blocking audio sink using mpv with clean interrupt handling.

Before mpv starts, the sink asks the sound-yield gate whether another app
is playing sound; if so the playback is held (in queue order) until that
sound stops, the longest wait passes, or interrupt() is called. See
sound_yield.py and ADR-015.
"""

from __future__ import annotations

import logging
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from state_paths import state_path
from typing import Any, Callable

from sound_yield import (
    ABORTED,
    EXPIRED_DROP,
    HoldOutcome,
    SoundYieldGate,
)

# Ensure unittest.mock is available if running under a test runner where unittest is loaded
if "unittest" in sys.modules:
    try:
        import unittest.mock  # noqa: F401
    except ImportError:
        pass

logger = logging.getLogger(__name__)

MPV_PID_PATH = state_path("auto-speech-mpv.pid")


class AudioSinkError(RuntimeError):
    """Base exception for all NativeAudioSink errors."""


class MpvNotInstalledError(AudioSinkError):
    """Raised when the mpv binary is not found on PATH."""


class PlaybackError(AudioSinkError):
    """Raised when mpv exits with a non-zero exit code during playback."""


class PlaybackYielded(AudioSinkError):
    """Raised when a playback was held for another app's sound up to the
    longest wait and the policy says drop it. The audio never started."""

    def __init__(self, path: Path, outcome: HoldOutcome) -> None:
        self.path = path
        self.outcome = outcome
        who = ", ".join(outcome.blockers) or "another app"
        super().__init__(
            f"playback of {path} dropped: held {outcome.waited_seconds:.0f}s while {who} played sound"
        )


def default_sound_yield_gate() -> SoundYieldGate | None:
    """The gate a sink gets when none is injected. Tests that drive a real
    sink against a mocked mpv rebind DEFAULT_SOUND_YIELD_FACTORY to
    `lambda: None` so they never touch Core Audio."""
    return SoundYieldGate()


DEFAULT_SOUND_YIELD_FACTORY: Callable[[], SoundYieldGate | None] = default_sound_yield_gate


class NativeAudioSink:
    """Synchronous audio sink playing WAV files via mpv with interrupt support.

    Playback is blocking and sequential. An active playback can be interrupted
    immediately from another thread via interrupt().
    """

    _TERMINATE_TIMEOUT = 0.5  # Seconds to wait for SIGTERM before escalating to SIGKILL
    _KILL_TIMEOUT = 0.5  # Seconds to wait after SIGKILL

    def __init__(
        self,
        mpv_path: str | Path | None = None,
        *,
        sound_yield: SoundYieldGate | Any | None = None,
    ) -> None:
        """Initialize NativeAudioSink.

        Args:
            mpv_path: Optional custom path to mpv binary. If None, resolved via PATH.
            sound_yield: The gate consulted before every playback. None means
                the default gate (DEFAULT_SOUND_YIELD_FACTORY); False means
                no gate at all (never hold).
        """
        self._custom_mpv_path = str(mpv_path) if mpv_path is not None else None
        self._playback_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._interrupted: bool = False
        if sound_yield is None:
            self._sound_yield = DEFAULT_SOUND_YIELD_FACTORY()
        elif sound_yield is False:
            self._sound_yield = None
        else:
            self._sound_yield = sound_yield
        self._hold_abort = threading.Event()
        self._holding_since: float | None = None
        self._last_hold: HoldOutcome | None = None

    def _resolve_mpv(self) -> str:
        """Resolve mpv binary path, raising MpvNotInstalledError if not found."""
        if self._custom_mpv_path:
            return self._custom_mpv_path
        binary = shutil.which("mpv")
        if not binary:
            raise MpvNotInstalledError("mpv not found on PATH. Install with: brew install mpv")
        return binary

    def play(self, wav_path: Path | str, timeout: float | None = None) -> None:
        """Plays wav file synchronously via mpv, blocking until playback finishes.

        Args:
            wav_path: Path to the WAV file to play.
            timeout: Optional maximum duration in seconds for playback.

        Raises:
            FileNotFoundError: If the WAV file does not exist.
            MpvNotInstalledError: If mpv is not installed.
            PlaybackError: If mpv terminates with a non-zero exit code and was not interrupted.
        """
        path = Path(wav_path)
        if not path.is_file():
            raise FileNotFoundError(f"Audio file not found: {path}")

        mpv_bin = self._resolve_mpv()

        # Serializes playback across threads: only one file plays at a time.
        with self._playback_lock:
            if not self._await_sound_yield(path):
                return
            with self._state_lock:
                self._interrupted = False
                proc = subprocess.Popen(
                    [
                        mpv_bin,
                        "--really-quiet",
                        "--no-video",
                        "--keep-open=no",
                        "--idle=no",
                        str(wav_path),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                self._proc = proc
                try:
                    MPV_PID_PATH.write_text(str(proc.pid))
                except OSError:
                    pass

            try:
                stdout, stderr = proc.communicate(timeout=timeout)
                retcode = proc.returncode
            except subprocess.TimeoutExpired:
                self.interrupt()
                raise PlaybackError(f"Playback timed out after {timeout}s: {path}")
            except Exception:
                self.interrupt()
                raise
            finally:
                with self._state_lock:
                    interrupted = self._interrupted
                    if self._proc is proc:
                        self._proc = None
                        try:
                            if MPV_PID_PATH.is_file() and MPV_PID_PATH.read_text().strip() == str(proc.pid):
                                MPV_PID_PATH.unlink(missing_ok=True)
                        except (OSError, ValueError):
                            pass

            # If interrupted, return cleanly without error.
            if interrupted or retcode in (-signal.SIGTERM, -signal.SIGKILL, 143, 137):
                logger.info("Playback interrupted for %s", path)
                return

            if retcode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip() if stderr else ""
                raise PlaybackError(f"mpv exited with code {retcode} for {path}: {err_msg}")

    def _await_sound_yield(self, path: Path) -> bool:
        """Hold for another app's sound. True = go ahead and play; False =
        the hold was aborted by interrupt() (return quietly, like an
        interrupted playback). Raises PlaybackYielded when the longest
        wait passed and the policy is drop."""
        gate = self._sound_yield
        if gate is None:
            return True
        self._hold_abort.clear()
        with self._state_lock:
            self._holding_since = time.monotonic()
            self._interrupted = False
        try:
            outcome = gate.wait_until_clear(should_abort=self._hold_abort.is_set)
        finally:
            with self._state_lock:
                self._holding_since = None
        self._last_hold = outcome
        if outcome.status == ABORTED:
            with self._state_lock:
                self._interrupted = True
            logger.info("Playback hold aborted for %s", path)
            return False
        if outcome.status == EXPIRED_DROP:
            raise PlaybackYielded(path, outcome)
        return True

    @property
    def hold_status(self) -> dict[str, Any]:
        """For the daemon's status reply: is a playback held right now, and how long."""
        with self._state_lock:
            since = self._holding_since
        last = self._last_hold
        return {
            "holding": since is not None,
            "held_seconds": round(time.monotonic() - since, 1) if since is not None else 0.0,
            "last_hold": None
            if last is None
            else {
                "status": last.status,
                "waited_seconds": round(last.waited_seconds, 1),
                "blockers": list(last.blockers),
            },
        }

    def interrupt(self) -> None:
        """Terminates any currently active mpv playback process immediately,
        and releases a playback that is held for another app's sound.

        Safe to call from any thread and idempotent if no playback is active.
        """
        self._hold_abort.set()
        with self._state_lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                return
            self._interrupted = True

        # Signal process outside state lock so other inspections don't block
        try:
            proc.terminate()
            try:
                proc.wait(timeout=self._TERMINATE_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=self._KILL_TIMEOUT)
                except (subprocess.TimeoutExpired, ProcessLookupError):
                    pass
        except ProcessLookupError:
            pass

    @property
    def is_playing(self) -> bool:
        """Returns True if playback is currently active."""
        with self._state_lock:
            return self._proc is not None and self._proc.poll() is None

    @property
    def was_interrupted(self) -> bool:
        """Returns True if the most recent playback was interrupted."""
        with self._state_lock:
            return self._interrupted
