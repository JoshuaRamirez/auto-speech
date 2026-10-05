"""UNIX domain socket IPC server for AutoSpeech daemon.

Provides DualWireRequestHandler supporting:
  - Wire 1: Line-delimited structured JSON (Sublimated protocol with 'action' discriminator)
  - Wire 2: Raw UTF-8 stream (Legacy protocol, terminated by EOF/SHUT_WR or newline)
"""

from __future__ import annotations

import json
import socket
import socketserver
from pathlib import Path
from typing import Any, Callable, Optional


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
                            parsed = json.loads(decoded)
                            if (
                                isinstance(parsed, dict)
                                and "action" in parsed
                                and isinstance(parsed["action"], str)
                                and parsed["action"] in ("speak", "play_cache", "interrupt", "status")
                            ):
                                is_wire1 = True
                                payload = parsed
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
                    parsed = json.loads(decoded)
                    if (
                        isinstance(parsed, dict)
                        and "action" in parsed
                        and isinstance(parsed["action"], str)
                        and parsed["action"] in ("speak", "play_cache", "interrupt", "status")
                    ):
                        is_wire1 = True
                        payload = parsed
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

    def server_bind(self) -> None:
        try:
            path = Path(self.server_address)
            if path.exists() or path.is_symlink():
                path.unlink(missing_ok=True)
        except OSError:
            pass
        super().server_bind()
