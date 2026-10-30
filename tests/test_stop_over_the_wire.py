"""His stop, over the wire, reaches the narrator at once (T32, #m1030-1008-fac1).

His 2026-10-30 ~09:47 press on the speech control's Stop never reached the
narrator: no "stopped by the speech control's stop" line, and the replays
of two play-from runs played on, one after another, to the restart at
09:51 (each line's mpv ran its full length: no cut in sysobs's process
record). The press starts in the UI Utility's window; this file covers the
narrator's half of its path, end to end over a real socket: the UI
Utility's line (a stand-in speaking the contract) -> the client's reader
thread -> the control -> ControlActions.stop.

Before #16 (a2308e3) a stop was handed to the control's worker, which was
then waiting on a slow reply from the UI Utility: the stop waited behind it
(up to the reply timeout, per request). Since #16 it is handled on the
reader thread at once. This test fails on a2308e3 and passes from c985041.

Never the real app, never a launch, no daemon, no audio. Run directly."""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
for _p in (str(SRC), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from missed_speech import MissedList  # noqa: E402
from speech_control import CONTROL_ID, ControlActions, ControlDriver  # noqa: E402
from speech_history import HistoryStore, SpeechHistory  # noqa: E402
from ui_utility_client import UiUtilityClient  # noqa: E402
from ui_utility_standin import StandIn, rev_1_2_script  # noqa: E402

STOP = {"event": "control", "id": CONTROL_ID, "action": "stop"}
SLOW_S = 3.0  # how long the stand-in sits on an `items` request before answering


class TestStopOverTheWire(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="auto-speech-wire-stop-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        # /tmp, not $TMPDIR: a Unix socket path on macOS must fit in 104 bytes.
        self.sockdir = Path(tempfile.mkdtemp(prefix="as-ws-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.sockdir, True)
        self.items_held = threading.Event()  # set while the stand-in sits on an `items`
        self.release = threading.Event()

        def script(request):
            if request.get("cmd") == "items":
                self.items_held.set()
                self.release.wait(SLOW_S)
            return rev_1_2_script(request)

        self.standin = StandIn(self.sockdir / "ui.sock", script=script).start()
        self.addCleanup(self.standin.stop)
        self.stopped = threading.Event()
        self.store = HistoryStore(self.root / "history.toml")
        self.store.set_player(True)
        self.driver = ControlDriver(
            self.store,
            history=SpeechHistory(self.root / "history", store=self.store),
            missed=MissedList(self.root / "missed.json"),
            actions=ControlActions(stop=self.stopped.set),
            installed=lambda: True,
            client_factory=lambda on_event: UiUtilityClient(self.standin.path, reply_timeout=SLOW_S + 2, on_event=on_event),
            launch=lambda: None,
            icon=None,
        )
        self.addCleanup(self.release.set)  # first in, last out: the stand-in lets go before the driver closes
        self.addCleanup(self.driver.close)

    def test_a_stop_pressed_while_the_control_waits_on_the_ui_utility_reaches_the_narrator_at_once(self):
        self.driver.start()  # the worker opens the control
        self.assertTrue(self.standin.wait_for("open", 1, timeout=3), "the control is open")
        self.driver.list_changed()  # a line ended: the worker sends the list, and the stand-in sits on it
        self.assertTrue(self.items_held.wait(3), "the worker is waiting on the UI Utility's reply")

        pressed = time.monotonic()
        self.standin.send_event(dict(STOP))  # his press, as the UI Utility writes it to the caller
        reached = self.stopped.wait(SLOW_S + 2)
        took = time.monotonic() - pressed

        self.assertTrue(reached, "the stop never reached the narrator")
        self.assertLess(took, 1.0, f"the stop waited {took:.1f} s behind the control's request to the UI Utility")


if __name__ == "__main__":
    unittest.main(verbosity=2)
