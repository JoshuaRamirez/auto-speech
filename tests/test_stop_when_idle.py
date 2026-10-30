"""`auto-speech stop` sets the narrator's stop even when nothing plays (#m1030-1421-30ba).

On 2026-10-30 the Desk Keeper found that with the narrator idle, `auto-speech
stop` printed "no active playback session", exited 2 and never sent the
narrator its interrupt, so the status stayed "stopped: no" and the next line
played. A stand-in narrator on a private socket records what it is sent.
"""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
import io
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import cli  # noqa: E402


class StandInNarrator:
    """Answers each request on a Unix socket as the narrator answers an interrupt."""

    def __init__(self, path: str) -> None:
        self.received: list[dict] = []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen(4)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                data = b""
                while b"\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                payload = json.loads(data.partition(b"\n")[0] or b"{}")
                self.received.append(payload)
                reply = {"status": "ok", "action": payload.get("action"), "stopped": True}
                conn.sendall(json.dumps(reply).encode() + b"\n")

    def close(self) -> None:
        self.server.close()


class StopWhenIdleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="as-stop-", dir="/tmp")
        self.sock = os.path.join(self.dir, "d.sock")
        self.narrator = StandInNarrator(self.sock)

    def tearDown(self) -> None:
        self.narrator.close()
        Path(self.sock).unlink(missing_ok=True)
        os.rmdir(self.dir)

    def test_idle_narrator_gets_the_stop_and_exit_is_zero(self) -> None:
        out = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"AUTO_SPEECH_DAEMON_SOCK": self.sock}),
            mock.patch("cli._has_active_session", return_value=False),
            mock.patch("cli._is_mpv_socket_alive", return_value=False),
            mock.patch("sys.stdout", out),
        ):
            rc = cli.main(["stop"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().strip(), "stopped")
        self.assertEqual([p.get("action") for p in self.narrator.received], ["interrupt"])
        self.assertEqual(self.narrator.received[0].get("source"), "auto-speech stop")

    def test_no_narrator_and_nothing_playing_still_says_so(self) -> None:
        err = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"AUTO_SPEECH_DAEMON_SOCK": os.path.join(self.dir, "none.sock")}),
            mock.patch("cli._has_active_session", return_value=False),
            mock.patch("sys.stderr", err),
        ):
            rc = cli.main(["stop"])
        self.assertEqual(rc, 2)
        self.assertIn("no active playback session", err.getvalue())
        self.assertEqual(self.narrator.received, [])


if __name__ == "__main__":
    unittest.main()
