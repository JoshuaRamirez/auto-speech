"""MpvController: spawn mpv detached, own the singleton session lifecycle."""
from __future__ import annotations

import fcntl
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from mpv_ipc import MpvIpc, MpvIpcError
from session_dir import SessionDir


# Serializes the kill-prior + spawn + await-socket sequence across every
# process that calls MpvController.start() — autoplay workers, narrator
# daemon, and the web app. Without this, two callers can race on the
# same /tmp/auto-speech/{socket, mpv.pid} state: each one's
# _kill_prior_session() tears down the other's just-spawned mpv, and
# the user hears audio cut off and restart, or hears nothing at all.
# Held only for the start sequence (~50-300 ms); playback itself is
# unaffected. fcntl.flock auto-releases on file close so a crashed
# holder cannot deadlock the lock.
_MPV_START_LOCK_PATH = Path("/tmp/auto-speech-mpv-start.lock")


class MpvNotInstalledError(RuntimeError):
    """Raised when the mpv binary is not on PATH."""


class MpvStartupError(RuntimeError):
    """Raised when the mpv socket does not become responsive in time."""


class MpvController:
    """Own the singleton mpv playback session."""

    _STARTUP_DEADLINE_SECONDS = 2.0
    _STARTUP_POLL_SECONDS = 0.05
    _TERMINATE_GRACE_SECONDS = 1.0

    def start(self, wav_path: Path) -> None:
        mpv = shutil.which("mpv")
        if not mpv:
            raise MpvNotInstalledError(
                "mpv not found on PATH. Install with: brew install mpv"
            )
        if not wav_path.is_file():
            raise FileNotFoundError(f"WAV missing: {wav_path}")

        # Acquire the start lock — held only for the brief kill/spawn/await
        # sequence so concurrent callers serialise. See _MPV_START_LOCK_PATH.
        with open(_MPV_START_LOCK_PATH, "w") as _lock:
            fcntl.flock(_lock.fileno(), fcntl.LOCK_EX)

            # Invariant I-12.1: kill any prior session first.
            self._kill_prior_session()

            socket_path = SessionDir.socket_path()
            SessionDir.root().mkdir(parents=True, exist_ok=True)
            # Ensure the old socket file is gone so mpv can create its own.
            try:
                socket_path.unlink()
            except FileNotFoundError:
                pass

            print(
                f"[mpv] starting  wav={wav_path}  socket={socket_path}",
                file=sys.stderr,
            )
            proc = subprocess.Popen(
                [
                    mpv,
                    "--no-video",
                    "--really-quiet",
                    f"--input-ipc-server={socket_path}",
                    str(wav_path),
                ],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                close_fds=True,
            )
            started_at = (
                datetime.now(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            )
            SessionDir.write(proc.pid, wav_path, started_at)

            self._await_socket_ready(socket_path, proc)
            print(
                f"[mpv] started   pid={proc.pid}  started_at={started_at}",
                file=sys.stderr,
            )
            # Lock auto-releases on file close at end of with-block.

    def stop(self) -> bool:
        """Send quit to the active session. Returns True if a session existed."""
        if not SessionDir.is_mpv_running():
            SessionDir.clear()
            return False
        try:
            MpvIpc.send(["quit"], SessionDir.socket_path())
        except MpvIpcError as exc:
            print(f"[mpv] stop: IPC failed: {exc}", file=sys.stderr)
            # Fall through to signal-based kill.
        self._kill_prior_session()
        return True

    def _kill_prior_session(self) -> None:
        pid = SessionDir.read_pid()
        if pid is None:
            SessionDir.clear()
            return
        if not SessionDir.is_mpv_running():
            SessionDir.clear()
            return
        print(f"[mpv] killing prior session pid={pid}", file=sys.stderr)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            SessionDir.clear()
            return

        deadline = time.monotonic() + self._TERMINATE_GRACE_SECONDS
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)  # existence check
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        SessionDir.clear()

    def _await_socket_ready(
        self, socket_path: Path, proc: subprocess.Popen
    ) -> None:
        deadline = time.monotonic() + self._STARTUP_DEADLINE_SECONDS
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise MpvStartupError(
                    f"mpv exited early with code {proc.returncode} before socket was ready"
                )
            if socket_path.exists():
                try:
                    MpvIpc.send(["get_property", "pid"], socket_path)
                    return
                except MpvIpcError:
                    pass
            time.sleep(self._STARTUP_POLL_SECONDS)
        raise MpvStartupError(
            f"mpv socket did not become ready within {self._STARTUP_DEADLINE_SECONDS}s"
        )
