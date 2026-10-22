"""NativeAudioSink: Synchronous, blocking audio sink using mpv with clean interrupt handling.

Before mpv starts, the sink asks the sound-yield gate whether another app
is playing sound; if so the playback is held (in queue order) until that
sound stops, the longest wait passes, or interrupt() is called. Past the
longest wait the playback is set aside (PlaybackYielded), never silently
dropped: the daemon records it in the missed list. See sound_yield.py,
missed_speech.py and ADR-015.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from state_paths import state_path
from typing import Any, Callable

import tempfile

from mpv_ipc import MpvIpc, MpvIpcError
from speech_priority import boosted_argv

from sound_yield import (
    ABORTED,
    EXPIRED,
    HoldOutcome,
    SoundYieldGate,
    REASON_MEETING,
)

# Ensure unittest.mock is available if running under a test runner where unittest is loaded
if "unittest" in sys.modules:
    try:
        import unittest.mock  # noqa: F401
    except ImportError:
        pass

logger = logging.getLogger(__name__)

MPV_PID_PATH = state_path("auto-speech-mpv.pid")
# The mpv audio output (its --ao). Unset: mpv picks the system output. "null" plays
# to nothing: the test suite sets it so no test can reach the speakers
# (his 2026-10-20 10:40 "turn off the speech tests").
AUDIO_OUTPUT_ENV = "AUTO_SPEECH_MPV_AO"


PLAY_STALL_ENV = "AUTO_SPEECH_PLAY_STALL_SECONDS"
DEFAULT_PLAY_STALL_SECONDS = 15.0


def play_stall_from_env() -> float:
    """The stall watch's window from AUTO_SPEECH_PLAY_STALL_SECONDS; the
    default when unset or unreadable; 0 turns the watch off."""
    raw = os.environ.get(PLAY_STALL_ENV, "").strip()
    if not raw:
        return DEFAULT_PLAY_STALL_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_PLAY_STALL_SECONDS


def audio_output_from_env() -> str | None:
    """The mpv audio output named by AUTO_SPEECH_MPV_AO, or None when unset or blank."""
    value = (os.environ.get(AUDIO_OUTPUT_ENV) or "").strip()
    return value or None


class AudioSinkError(RuntimeError):
    """Base exception for all NativeAudioSink errors."""


MPV_FALLBACK_PATHS = ("/opt/homebrew/bin/mpv", "/usr/local/bin/mpv")


class MpvNotInstalledError(AudioSinkError):
    """Raised when the mpv binary is not found on PATH."""


class PlaybackError(AudioSinkError):
    """Raised when mpv exits with a non-zero exit code during playback."""


class PlaybackYielded(AudioSinkError):
    """Raised when a playback was held for another app's sound up to the
    longest wait. The audio never started; the caller keeps the line (the
    daemon puts it on the missed list)."""

    def __init__(self, path: Path, outcome: HoldOutcome) -> None:
        self.path = path
        self.outcome = outcome
        who = ", ".join(outcome.blockers) or "another app"
        if getattr(outcome, "reason", "") == REASON_MEETING:
            message = f"playback of {path} set aside: in a meeting ({who})"
        else:
            message = f"playback of {path} set aside: held {outcome.waited_seconds:.0f}s while {who} played sound"
        super().__init__(message)

    @property
    def in_meeting(self) -> bool:
        return getattr(self.outcome, "reason", "") == REASON_MEETING


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
    # One progress read may wait this long for mpv's reply. A read that misses counts as
    # "no progress" for the stall window (ADR-019); it never ends the line on its own.
    _PROGRESS_READ_TIMEOUT = 1.0

    def __init__(
        self,
        mpv_path: str | Path | None = None,
        *,
        sound_yield: SoundYieldGate | Any | None = None,
        audio_output: str | None = None,
    ) -> None:
        """Initialize NativeAudioSink.

        Args:
            mpv_path: Optional custom path to mpv binary. If None, resolved via PATH.
            audio_output: mpv's audio output (--ao), e.g. "null" for silence. None
                reads AUTO_SPEECH_MPV_AO; unset or blank leaves mpv's default.
            sound_yield: The gate consulted before every playback. None means
                the default gate (DEFAULT_SOUND_YIELD_FACTORY); False means
                no gate at all (never hold).
        """
        self._custom_mpv_path = str(mpv_path) if mpv_path is not None else None
        self._audio_output = (
            (audio_output.strip() or None) if audio_output is not None else audio_output_from_env()
        )
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
        self._stall_seconds: float = play_stall_from_env()
        self._ipc_path: Path | None = None
        self._last_position: float | None = None
        self._speed: float = 1.0

    @property
    def stall_seconds(self) -> float:
        """How long mpv may report no progress before the play is given up
        (ADR-019). 0: no stall watch; the wall-clock budget alone applies."""
        return self._stall_seconds

    @stall_seconds.setter
    def stall_seconds(self, value: float) -> None:
        self._stall_seconds = max(0.0, float(value))

    @property
    def last_position(self) -> float | None:
        """mpv's last reported time-pos of the current or latest play."""
        return self._last_position

    @property
    def audio_output(self) -> str | None:
        """The mpv audio output this sink plays to (None: mpv's default)."""
        return self._audio_output

    def mpv_argv(self, mpv_bin: str, wav_path: str | Path) -> list[str]:
        """The mpv command line for one file: quiet, no video, no media keys, and
        --ao=<audio_output> when one is set (the test suite's "null")."""
        argv = [
            mpv_bin,
            "--really-quiet",
            "--no-video",
            "--keep-open=no",
            "--idle=no",
            "--input-media-keys=no",  # never claim the macOS media keys (AirPods play/pause belongs to other apps)
        ]
        if self._audio_output:
            argv.append(f"--ao={self._audio_output}")
        if self._speed != 1.0:
            argv.append(f"--speed={self._speed:g}")
        if self._ipc_path is not None:
            argv.append(f"--input-ipc-server={self._ipc_path}")
        argv.append(str(wav_path))
        return argv

    def _new_ipc_path(self) -> Path | None:
        """A fresh socket path for one play's progress reads; None when the
        stall watch is off (no socket, no flag on mpv's command line)."""
        if self._stall_seconds <= 0:
            return None
        return Path(tempfile.gettempdir()) / f"auto-speech-mpv-{os.getpid()}-{int(time.time() * 1000) % 100000000}.sock"

    def _read_progress(self) -> tuple[float, bool] | None:
        """mpv's (time-pos, paused) over its socket; None when it cannot be
        read (mpv still starting, the socket gone, or no socket at all)."""
        path = self._ipc_path
        if path is None:
            return None
        try:
            pos_reply = MpvIpc.send(["get_property", "time-pos"], path, timeout=self._PROGRESS_READ_TIMEOUT)
            pause_reply = MpvIpc.send(["get_property", "pause"], path, timeout=self._PROGRESS_READ_TIMEOUT)
        except MpvIpcError:
            return None
        pos = pos_reply.get("data")
        if not isinstance(pos, (int, float)):
            return None
        return float(pos), bool(pause_reply.get("data", False))

    def _wait_for_exit(self, proc: subprocess.Popen, path: Path, timeout: float | None) -> tuple[Any, Any]:
        """Wait for mpv to finish, watching its progress (ADR-019). The play
        is ended early only when mpv reports no progress for stall_seconds
        (a stall, raised as PlaybackError "stalled"), or, while its position
        cannot be read at all, when the wall-clock `timeout` passes (raised
        as PlaybackError "timed out"). A clip that is still advancing is
        never cut off, whatever the wall clock says."""
        start = time.monotonic()
        last_advance = start
        last_pos: float | None = None
        ever_read = False          # once mpv has reported a position, the wall clock is off for good
        stall = float(self._stall_seconds)
        self._last_position = None
        while True:
            now = time.monotonic()
            slice_s = 1.0
            if timeout is not None and not ever_read:
                slice_s = max(0.05, min(1.0, (start + timeout) - now))
            called_at = time.monotonic()
            try:
                return proc.communicate(timeout=slice_s)
            except subprocess.TimeoutExpired:
                pass
            if time.monotonic() - called_at < 0.01:   # a mock that raises at once: do not spin
                time.sleep(0.05)
            now = time.monotonic()
            progress = self._read_progress()
            if progress is not None:
                ever_read = True
                pos, paused = progress
                self._last_position = pos
                if paused or last_pos is None or pos != last_pos:
                    last_pos, last_advance = pos, now
                elif stall > 0 and now - last_advance >= stall:
                    self.interrupt()
                    raise PlaybackError(f"Playback stalled for {stall:.0f}s at {pos:.1f}s: {path}")
                continue
            if ever_read:
                # mpv closed its socket (it is finishing) or stopped answering: a stall
                # only after the window with no progress; the wall clock never applies.
                if stall > 0 and now - last_advance >= stall:
                    self.interrupt()
                    raise PlaybackError(f"Playback stalled for {stall:.0f}s at {last_pos or 0.0:.1f}s: {path}")
                continue
            # No position was ever read: only then does the wall clock count.
            if timeout is not None and now - start >= timeout:
                self.interrupt()
                raise PlaybackError(f"Playback timed out after {timeout}s: {path}")

    def _resolve_mpv(self) -> str:
        """Resolve mpv binary path, raising MpvNotInstalledError if not found."""
        if self._custom_mpv_path:
            return self._custom_mpv_path
        binary = shutil.which("mpv")
        if binary:
            return binary
        # launchd and other sparse-PATH starters leave out Homebrew's bin:
        # the 2026-10-20 20:02 narrator spoke nothing for two hours on that.
        for fallback in MPV_FALLBACK_PATHS:
            if os.path.isfile(fallback) and os.access(fallback, os.X_OK):
                return fallback
        raise MpvNotInstalledError("mpv not found on PATH or in " + ", ".join(MPV_FALLBACK_PATHS) + ". Install with: brew install mpv")

    def play(
        self,
        wav_path: Path | str,
        timeout: float | None = None,
        *,
        hold: bool = True,
        already_waited: float = 0.0,
        on_hold: Callable[[tuple[str, ...]], None] | None = None,
        on_start: Callable[[], None] | None = None,
    ) -> None:
        """Plays wav file synchronously via mpv, blocking until playback finishes.

        Args:
            wav_path: Path to the WAV file to play.
            timeout: Optional maximum duration in seconds for playback.
            hold: False skips the sound-yield gate for this playback (an
                explicit replay of a missed line plays over other sound).
            already_waited: How long this speech has already waited in the
                queue; counts against the gate's longest wait.
            on_hold: Called once, with the blockers' names, when the gate
                holds this playback.
            on_start: Called once, just before mpv starts (after any hold).

        Raises:
            FileNotFoundError: If the WAV file does not exist.
            MpvNotInstalledError: If mpv is not installed.
            PlaybackError: If mpv terminates with a non-zero exit code and was not interrupted.
            PlaybackYielded: If the hold reached the longest wait; nothing played.
        """
        path = Path(wav_path)
        if not path.is_file():
            raise FileNotFoundError(f"Audio file not found: {path}")

        mpv_bin = self._resolve_mpv()

        # Serializes playback across threads: only one file plays at a time.
        with self._playback_lock:
            if not hold:
                self._last_hold = None
            elif not self._await_sound_yield(path, already_waited=already_waited, on_hold=on_hold):
                return
            if on_start is not None:
                try:
                    on_start()
                except Exception:  # a listener never stops the play
                    logger.exception("on_start failed for %s", path)
            with self._state_lock:
                self._interrupted = False
                self._ipc_path = self._new_ipc_path()
                proc = subprocess.Popen(
                    boosted_argv(self.mpv_argv(mpv_bin, wav_path)),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                self._proc = proc
                try:
                    MPV_PID_PATH.write_text(str(proc.pid))
                except OSError:
                    pass

            try:
                stdout, stderr = self._wait_for_exit(proc, path, timeout)
                retcode = proc.returncode
            except PlaybackError:
                raise
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
                    ipc = self._ipc_path
                    self._ipc_path = None
                if ipc is not None:
                    try:
                        ipc.unlink()
                    except OSError:
                        pass

            # If interrupted, return cleanly without error.
            if interrupted or retcode in (-signal.SIGTERM, -signal.SIGKILL, 143, 137):
                logger.info("Playback interrupted for %s", path)
                return

            if retcode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip() if stderr else ""
                raise PlaybackError(f"mpv exited with code {retcode} for {path}: {err_msg}")

    def _await_sound_yield(
        self,
        path: Path,
        *,
        already_waited: float = 0.0,
        on_hold: Callable[[tuple[str, ...]], None] | None = None,
    ) -> bool:
        """Hold for another app's sound. True = go ahead and play; False =
        the hold was aborted by interrupt() (return quietly, like an
        interrupted playback). Raises PlaybackYielded when the longest
        wait passed: the playback is set aside, not started."""
        gate = self._sound_yield
        if gate is None:
            self._last_hold = None
            return True
        self._hold_abort.clear()
        with self._state_lock:
            self._holding_since = time.monotonic()
            self._interrupted = False
        try:
            outcome = gate.wait_until_clear(
                should_abort=self._hold_abort.is_set,
                already_waited=already_waited,
                on_hold=on_hold,
            )
        finally:
            with self._state_lock:
                self._holding_since = None
        self._last_hold = outcome
        if outcome.status == ABORTED:
            with self._state_lock:
                self._interrupted = True
            logger.info("Playback hold aborted for %s", path)
            return False
        if outcome.status == EXPIRED:
            raise PlaybackYielded(path, outcome)
        return True

    @property
    def last_hold(self) -> HoldOutcome | None:
        """How the most recent playback's hold ended (None when the gate
        was skipped or the sink has not played yet)."""
        return self._last_hold

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
                "reason": getattr(last, "reason", ""),
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

    # -- the speech control's verbs, on the playback now running (ADR-022) ------

    def _mpv_command(self, command: list) -> bool:
        """One command to the mpv now playing, over its socket. False when
        nothing plays, the stall watch (and so the socket) is off, or mpv
        does not answer."""
        with self._state_lock:
            path, proc = self._ipc_path, self._proc
        if path is None or proc is None or proc.poll() is not None:
            return False
        try:
            reply = MpvIpc.send(command, path, timeout=self._PROGRESS_READ_TIMEOUT)
        except MpvIpcError:
            return False
        return reply.get("error") == "success"

    def pause(self) -> bool:
        """Pause the line now playing. A paused line is not a stall."""
        return self._mpv_command(["set_property", "pause", True])

    def resume(self) -> bool:
        return self._mpv_command(["set_property", "pause", False])

    def restart(self) -> bool:
        """The line now playing, from its start."""
        return self._mpv_command(["seek", 0, "absolute"])

    def skip(self) -> bool:
        """End the line now playing (never a held one: that is interrupt's)."""
        if not self.is_playing:
            return False
        self.interrupt()
        return True

    MIN_SPEED, MAX_SPEED = 0.25, 4.0

    @property
    def speed(self) -> float:
        """The playback rate every play starts at (mpv --speed)."""
        return self._speed

    def set_speed(self, rate: float) -> float:
        """Set the playback rate for this and every later line; returns the
        rate in effect (clamped to MIN_SPEED..MAX_SPEED)."""
        rate = min(self.MAX_SPEED, max(self.MIN_SPEED, float(rate)))
        self._speed = rate
        self._mpv_command(["set_property", "speed", rate])
        return rate

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
