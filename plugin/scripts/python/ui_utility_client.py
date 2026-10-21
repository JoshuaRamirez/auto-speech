"""A caller of the UI Utility's published contract, v1 (rev 1.1):
~/Developer/UI-Utility/docs/external/CONTRACT.md, read only.

JSON lines over a Unix stream socket, one object per line each way. Every
request carries `"v":1`, `"cmd"` and a `"req"` id; its reply echoes the id,
and events (which carry `"event"`) arrive interleaved with replies. A reader
thread reads every line, so the utility's position events never back up.

auto-speech uses only `hello` and `open` (specs/001-player-launch,
contracts/player-handover.md). `launch` starts the utility in the
background, never taking focus, and runs nothing at all when
AUTO_SPEECH_PLAYER_LAUNCH=never (the test suite sets it: no test starts
the real app). See docs/decisions/ADR-021.
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import socket
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

CONTRACT = 1
PANEL = "player"
SOCK_ENV = "UI_UTILITY_SOCK"
SILENT_ENV = "UI_UTILITY_SILENT"
LAUNCH_ENV = "AUTO_SPEECH_PLAYER_LAUNCH"
DEFAULT_SOCK = Path.home() / ".local" / "run" / "ui-utility.sock"
UTILITY_COMMAND = "ui-utility"
UTILITY_APP = "UI Utility"

KINDS = ("no socket", "refused", "timeout", "wrong contract", "closed")


def socket_path() -> Path:
    return Path(os.environ.get(SOCK_ENV) or DEFAULT_SOCK)


class ContractError(Exception):
    def __init__(self, kind: str, detail: str = "") -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown contract error kind: {kind!r}")
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail


class _Slot:
    def __init__(self) -> None:
        self.done = threading.Event()
        self.reply: Optional[dict[str, Any]] = None
        self.error: Optional[ContractError] = None


class UiUtilityClient:
    def __init__(
        self,
        path: Path | str | None = None,
        *,
        reply_timeout: float = 5.0,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if reply_timeout <= 0:
            raise ValueError("reply_timeout must be positive")
        self._path = Path(path) if path is not None else socket_path()
        self._reply_timeout = reply_timeout
        self._on_event = on_event
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()  # guards _pending and _closed
        self._send_lock = threading.Lock()
        self._pending: dict[str, _Slot] = {}
        self._closed = True
        self._ids = itertools.count(1)

    @property
    def connected(self) -> bool:
        with self._lock:
            return not self._closed

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(self._reply_timeout)
            sock.connect(str(self._path))
        except OSError as exc:  # FileNotFoundError, ConnectionRefusedError, a timeout…
            sock.close()
            raise ContractError("no socket", f"{self._path}: {exc.__class__.__name__}") from None
        with self._lock:
            self._sock = sock
            self._closed = False
        threading.Thread(target=self._read, args=(sock,), name="ui-utility-reader", daemon=True).start()

    def _read(self, sock: socket.socket) -> None:
        buf = b""
        try:
            while True:
                try:
                    chunk = sock.recv(65536)
                except (socket.timeout, TimeoutError):
                    if not self.connected:
                        return
                    continue
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    self._route(raw)
        except OSError:
            return
        finally:
            self._drop("the utility closed the connection", only=sock)

    def _route(self, raw: bytes) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        if "event" in msg:
            if self._on_event is not None:
                try:
                    self._on_event(msg)
                except Exception:  # noqa: BLE001 — a listener never stops the reader
                    pass
            return
        req = msg.get("req")
        with self._lock:
            slot = self._pending.pop(req, None) if isinstance(req, str) else None
        if slot is not None:
            slot.reply = msg
            slot.done.set()

    def _drop(self, why: str, *, only: Optional[socket.socket] = None) -> None:
        with self._lock:
            if only is not None and self._sock is not only:
                return  # a reader of an earlier connection ending late: not this one's business
            self._closed = True
            sock, self._sock = self._sock, None
            pending, self._pending = self._pending, {}
        for slot in pending.values():
            slot.error = ContractError("closed", why)
            slot.done.set()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def close(self) -> None:
        self._drop("closed by auto-speech")

    def request(self, obj: Mapping[str, Any]) -> dict[str, Any]:
        if "cmd" not in obj:
            raise ValueError("a request needs a cmd")
        req = f"as-{next(self._ids)}"
        slot = _Slot()
        with self._lock:
            if self._closed or self._sock is None:
                raise ContractError("closed", "not connected")
            sock = self._sock
            self._pending[req] = slot
        line = json.dumps({**obj, "v": CONTRACT, "req": req}, separators=(",", ":")) + "\n"
        try:
            with self._send_lock:
                sock.sendall(line.encode("utf-8"))
        except (socket.timeout, TimeoutError):
            self._drop("a send timed out")
            raise ContractError("timeout", f"{obj['cmd']}: the send did not finish in {self._reply_timeout:g} s") from None
        except OSError as exc:
            self._drop(f"send failed: {exc.__class__.__name__}")
            raise ContractError("closed", f"{obj['cmd']}: {exc.__class__.__name__}") from None
        if not slot.done.wait(self._reply_timeout):
            with self._lock:
                self._pending.pop(req, None)
            raise ContractError("timeout", f"{obj['cmd']}: no reply in {self._reply_timeout:g} s")
        if slot.error is not None:
            raise slot.error
        reply = slot.reply or {}
        if reply.get("ok") is not True:
            raise ContractError("refused", f"{reply.get('error') or 'error'}: {reply.get('detail') or ''}".rstrip())
        return reply

    def hello(self) -> dict[str, Any]:
        reply = self.request({"cmd": "hello"})
        pieces = reply.get("pieces")
        if reply.get("contract") != CONTRACT or not isinstance(pieces, list) or PANEL not in pieces:
            raise ContractError("wrong contract", f"contract {reply.get('contract')!r}, pieces {pieces!r}")
        return reply

    def open_player(
        self,
        *,
        panel_id: str,
        clip: Path | str,
        words: list[dict[str, Any]],
        title: str,
        play: bool = False,
    ) -> dict[str, Any]:
        return self.request({
            "cmd": "open",
            "panel": PANEL,
            "id": panel_id,
            "clip": {"path": str(clip)},
            "text": {"words": list(words)},
            "title": title,
            "play": bool(play),
        })


def launch_command(*, silent: bool, sock: str | None, which: Callable[[str], Optional[str]] = shutil.which) -> list[str]:
    """The contract's two launch forms, both in the background."""
    if which(UTILITY_COMMAND):
        return [UTILITY_COMMAND, "launch"] + (["--silent"] if silent else [])
    argv = ["open", "-g", "-a", UTILITY_APP]
    if silent:
        argv += ["--env", f"{SILENT_ENV}=1"]
    if sock:
        argv += ["--env", f"{SOCK_ENV}={sock}"]
    return argv


def launch(
    *,
    run: Callable[..., Any] = subprocess.run,
    env: Mapping[str, str] = os.environ,
    which: Callable[[str], Optional[str]] = shutil.which,
) -> list[str] | None:
    """Start the utility. Returns the argv run, or None when launching is off
    (AUTO_SPEECH_PLAYER_LAUNCH=never): then nothing runs at all."""
    if env.get(LAUNCH_ENV) == "never":
        return None
    argv = launch_command(silent=env.get(SILENT_ENV) == "1", sock=env.get(SOCK_ENV) or None, which=which)
    run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=False)
    return argv
