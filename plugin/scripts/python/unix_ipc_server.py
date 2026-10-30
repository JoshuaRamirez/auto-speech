"""UNIX domain socket IPC server for AutoSpeech daemon.

Provides DualWireRequestHandler supporting:
  - Wire 1: Line-delimited structured JSON (Sublimated protocol with 'action' discriminator;
    a 'command' key naming the same control is read the same way, so a probe such as
    {"command":"status"} is answered, never spoken — the Desk, 2026-10-18 17:52)
  - Wire 2: Raw UTF-8 stream (Legacy protocol, terminated by EOF/SHUT_WR or newline)
"""

from __future__ import annotations

import errno
import json
import os
import socket
import socketserver
from pathlib import Path
from typing import Any, Callable, Optional

# The controls the daemon answers on Wire 1. A JSON object names one under
# "action" (the protocol key) or "command" (what a hand-typed probe tends to
# use); either is a control request. Anything else that starts with "{" is
# Wire 2 text and gets spoken, which is what happened on 2026-10-18 17:51.
CONTROLS: tuple[str, ...] = ("speak", "play_cache", "play_history", "interrupt", "resume", "missed_add", "status", "queue")
CONTROL_KEYS: tuple[str, ...] = ("action", "command")


def control_payload(parsed: Any) -> Optional[dict[str, Any]]:
    """The Wire 1 payload for `parsed`, or None when it is not a control request.

    `parsed` is a control request when it is a dict whose "action" or
    "command" value is one of CONTROLS. The result always carries the
    control under "action" (a copy, so the caller's dict is untouched);
    "action" wins when both keys are present and valid.
    """
    if not isinstance(parsed, dict):
        return None
    for key in CONTROL_KEYS:
        value = parsed.get(key)
        if isinstance(value, str) and value in CONTROLS:
            payload = dict(parsed)
            payload["action"] = value
            return payload
    return None


class DualWireRequestHandler(socketserver.BaseRequestHandler):
    """Hardened Dual-Wire Protocol Adapter supporting Wire 1 JSON and Wire 2 legacy text."""

    server: _DaemonSocketServer

    def handle(self) -> None:
        try:
            self.request.settimeout(5.0)
        except OSError:
            pass

        buffer = b""
        aborted = False

        while True:
            try:
                chunk = self.request.recv(4096)
                if not chunk:
                    # Connection closed by client (EOF or SHUT_WR)
                    break
                buffer += chunk
            except (ConnectionResetError, BrokenPipeError, TimeoutError, OSError):
                aborted = True
                break

            # Process framed Wire 1 JSON requests line-by-line if present
            while buffer:
                # Strip leading whitespace/newlines between framed messages
                lstripped = buffer.lstrip(b"\r\n \t")
                if not lstripped:
                    buffer = b""
                    break
                buffer = lstripped

                # Candidate Wire 1 frame (starts with '{')
                if buffer.startswith(b"{"):
                    if b"\n" in buffer:
                        line, _, remainder = buffer.partition(b"\n")
                        is_wire1 = False
                        payload = None
                        try:
                            decoded = line.decode("utf-8").strip()
                            payload = control_payload(json.loads(decoded))
                            is_wire1 = payload is not None
                        except Exception:
                            is_wire1 = False

                        if is_wire1 and payload is not None:
                            dispatcher = getattr(self.server, "dispatcher", None)
                            if dispatcher and hasattr(dispatcher, "dispatch_json"):
                                try:
                                    resp = dispatcher.dispatch_json(payload)
                                except Exception as e:
                                    resp = {
                                        "status": "error",
                                        "error_code": "DISPATCH_FAILED",
                                        "message": str(e),
                                    }
                            else:
                                resp = {"status": "ok", "action": payload.get("action")}
                            try:
                                self.request.sendall(json.dumps(resp).encode("utf-8") + b"\n")
                            except OSError:
                                pass
                            buffer = remainder
                            continue
                        else:
                            # Starts with '{', but not Wire 1 JSON with valid action!
                            # Could be user spoken text starting with '{', e.g. code snippet or dict.
                            # Break inner loop to accumulate full stream until EOF / SHUT_WR.
                            break
                    else:
                        # Incomplete JSON candidate line; wait for more data in recv
                        break
                else:
                    # Does not start with '{'. Accumulate until connection EOF / SHUT_WR.
                    break

        if aborted:
            return

        # Handle remaining buffer upon EOF / SHUT_WR
        if buffer:
            # Check if residual buffer is a Wire 1 JSON payload (even if not newline-terminated)
            is_wire1 = False
            payload = None
            if buffer.lstrip().startswith(b"{"):
                try:
                    decoded = buffer.decode("utf-8").strip()
                    payload = control_payload(json.loads(decoded))
                    is_wire1 = payload is not None
                except Exception:
                    is_wire1 = False

            if is_wire1 and payload is not None:
                dispatcher = getattr(self.server, "dispatcher", None)
                if dispatcher and hasattr(dispatcher, "dispatch_json"):
                    try:
                        resp = dispatcher.dispatch_json(payload)
                    except Exception as e:
                        resp = {"status": "error", "error_code": "DISPATCH_FAILED", "message": str(e)}
                else:
                    resp = {"status": "ok", "action": payload.get("action")}
                try:
                    self.request.sendall(json.dumps(resp).encode("utf-8") + b"\n")
                except OSError:
                    pass
            else:
                # Wire 2: Legacy Raw UTF-8 text
                try:
                    raw_text = buffer.decode("utf-8", errors="replace")
                except Exception:
                    raw_text = ""

                cleaned = raw_text.strip()
                if cleaned:
                    dispatcher = getattr(self.server, "dispatcher", None)
                    if dispatcher and hasattr(dispatcher, "dispatch_legacy_text"):
                        dispatcher.dispatch_legacy_text(cleaned)
                    elif hasattr(self.server, "on_text") and self.server.on_text is not None:
                        self.server.on_text(cleaned)

                try:
                    self.request.sendall(b"OK\n")
                except (ConnectionResetError, BrokenPipeError, OSError):
                    pass


