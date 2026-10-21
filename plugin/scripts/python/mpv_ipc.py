"""MpvIpc: one-shot JSON-line send/receive over mpv's Unix socket."""

from __future__ import annotations

import json
import socket
from pathlib import Path


class MpvIpcError(RuntimeError):
    """Raised on any failure to connect, write, read, or parse a reply."""


class MpvIpc:
    """Open the socket, send one JSON line, read one JSON line, close."""

    _CONNECT_TIMEOUT_SECONDS = 2.0
    _READ_TIMEOUT_SECONDS = 5.0
    _READ_BUFFER = 65536

    @staticmethod
    def send(command: list | dict, socket_path: Path, *, timeout: float | None = None) -> dict:
        """Send `command` to mpv at `socket_path` and return the parsed reply.

        `timeout` bounds the send and the wait for the reply (default
        _READ_TIMEOUT_SECONDS). Every failure, a timeout included, is an
        MpvIpcError: a slow mpv under load is a failed read, never an
        exception that ends the line (the 2026-10-21 06:14:50Z line ended on
        a bare TimeoutError from here).

        Accepts:
          - a list   → wrapped into `{"command": <list>}`
          - a dict   → sent as-is

        Returns the first complete JSON reply line. mpv may also emit
        asynchronous "event" lines; this implementation reads until it
        sees one with a "request_id" or "error" field, consistent with
        mpv's command-reply protocol.
        """
        envelope: dict
        if isinstance(command, list):
            envelope = {"command": command}
        elif isinstance(command, dict):
            envelope = dict(command)
        else:
            raise MpvIpcError(f"unsupported command type: {type(command).__name__}")
        envelope.setdefault("request_id", 1)

        line = (json.dumps(envelope) + "\n").encode("utf-8")

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(MpvIpc._CONNECT_TIMEOUT_SECONDS)
            sock.connect(str(socket_path))
        except OSError as exc:  # FileNotFoundError, ConnectionRefusedError, a timeout
            sock.close()
            raise MpvIpcError(f"mpv socket unavailable at {socket_path}: {exc}") from exc

        read_timeout = MpvIpc._READ_TIMEOUT_SECONDS if timeout is None else timeout
        try:
            sock.settimeout(read_timeout)
            sock.sendall(line)
            buf = b""
            while True:
                chunk = sock.recv(MpvIpc._READ_BUFFER)
                if not chunk:
                    raise MpvIpcError("mpv closed the socket without a reply")
                buf += chunk
                # mpv emits one JSON object per line.
                while b"\n" in buf:
                    raw, _, buf = buf.partition(b"\n")
                    if not raw.strip():
                        continue
                    try:
                        obj = json.loads(raw.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        raise MpvIpcError(f"bad JSON from mpv: {raw!r} ({exc})") from exc
                    # Reply lines carry request_id or error; event lines do not.
                    if "request_id" in obj or "error" in obj:
                        return obj
                    # Else it's an async event; keep reading.
        except (socket.timeout, TimeoutError) as exc:
            raise MpvIpcError(f"mpv did not reply in {read_timeout:g}s at {socket_path}") from exc
        except OSError as exc:
            raise MpvIpcError(f"mpv socket error at {socket_path}: {exc}") from exc
        finally:
            try:
                sock.close()
            except OSError:
                pass
