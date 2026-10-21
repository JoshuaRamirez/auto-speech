"""A stand-in for the UI Utility, for tests: a Unix socket server that
speaks the contract's line format, records every request and answers from
a script. Never the real app (specs/001-player-launch, clarify Q4: a
silent copy started by a test would stay as his copy). Not a test file."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

Script = Callable[[dict[str, Any]], Optional[dict[str, Any]]]


def default_script(request: dict[str, Any]) -> Optional[dict[str, Any]]:
    cmd = request.get("cmd")
    if cmd == "hello":
        return {"ok": True, "contract": 1, "pieces": ["player"]}
    if cmd == "open":
        return {"ok": True, "id": request.get("id") or "player-1"}
    return {"ok": True}


def never(request: dict[str, Any]) -> None:
    return None


class StandIn:
    def __init__(self, path: Path | str, *, script: Script | None = None) -> None:
        self.path = Path(path)
        self.script = script or default_script
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._conns: list[socket.socket] = []
        self._server: Optional[socket.socket] = None
        self.connections = 0

    def start(self) -> "StandIn":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(self.path))
        srv.listen(8)
        self._server = srv
        threading.Thread(target=self._accept, args=(srv,), daemon=True).start()
        return self

    def _accept(self, srv: socket.socket) -> None:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with self._lock:
                self._conns.append(conn)
                self.connections += 1
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        buf = b""
        try:
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    request = json.loads(raw)
                    with self._lock:
                        self.requests.append(request)
                    reply = self.script(request)
                    if reply is None:
                        continue
                    if "req" in request:
                        reply = {**reply, "req": request["req"]}
                    conn.sendall((json.dumps(reply) + "\n").encode())
        except OSError:
            return
        finally:
            with self._lock:
                if conn in self._conns:
                    self._conns.remove(conn)
            conn.close()

    def send_event(self, obj: dict[str, Any]) -> None:
        with self._lock:
            conns = list(self._conns)
        for c in conns:
            try:
                c.sendall((json.dumps(obj) + "\n").encode())
            except OSError:
                pass

    def drop_connections(self) -> None:
        with self._lock:
            conns, self._conns = list(self._conns), []
        for c in conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            c.close()

    def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        self.drop_connections()
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def cmds(self) -> list[str]:
        with self._lock:
            return [r.get("cmd") for r in self.requests]

    def wait_for(self, cmd: str, n: int = 1, timeout: float = 2.0) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                got = [r for r in self.requests if r.get("cmd") == cmd]
            if len(got) >= n or time.monotonic() >= deadline:
                return got
            time.sleep(0.01)
