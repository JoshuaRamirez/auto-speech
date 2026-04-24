"""SessionDir: paths and lifecycle helpers for /tmp/auto-speech/."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class SessionDir:
    """Well-known singleton location for the active mpv playback session.

    Layout:
      /tmp/auto-speech/control.sock   mpv IPC Unix socket
      /tmp/auto-speech/mpv.pid        decimal PID of the mpv process
      /tmp/auto-speech/wav.path       absolute path of the WAV being played
      /tmp/auto-speech/started_at     ISO-8601 UTC timestamp
    """

    _ROOT = Path("/tmp/auto-speech")

    @staticmethod
    def root() -> Path:
        return SessionDir._ROOT

    @staticmethod
    def socket_path() -> Path:
        return SessionDir._ROOT / "control.sock"

    @staticmethod
    def pid_path() -> Path:
        return SessionDir._ROOT / "mpv.pid"

    @staticmethod
    def wav_path_path() -> Path:
        return SessionDir._ROOT / "wav.path"

    @staticmethod
    def started_at_path() -> Path:
        return SessionDir._ROOT / "started_at"

    @staticmethod
    def clear() -> None:
        shutil.rmtree(SessionDir._ROOT, ignore_errors=True)

    @staticmethod
    def write(pid: int, wav_path: Path, started_at: str) -> None:
        SessionDir._ROOT.mkdir(parents=True, exist_ok=True)
        SessionDir.pid_path().write_text(f"{pid}\n", encoding="utf-8")
        SessionDir.wav_path_path().write_text(f"{wav_path}\n", encoding="utf-8")
        SessionDir.started_at_path().write_text(f"{started_at}\n", encoding="utf-8")

    @staticmethod
    def read_pid() -> int | None:
        path = SessionDir.pid_path()
        if not path.is_file():
            return None
        try:
            return int(path.read_text(encoding="utf-8").strip())
        except ValueError:
            return None

    @staticmethod
    def is_mpv_running() -> bool:
        """Best-effort: PID exists AND its command name is 'mpv'."""
        pid = SessionDir.read_pid()
        if pid is None:
            return False
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm="],
                capture_output=True,
                text=True,
                check=False,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return False
        if result.returncode != 0:
            return False
        return "mpv" in result.stdout.strip().lower()
