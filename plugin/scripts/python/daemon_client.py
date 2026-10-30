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
import uuid
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from state_paths import state_path
from typing import Any, Optional

from config_constants import DEFAULT_SPEED, DEFAULT_VOICE_ID

DEFAULT_SOCKET_PATH = state_path("auto-speech-daemon.sock")


class Priority(IntEnum):
    """4-tier in-memory priority hierarchy per RFC §3.4."""

    USER_INTERRUPT = 1
    EXPLICIT_MCP = 2
    AUTOPLAY = 3
    TOOL_NARRATION = 4


NARRATOR_TOO_OLD = "NARRATOR_TOO_OLD"  # play_history: the running narrator does not know it yet (T38)


@dataclass(frozen=True)
class DaemonResponse:
    """Structured response from the AutoSpeech daemon."""

    status: str
    action: str
    cache_hit: bool = False
    message: str = ""
    error_code: Optional[str] = None
    queue_depth: int = 0
    raw: Optional[dict[str, Any]] = None


class DaemonClient:
    """High-performance, zero-subprocess client for the AutoSpeech daemon."""

    def __init__(
        self,
        socket_path: Optional[Path | str] = None,
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
        except Exception:
            return False

    def ensure_daemon_running(self) -> bool:
        """Starts the narrator daemon if it is not already running.

        Returns True if the daemon is alive or successfully started.
        """
        if self.is_alive():
            return True
        start_script = Path(__file__).resolve().parents[1] / "shell" / "narrator_service_start.sh"
        if start_script.is_file():
            try:
                import subprocess

                subprocess.run(
                    ["bash", str(start_script)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                for _ in range(25):
                    if self._socket_path.is_socket():
                        try:
                            if self.is_alive():
                                return True
                        except Exception:
                            pass
                    time.sleep(0.04)
            except Exception:
                pass
        return self.is_alive()

    def speak(
        self,
        text: str,
        *,
        priority: Priority = Priority.EXPLICIT_MCP,
        source_hash: Optional[str] = None,
        session_id: Optional[str] = None,
        voice_id: str | None = DEFAULT_VOICE_ID,
        speed: float = DEFAULT_SPEED,
        missed_id: Optional[str] = None,
        hold: bool = True,
        origin_file: Optional[str] = None,
        line_id: Optional[str] = None,
        sender: Optional[str] = None,
    ) -> DaemonResponse:
        """Submits speech request to daemon priority queue (defaults mirror config_constants.py).

        `missed_id` names the missed-list line this replays (the daemon
        clears it once played); `hold=False` plays over other apps' sound;
        `origin_file` names the file the line came from (desk-say's pending
        file), kept in the history sidecar (ADR-020). `line_id` names the
        line: a speak whose id is queued, playing or done in the last ten
        minutes is acknowledged and not queued again (ADR-025). Without one,
        this call makes its own, so the retries below never queue a line
        twice; the reply's raw["line_id"] says which."""
        payload: dict[str, Any] = {
            "action": "speak",
            "text": text,
            "priority": int(priority),
            "source_hash": source_hash,
            "session_id": session_id,
            "voice_id": voice_id or DEFAULT_VOICE_ID,
            "speed": speed,
        }
        if missed_id:
            payload["missed_id"] = missed_id
        if not hold:
            payload["hold"] = False
        if origin_file:
            payload["origin_file"] = origin_file
        if sender:
            payload["sender"] = sender  # the desk named on a missed line played again
        payload["line_id"] = line_id or f"client-{uuid.uuid4().hex[:16]}"
        return self._send_request(payload)

    def play_cache(
        self,
        source_hash: str,
        *,
        priority: Priority = Priority.EXPLICIT_MCP,
        session_id: Optional[str] = None,
        missed_id: Optional[str] = None,
        hold: bool = True,
    ) -> DaemonResponse:
        """Plays pre-cached audio immediately by source hash."""
        payload: dict[str, Any] = {
            "action": "play_cache",
            "source_hash": source_hash,
            "priority": int(priority),
            "session_id": session_id,
        }
        if missed_id:
            payload["missed_id"] = missed_id
        if not hold:
            payload["hold"] = False
        return self._send_request(payload)

    def play_history(self, line_id: str) -> DaemonResponse:
        """Plays a speech history line again by its id, queued as the speech
        control's play-item queues it (`auto-speech replay`, T38).

        A narrator older than `play_history` does not know the action, and
        would take the request for words to speak once the connection
        closes; so the narrator's status is asked first, and the request is
        sent only when it says `replays_history`. Otherwise the answer is an
        error with error_code NARRATOR_TOO_OLD and nothing is sent."""
        st = self.status()
        if not (isinstance(st.raw, dict) and st.raw.get("replays_history") is True):
            return DaemonResponse(
                status="error",
                action="play_history",
                error_code=NARRATOR_TOO_OLD,
                message="the running narrator is older than replay from the speech history",
            )
        return self._send_request({"action": "play_history", "line_id": line_id})

    def interrupt(self, session_id: Optional[str] = None, source: Optional[str] = None) -> DaemonResponse:
        """Halts active audio playback immediately and clears preemptible items.
        `source` names the caller in the narrator's log."""
        payload: dict[str, Any] = {
            "action": "interrupt",
            "session_id": session_id,
        }
        if source:
            payload["source"] = source
        return self._send_request(payload)

    def resume(self) -> DaemonResponse:
        """Ends his stop (ADR-024), and only that: the lines it set aside stay
        on the missed list (#m1030-1241-6e0a)."""
        return self._send_request({"action": "resume"})

    def missed_add(
        self,
        text: str,
        *,
        source: Optional[str] = None,
        at: Optional[float] = None,
        line_id: Optional[str] = None,
        speech_line_id: Optional[str] = None,
        origin_file: Optional[str] = None,
        session_id: Optional[str] = None,
        sender: Optional[str] = None,
    ) -> DaemonResponse:
        """Puts a line held or cut elsewhere on the missed list, through the daemon.
        `line_id` is the missed-list entry's id (historic name); `speech_line_id`
        is the line's own speak id (ADR-025/026): a line already heard is not
        added, one already on the list is the same entry, and its replay
        carries the id so it plays once."""
        payload: dict[str, Any] = {"action": "missed_add", "text": text}
        if source:
            payload["source"] = source
        if at is not None:
            payload["at"] = at
        if line_id:
            payload["id"] = line_id
        if speech_line_id:
            payload["line_id"] = speech_line_id
        if origin_file:
            payload["origin_file"] = origin_file
        if session_id:
            payload["session_id"] = session_id  # the session that wrote the line: the narrator names its desk
        if sender:
            payload["sender"] = sender
        return self._send_request(payload)

    def queue(self) -> DaemonResponse:
        """What waits to be played, in the order it will play: raw["items"],
        each {"id", "text", "time", "source"} (the speech control's queue tab)."""
        return self._send_request({"action": "queue"})

    def status(self, line_id: Optional[str] = None) -> DaemonResponse:
        """Retrieves daemon health, queue depths, and current playback state;
        with `line_id`, raw["line"] says where that line is (ADR-025)."""
        payload: dict[str, Any] = {"action": "status"}
        if line_id:
            payload["line_id"] = line_id
        return self._send_request(payload)

    def _send_request(self, payload: dict[str, Any]) -> DaemonResponse:
        line = json.dumps(payload).encode("utf-8") + b"\n"
        last_err: Optional[Exception] = None

        ensured = False
        for attempt in range(self._max_retries + 1):
            if attempt == 0 and not self._socket_path.is_socket():
                self.ensure_daemon_running()
                ensured = True

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
            except Exception as e:
                last_err = e
                if not ensured and attempt == 0 and isinstance(e, (FileNotFoundError, ConnectionRefusedError)):
                    self.ensure_daemon_running()
                    ensured = True
                if attempt < self._max_retries:
                    time.sleep(0.02 * (attempt + 1))
            finally:
                sock.close()

        raise ConnectionError(
            f"Failed to communicate with auto-speech daemon at {self._socket_path}: {last_err}"
        ) from last_err
