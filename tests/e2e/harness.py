"""Test harness, environment isolation, and test doubles for auto-speech E2E tests."""

from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PYTHON = PROJECT_ROOT / "plugin" / "scripts" / "python"
PLUGIN_SHELL = PROJECT_ROOT / "plugin" / "scripts" / "shell"
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
if not VENV_PYTHON.exists():
    VENV_PYTHON = Path(sys.executable)


def create_dummy_wav(path: Path, duration_s: float = 0.1, sample_rate: int = 24000) -> Path:
    """Generate a valid minimal mono 16-bit PCM WAV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    num_samples = int(duration_s * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        # Write silent PCM 16 frames
        raw_data = struct.pack(f"<{num_samples}h", *(0 for _ in range(num_samples)))
        wf.writeframes(raw_data)
    return path


class SpyMpv:
    """Creates a mock/spy mpv binary in a temporary directory to intercept calls.

    Records arguments, flags, timestamps, and simulates execution without playing sound.
    """

    def __init__(self, delay_s: float = 0.05, exit_code: int = 0) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="auto_speech_spy_mpv_")
        self.bin_path = Path(self.temp_dir) / "mpv"
        self.log_file = Path(self.temp_dir) / "invocations.jsonl"
        self.active_pids_dir = Path(self.temp_dir) / "pids"
        self.active_pids_dir.mkdir(parents=True, exist_ok=True)
        self.delay_s = delay_s
        self.exit_code = exit_code

        self._create_executable()

    def _create_executable(self) -> None:
        script = f"""#!/usr/bin/env python3
import sys, os, time, json

log_file = "{self.log_file}"
pid = os.getpid()
pid_file = os.path.join("{self.active_pids_dir}", str(pid))

# Record active PID
with open(pid_file, "w") as f:
    f.write(str(pid))

record = {{
    "pid": pid,
    "timestamp": time.time(),
    "argv": sys.argv[1:],
    "cwd": os.getcwd()
}}

with open(log_file, "a") as f:
    f.write(json.dumps(record) + "\\n")

try:
    time.sleep({self.delay_s})
finally:
    try:
        os.remove(pid_file)
    except OSError:
        pass

sys.exit({self.exit_code})
"""
        self.bin_path.write_text(script, encoding="utf-8")
        self.bin_path.chmod(0o755)

    def get_invocations(self) -> list[dict[str, Any]]:
        """Return all logged mpv invocations."""
        if not self.log_file.exists():
            return []
        invocations = []
        for line in self.log_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    invocations.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return invocations

    def get_active_pids(self) -> list[int]:
        """Return list of active spy mpv PIDs currently running."""
        pids = []
        for f in self.active_pids_dir.glob("*"):
            try:
                pids.append(int(f.name))
            except ValueError:
                pass
        return pids

    def cleanup(self) -> None:
        try:
            shutil.rmtree(self.temp_dir)
        except OSError:
            pass


class IsolatedEnvironment:
    """Sandbox environment isolating filesystem paths, sockets, and PATH."""

    def __init__(self, use_spy_mpv: bool = True, mpv_delay_s: float = 0.05) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="auto_speech_e2e_")
        self.root = Path(self.temp_dir)

        self.socket_path = self.root / "auto-speech-daemon.sock"
        self.pid_path = self.root / "auto-speech-narrator-daemon.pid"
        self.log_path = self.root / "auto-speech-narrator-daemon.log"
        self.events_path = self.root / "auto-speech-narrator-events.jsonl"
        self.depth_path = self.root / "auto-speech-narration-depth"
        self.watermark_path = self.root / "auto-speech-narrator-daemon.watermark"

        self.spy_mpv = SpyMpv(delay_s=mpv_delay_s) if use_spy_mpv else None

        self._orig_env = os.environ.copy()
        self.env = os.environ.copy()
        self.env["AUTO_SPEECH_DAEMON_SOCK"] = str(self.socket_path)
        self.env["AUTO_SPEECH_NARRATOR_PID"] = str(self.pid_path)
        self.env["AUTO_SPEECH_NARRATOR_EVENTS"] = str(self.events_path)
        self.env["AUTO_SPEECH_TMP_ROOT"] = str(self.root)
        self.env["PYTHONPATH"] = f"{PLUGIN_PYTHON}:{os.environ.get('PYTHONPATH', '')}"

        if self.spy_mpv:
            self.env["PATH"] = f"{self.spy_mpv.temp_dir}:{os.environ.get('PATH', '')}"

    def write_event(self, event_type: str = "ToolExecution", data: dict | None = None) -> None:
        """Append a JSONL event line to the events file."""
        if data is None:
            data = {"tool": "Bash", "command": "echo test"}
        entry = {"type": event_type, "timestamp": time.time(), "data": data}
        with open(self.events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def cleanup(self) -> None:
        if self.spy_mpv:
            self.spy_mpv.cleanup()
        try:
            shutil.rmtree(self.temp_dir)
        except OSError:
            pass


class UnixSocketClient:
    """Helper client to communicate with UNIX domain sockets."""

    @staticmethod
    def send_text(
        socket_path: Path | str,
        text: str,
        timeout: float = 2.0,
        shutdown_write: bool = True,
    ) -> str:
        """Connects to socket, transmits UTF-8 text, and reads any response."""
        sock_path_str = str(socket_path)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(sock_path_str)
            sock.sendall(text.encode("utf-8"))
            if shutdown_write:
                sock.shutdown(socket.SHUT_WR)
            response_chunks = []
            while True:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    response_chunks.append(chunk)
                except socket.timeout:
                    break
            return b"".join(response_chunks).decode("utf-8", errors="replace")
        finally:
            sock.close()

    @staticmethod
    def is_alive(socket_path: Path | str, timeout: float = 0.5) -> bool:
        """Test if a UNIX socket is accepting connections."""
        sock_path_str = str(socket_path)
        if not os.path.exists(sock_path_str):
            return False
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(sock_path_str)
            return True
        except (socket.error, OSError):
            return False
        finally:
            sock.close()


def run_speak_cli(
    text: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 2.5,
) -> subprocess.CompletedProcess[str]:
    """Execute speak.py with given text piped to stdin."""
    speak_script = PLUGIN_PYTHON / "speak.py"
    cmd = [str(VENV_PYTHON), str(speak_script)]
    if args:
        cmd.extend(args)

    try:
        return subprocess.run(
            cmd,
            input=text,
            text=True,
            capture_output=True,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout_str = (
            exc.stdout.decode("utf-8", errors="replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")
        )
        stderr_str = (
            exc.stderr.decode("utf-8", errors="replace")
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or "")
        )
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=124,
            stdout=stdout_str,
            stderr=stderr_str + f"\nTimeoutExpired: command timed out after {timeout}s",
        )
