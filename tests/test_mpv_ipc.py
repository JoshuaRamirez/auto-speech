"""Unit tests for mpv_ipc.MpvIpc.send against a fake mpv socket: every
failure, a slow reply included, is an MpvIpcError. Run directly."""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
import json
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from mpv_ipc import MpvIpc, MpvIpcError  # noqa: E402


class FakeMpv:
    """Accepts connections and answers each request line with `reply(req)`
    after `delay` seconds; None means never answer."""

    def __init__(self, path: Path, reply, delay: float = 0.0) -> None:
        self.path, self.reply, self.delay = path, reply, delay
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(str(path))
        self.srv.listen(4)
        self.conns: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            self.conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        try:
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
            req = json.loads(buf.split(b"\n", 1)[0])
            out = self.reply(req)
            if out is None:
                time.sleep(5)
                return
            time.sleep(self.delay)
            conn.sendall(out)
        except OSError:
            return

    def close(self) -> None:
        self.srv.close()
        for c in self.conns:
            c.close()


class TestMpvIpc(unittest.TestCase):
    def setUp(self) -> None:
        # /tmp: a Unix socket path on macOS must fit in 104 bytes.
        self.dir = Path(tempfile.mkdtemp(prefix="as-mpv-", dir="/tmp"))
        self.sock = self.dir / "mpv.sock"
        self.fake: FakeMpv | None = None

    def tearDown(self) -> None:
        if self.fake is not None:
            self.fake.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def serve(self, reply, delay: float = 0.0) -> None:
        self.fake = FakeMpv(self.sock, reply, delay)

    def test_a_reply_after_an_event_is_returned(self):
        self.serve(lambda r: b'{"event":"playback-restart"}\n{"data":1.5,"request_id":1,"error":"success"}\n')
        self.assertEqual(MpvIpc.send(["get_property", "time-pos"], self.sock)["data"], 1.5)

    def test_a_slow_mpv_is_an_mpv_ipc_error_not_a_bare_timeout(self):
        # 2026-10-21 06:14:50Z: a TimeoutError from here ended a live line.
        self.serve(lambda r: None)
        t0 = time.monotonic()
        with self.assertRaises(MpvIpcError) as cm:
            MpvIpc.send(["get_property", "time-pos"], self.sock, timeout=0.2)
        self.assertIn("did not reply", str(cm.exception))
        self.assertLess(time.monotonic() - t0, 1.0)

    def test_the_default_timeout_still_applies(self):
        self.serve(lambda r: b'{"data":2,"request_id":1}\n', delay=0.1)
        self.assertEqual(MpvIpc.send(["get_property", "time-pos"], self.sock)["data"], 2)

    def test_no_socket_closed_socket_and_bad_bytes_are_mpv_ipc_errors(self):
        with self.assertRaises(MpvIpcError):
            MpvIpc.send(["stop"], self.sock)
        self.serve(lambda r: b"")
        with self.assertRaises(MpvIpcError):
            MpvIpc.send(["stop"], self.sock, timeout=0.5)

    def test_undecodable_bytes_are_an_mpv_ipc_error(self):
        self.serve(lambda r: b"\xff\xfe\n")
        with self.assertRaises(MpvIpcError):
            MpvIpc.send(["stop"], self.sock, timeout=0.5)


if __name__ == "__main__":
    unittest.main(verbosity=1)