# Maintain legacy alias so existing imports of _DaemonRequestHandler continue working
_DaemonRequestHandler = DualWireRequestHandler


class _DaemonSocketServer(socketserver.ThreadingUnixStreamServer):
    """Multi-threaded UNIX domain stream socket server for daemon IPC."""

    address_family = socket.AF_UNIX
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 512

    def __init__(
        self,
        server_address: str | Path,
        on_text: Optional[Callable[[str], None]] = None,
        dispatcher: Optional[Any] = None,
    ) -> None:
        self.on_text = on_text
        self.dispatcher = dispatcher
        super().__init__(str(server_address), DualWireRequestHandler)

    # (st_dev, st_ino) of the socket file this server bound: the one file it
    # may remove (#m1030-1044-b20b). None until bound.
    bound_file: Optional[tuple[int, int]] = None

    def server_bind(self) -> None:
        path = Path(self.server_address)
        if socket_answers(path):
            # A live server owns this path. Taking it would orphan that
            # server: the file must stay, and this bind fails.
            raise OSError(errno.EADDRINUSE, f"a live server already answers at {path}")
        try:
            if path.exists() or path.is_symlink():
                path.unlink(missing_ok=True)
        except OSError:
            pass
        super().server_bind()
        st = os.lstat(path)
        self.bound_file = (st.st_dev, st.st_ino)


def socket_answers(path: str | Path, timeout: float = 0.5) -> bool:
    """True when something accepts a connection on the UNIX socket at `path`:
    a live server, whose file must never be removed."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        s.close()


def unlink_if_bound(path: str | Path, bound_file: Optional[tuple[int, int]]) -> bool:
    """Remove the socket file at `path` only when it is still the file this
    process bound (`bound_file`, from _DaemonSocketServer.bound_file). A file
    another server put there since is left alone. True when removed."""
    if bound_file is None:
        return False
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if (st.st_dev, st.st_ino) != bound_file:
        return False
    try:
        os.unlink(path)
    except OSError:
        return False
    return True
