"""NativeAudioSink: Synchronous, blocking audio sink using mpv with clean interrupt handling."""

from __future__ import annotations

import logging
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path

# Ensure unittest.mock is available if running under a test runner where unittest is loaded
if "unittest" in sys.modules:
    try:
        import unittest.mock  # noqa: F401
    except ImportError:
        pass

logger = logging.getLogger(__name__)


class AudioSinkError(RuntimeError):
    """Base exception for all NativeAudioSink errors."""


class MpvNotInstalledError(AudioSinkError):
    """Raised when the mpv binary is not found on PATH."""


class PlaybackError(AudioSinkError):
    """Raised when mpv exits with a non-zero exit code during playback."""


class NativeAudioSink:
    """Synchronous audio sink playing WAV files via mpv with interrupt support.

    Playback is blocking and sequential. An active playback can be interrupted
    immediately from another thread via interrupt().
    """

    _TERMINATE_TIMEOUT = 0.5  # Seconds to wait for SIGTERM before escalating to SIGKILL
    _KILL_TIMEOUT = 0.5  # Seconds to wait after SIGKILL

    def __init__(self, mpv_path: str | Path | None = None) -> None:
        """Initialize NativeAudioSink.

        Args:
            mpv_path: Optional custom path to mpv binary. If None, resolved via PATH.
        """
        self._custom_mpv_path = str(mpv_path) if mpv_path is not None else None
        self._playback_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._interrupted: bool = False

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
                    Path("/tmp/auto-speech-mpv.pid").write_text(str(proc.pid))
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
                            Path("/tmp/auto-speech-mpv.pid").unlink(missing_ok=True)
                        except OSError:
                            pass

            # If interrupted, return cleanly without error.
            if interrupted or retcode in (-signal.SIGTERM, -signal.SIGKILL, 143, 137):
                logger.info("Playback interrupted for %s", path)
                return

            if retcode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip() if stderr else ""
                raise PlaybackError(f"mpv exited with code {retcode} for {path}: {err_msg}")

    def interrupt(self) -> None:
        """Terminates any currently active mpv playback process immediately.

        Safe to call from any thread and idempotent if no playback is active.
        """
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
