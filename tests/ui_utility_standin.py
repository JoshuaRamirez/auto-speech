"""A stand-in for the UI Utility, for tests: a Unix socket server that
speaks the contract's line format, records every request and answers from
a script. Never the real app (specs/001-player-launch, clarify Q4: a
silent copy started by a test would stay as his copy). Not a test file."""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
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
        return {"ok": True, "contract": 1, "pieces": ["player", "control", "settings"]}
    if cmd == "open":
        return {"ok": True, "id": request.get("id") or "player-1"}
    return {"ok": True}


def rev_1_2_script(request: dict[str, Any]) -> Optional[dict[str, Any]]:
    """A copy from before the settings piece (contract rev 1.2, release 0.3.0)."""
    if request.get("cmd") == "hello":
        return {"ok": True, "contract": 1, "pieces": ["player", "control"]}
    if request.get("cmd") == "open" and request.get("panel") == "settings":
        return {"ok": False, "error": "bad-value", "detail": "panel must be one of player, control"}
    return default_script(request)


def _fits(item: dict[str, Any], value: Any) -> bool:
    """Whether a value fits a settings item (contract rev 1.3, "values")."""
    kind = item.get("kind")
    if kind == "switch":
        return isinstance(value, bool)
    if kind == "choice":
        return value in [c.get("id") for c in item.get("choices") or []]
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        lo, hi = item.get("min"), item.get("max")
        return (lo is None or value >= lo) and (hi is None or value <= hi)
    if kind == "text":
        return isinstance(value, str)
    return kind == "action" and value is None


class SettingsPieces:
    """The settings pieces a stand-in holds (contract rev 1.3): the groups
    opened, the values shown, a `values` that does not fit refused with
    bad-value, `status` carrying the values. Ids are one space across pieces."""

    def __init__(self) -> None:
        self.pieces: dict[str, dict[str, Any]] = {}

    def items(self, piece_id: str) -> dict[str, dict[str, Any]]:
        groups = self.pieces[piece_id]["groups"]
        return {i["id"]: i for g in groups for i in g.get("items", [])}

    def values(self, piece_id: str) -> dict[str, Any]:
        return {k: i.get("value") for k, i in self.items(piece_id).items()}

    def __call__(self, request: dict[str, Any], fallback: Script = default_script) -> Optional[dict[str, Any]]:
        cmd, piece_id = request.get("cmd"), request.get("id")
        if cmd == "open" and request.get("panel") == "settings":
            for g in request.get("groups") or []:
                for i in g.get("items") or []:
                    if i.get("kind") != "action" and not _fits(i, i.get("value")):
                        return {"ok": False, "error": "bad-value", "detail": f"{i.get('id')}: {i.get('value')!r}"}
            self.pieces[piece_id] = {"groups": request.get("groups") or [], "source": request.get("source")}
            return {"ok": True, "id": piece_id}
        if piece_id in self.pieces:
            if cmd == "values":
                items = self.items(piece_id)
                for k, v in (request.get("values") or {}).items():
                    if k not in items or not _fits(items[k], v):
                        return {"ok": False, "error": "bad-value", "detail": f"{k}: {v!r}"}
                for k, v in request["values"].items():
                    items[k]["value"] = v
                return {"ok": True}
            if cmd == "close":
                del self.pieces[piece_id]
                return {"ok": True}
            if cmd == "status":
                return {"ok": True, "id": piece_id, "piece": "settings", "values": self.values(piece_id)}
        elif cmd == "open" and piece_id is not None:
            self.pieces.pop(piece_id, None)  # an open of another piece takes the id
        return fallback(request)


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
