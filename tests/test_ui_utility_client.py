"""Unit tests for ui_utility_client: the UI Utility contract v1 caller, run
against tests/ui_utility_standin.py on a private socket. Never the real app,
never a launch. Run directly."""

from __future__ import annotations

import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[0] / "plugin" / "scripts" / "python"
for p in (str(SRC), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import ui_utility_client as uc  # noqa: E402
from ui_utility_client import ContractError, UiUtilityClient, launch, launch_command  # noqa: E402
from ui_utility_standin import StandIn, default_script, never  # noqa: E402

WORDS = [{"w": "Hello", "start": 0.0, "end": 0.4}, {"w": "there.", "start": 0.4, "end": 0.9}]


class _Sock(unittest.TestCase):
    def setUp(self) -> None:
        # /tmp, not $TMPDIR: a Unix socket path on macOS must fit in 104 bytes.
        self.dir = Path(tempfile.mkdtemp(prefix="as-uiu-", dir="/tmp"))
        self.sock = self.dir / "ui.sock"
        self.standin: StandIn | None = None
        self.client: UiUtilityClient | None = None

    def tearDown(self) -> None:
        if self.client is not None:
            self.client.close()
        if self.standin is not None:
            self.standin.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def serve(self, script=None) -> StandIn:
        if self.standin is not None:
            self.standin.stop()
        self.standin = StandIn(self.sock, script=script).start()
        return self.standin

    def connect(self, **kw) -> UiUtilityClient:
        if self.client is not None:
            self.client.close()
        self.client = UiUtilityClient(self.sock, **kw)
        self.client.connect()
        return self.client


class TestClient(_Sock):
    def test_hello_ok(self):
        self.serve()
        reply = self.connect().hello()
        self.assertEqual(reply["contract"], 1)
        [req] = self.standin.requests
        self.assertEqual((req["v"], req["cmd"]), (1, "hello"))
        self.assertTrue(req["req"].startswith("as-"))

    def test_another_contract_is_wrong(self):
        self.serve(lambda r: {"ok": True, "contract": 2, "pieces": ["player"]})
        with self.assertRaises(ContractError) as cm:
            self.connect().hello()
        self.assertEqual(cm.exception.kind, "wrong contract")
        self.serve(lambda r: {"ok": True, "contract": 1, "pieces": ["chart"]})
        with self.assertRaises(ContractError) as cm:
            self.connect().hello()
        self.assertEqual(cm.exception.kind, "wrong contract")

    def test_open_sends_exactly_the_contract_fields(self):
        self.serve()
        reply = self.connect().open_player(panel_id="auto-speech", clip=Path("/x/a.wav"), words=WORDS, title="Desk · 01:02:03")
        self.assertEqual(reply["id"], "auto-speech")
        [req] = self.standin.requests
        self.assertEqual(set(req), {"v", "cmd", "req", "panel", "id", "clip", "text", "title", "play"})
        self.assertEqual(req["panel"], "player")
        self.assertEqual(req["clip"], {"path": "/x/a.wav"})
        self.assertEqual(req["text"], {"words": WORDS})
        self.assertIs(req["play"], False)

    def test_a_refusal_carries_its_code_and_keeps_the_connection(self):
        def script(r):
            if r["cmd"] == "open":
                return {"ok": False, "error": "no-panel", "detail": "no such id"}
            return default_script(r)

        self.serve(script)
        c = self.connect()
        with self.assertRaises(ContractError) as cm:
            c.open_player(panel_id="x", clip="/a.wav", words=[], title="t")
        self.assertEqual(cm.exception.kind, "refused")
        self.assertEqual(cm.exception.detail, "no-panel: no such id")
        self.assertTrue(c.connected)
        c.hello()

    def test_no_reply_times_out(self):
        self.serve(never)
        c = self.connect(reply_timeout=0.3)
        t0 = time.monotonic()
        with self.assertRaises(ContractError) as cm:
            c.hello()
        self.assertEqual(cm.exception.kind, "timeout")
        self.assertLess(time.monotonic() - t0, 0.8)

    def test_an_event_between_request_and_reply_reaches_the_listener(self):
        gate = threading.Event()

        def script(r):
            self.standin.send_event({"event": "position", "id": "auto-speech", "t": 1.0})
            gate.wait(1.0)
            return default_script(r)

        events: list[dict] = []

        def on_event(evt):
            events.append(evt)
            gate.set()

        self.serve(script)
        reply = self.connect(on_event=on_event).hello()
        self.assertEqual(reply["contract"], 1)
        self.assertEqual([e["event"] for e in events], ["position"])

    def test_a_listener_that_raises_does_not_stop_the_reader(self):
        def script(r):
            self.standin.send_event({"event": "closed", "id": "auto-speech", "by": "user"})
            return default_script(r)

        def boom(evt):
            raise RuntimeError("listener broke")

        self.serve(script)
        c = self.connect(on_event=boom)
        c.hello()
        c.hello()
        self.assertTrue(c.connected)

    def test_a_dropped_connection_wakes_a_pending_request(self):
        def script(r):
            self.standin.drop_connections()
            return None

        self.serve(script)
        c = self.connect(reply_timeout=2.0)
        t0 = time.monotonic()
        with self.assertRaises(ContractError) as cm:
            c.hello()
        self.assertEqual(cm.exception.kind, "closed")
        self.assertLess(time.monotonic() - t0, 1.5)
        self.assertFalse(c.connected)
        with self.assertRaises(ContractError) as cm:
            c.hello()
        self.assertEqual(cm.exception.kind, "closed")

    def test_no_socket(self):
        with self.assertRaises(ContractError) as cm:
            self.connect()
        self.assertEqual(cm.exception.kind, "no socket")
        self.assertFalse(self.client.connected)

    def test_close_is_idempotent(self):
        self.serve()
        c = self.connect()
        c.close()
        c.close()
        self.assertFalse(c.connected)

    def test_socket_path_follows_the_env(self):
        import os

        saved = os.environ.get(uc.SOCK_ENV)
        try:
            os.environ[uc.SOCK_ENV] = "/tmp/x.sock"
            self.assertEqual(uc.socket_path(), Path("/tmp/x.sock"))
            del os.environ[uc.SOCK_ENV]
            self.assertEqual(uc.socket_path(), uc.DEFAULT_SOCK)
        finally:
            if saved is not None:
                os.environ[uc.SOCK_ENV] = saved


class TestLaunch(unittest.TestCase):
    def test_launch_command_forms(self):
        on_path = lambda name: "/usr/local/bin/ui-utility"  # noqa: E731
        off_path = lambda name: None  # noqa: E731
        self.assertEqual(launch_command(silent=False, sock=None, which=on_path), ["ui-utility", "launch"])
        self.assertEqual(launch_command(silent=True, sock="/s", which=on_path), ["ui-utility", "launch", "--silent"])
        self.assertEqual(launch_command(silent=False, sock=None, which=off_path), ["open", "-g", "-a", "UI Utility"])
        self.assertEqual(
            launch_command(silent=True, sock=None, which=off_path),
            ["open", "-g", "-a", "UI Utility", "--env", "UI_UTILITY_SILENT=1"],
        )
        self.assertEqual(
            launch_command(silent=True, sock="/tmp/u.sock", which=off_path),
            ["open", "-g", "-a", "UI Utility", "--env", "UI_UTILITY_SILENT=1", "--env", "UI_UTILITY_SOCK=/tmp/u.sock"],
        )
        self.assertEqual(
            launch_command(silent=False, sock="/tmp/u.sock", which=off_path),
            ["open", "-g", "-a", "UI Utility", "--env", "UI_UTILITY_SOCK=/tmp/u.sock"],
        )

    def test_never_runs_nothing(self):
        def run(*a, **kw):
            raise AssertionError("launch ran a command while AUTO_SPEECH_PLAYER_LAUNCH=never")

        self.assertIsNone(launch(run=run, env={"AUTO_SPEECH_PLAYER_LAUNCH": "never"}))

    def test_launch_runs_the_background_form(self):
        calls = []
        argv = launch(run=lambda a, **kw: calls.append((a, kw)), env={"UI_UTILITY_SILENT": "1"}, which=lambda n: None)
        self.assertEqual(argv, ["open", "-g", "-a", "UI Utility", "--env", "UI_UTILITY_SILENT=1"])
        [(a, kw)] = calls
        self.assertEqual(a, argv)
        self.assertEqual(kw["timeout"], 10)

    def test_the_suite_never_launches(self):
        import os

        # run_all.sh sets it; a run outside the suite is not this test's business.
        if os.environ.get("UI_UTILITY_SOCK"):
            self.assertEqual(os.environ.get("AUTO_SPEECH_PLAYER_LAUNCH"), "never")
            self.assertEqual(os.environ.get("UI_UTILITY_SILENT"), "1")


if __name__ == "__main__":
    unittest.main(verbosity=1)
