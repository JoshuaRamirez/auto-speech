"""In-process, zero-subprocess client for the AutoSpeech daemon.

Provides the canonical Python interface for callers (MCP server, autoplay worker,
CLI clients, replay control) communicating with the daemon UNIX domain socket
per RFC §4.3.
"""

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any

from config_constants import DEFAULT_SPEED, DEFAULT_VOICE_ID

DEFAULT_SOCKET_PATH = Path("/tmp/auto-speech-daemon.sock")


class Priority(IntEnum):
    """4-tier in-memory priority hierarchy per RFC §3.4."""

    USER_INTERRUPT = 1
    EXPLICIT_MCP = 2
    AUTOPLAY = 3
    TOOL_NARRATION = 4


@dataclass(frozen=True)
class DaemonResponse:
    """Structured response from the AutoSpeech daemon."""

    status: str
    action: str
    cache_hit: bool = False
    message: str = ""
    error_code: str | None = None
    queue_depth: int = 0
    raw: dict[str, Any] | None = None


class DaemonClient:
    """High-performance, zero-subprocess client for the AutoSpeech daemon."""

    def __init__(
        self,
        socket_path: Path | str | None = None,
        timeout: float = 5.0,
        max_retries: int = 2,
    ) -> None:
        if socket_path is not None:
            self._socket_path = Path(socket_path)
        else:
            env_override = os.environ.get("AUTO_SPEECH_DAEMON_SOCK")
            self._socket_path = Path(env_override) if env_override else DEFAULT_SOCKET_PATH
        self._timeout = timeout
        self._max_retries = max_retries

    @property
    def socket_path(self) -> Path:
        """Resolved path to the daemon UNIX domain socket."""
        return self._socket_path

    def is_alive(self) -> bool:
        """Checks if daemon socket exists and accepts connections."""
        if not self._socket_path.is_socket():
            return False
        try:
            resp = self.status()
            return resp.status == "ok"
        except Exception:  # noqa: BLE001 — any probe failure means the daemon is down
            return False

    def speak(
        self,
        text: str,
        *,
        priority: Priority = Priority.EXPLICIT_MCP,
        source_hash: str | None = None,
        session_id: str | None = None,
        voice_id: str = DEFAULT_VOICE_ID,
        speed: float = DEFAULT_SPEED,
    ) -> DaemonResponse:
        """Submits speech request to daemon priority queue (defaults mirror config_constants.py)."""
        payload: dict[str, Any] = {
            "action": "speak",
            "text": text,
            "priority": int(priority),
            "source_hash": source_hash,
            "session_id": session_id,
            "voice_id": voice_id,
            "speed": speed,
        }
        return self._send_request(payload)

    def play_cache(
        self,
        source_hash: str,
        *,
        priority: Priority = Priority.EXPLICIT_MCP,
        session_id: str | None = None,
    ) -> DaemonResponse:
        """Plays pre-cached audio immediately by source hash."""
        payload: dict[str, Any] = {
            "action": "play_cache",
            "source_hash": source_hash,
            "priority": int(priority),
            "session_id": session_id,
        }
        return self._send_request(payload)

    def interrupt(self, session_id: str | None = None) -> DaemonResponse:
        """Halts active audio playback immediately and clears preemptible items."""
        payload: dict[str, Any] = {
            "action": "interrupt",
            "session_id": session_id,
        }
        return self._send_request(payload)

    def status(self) -> DaemonResponse:
        """Retrieves daemon health, queue depths, and current playback state."""
        return self._send_request({"action": "status"})

    def _send_request(self, payload: dict[str, Any]) -> DaemonResponse:
        line = json.dumps(payload).encode("utf-8") + b"\n"
        last_err: Exception | None = None

        for attempt in range(self._max_retries + 1):
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(self._timeout)
            try:
                sock.connect(str(self._socket_path))
                sock.sendall(line)

                # Receive framed JSON response line
                resp_data = b""
                while b"\n" not in resp_data:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    resp_data += chunk

                resp_line, _, _ = resp_data.partition(b"\n")
                if not resp_line:
                    raise ConnectionResetError("Empty response received from daemon")
                data = json.loads(resp_line.decode("utf-8"))
                return DaemonResponse(
                    status=data.get("status", "ok"),
                    action=payload.get("action", ""),
                    cache_hit=data.get("cache_hit", False),
                    message=data.get("message", ""),
                    error_code=data.get("error_code"),
                    queue_depth=data.get("queue_depth", 0),
                    raw=data,
                )
            except Exception as e:  # noqa: BLE001 — any IPC failure is retried
                last_err = e
                if attempt < self._max_retries:
                    time.sleep(0.02 * (attempt + 1))
            finally:
                sock.close()

        raise ConnectionError(
            f"Failed to communicate with auto-speech daemon at {self._socket_path}: {last_err}"
        ) from last_err
