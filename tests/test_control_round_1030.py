"""The speech control's 2026-10-30 round, the caller's side of contract 1.6.

His words (09:39 to 09:55): a replayed line selects its own row instead of
adding one; every row names who spoke it; the row playing lights its words
as they are spoken; transport to the line's start, its end, and 1, 5 or 10
seconds back or forward; and (09:55) "I also want to see somehow what is
queued to be played, and I want to be able to manipulate the queue, clear
it, modify it."

Here: the arbiter's queue in play order with stable ids, moved, removed and
cleared in place (the real PriorityArbiter, no mock of it); a live narrator
worker into a recording sink for the queue tab's edits and the replay rows;
and the wire the control sends, against the stand-in UI Utility. Nothing is
heard; everything is in temporary folders."""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
for _p in (str(SRC), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("AUTO_SPEECH_TMP_ROOT", tempfile.mkdtemp(prefix="auto-speech-tests-"))

import narrator_service  # noqa: E402
import speech_control as sc  # noqa: E402
from line_ledger import DONE  # noqa: E402
from missed_speech import MissedList  # noqa: E402
from priority_arbiter import Priority, PriorityArbiter, QueueItem  # noqa: E402
from speech_control import CONTROL_ID, ControlActions, ControlDriver  # noqa: E402
from speech_history import HistoryStore, SpeechHistory  # noqa: E402
from test_line_id import _Daemon, _Sink  # noqa: E402
from test_speech_control import Clock, write_wav  # noqa: E402
from ui_utility_client import UiUtilityClient  # noqa: E402
from ui_utility_standin import StandIn, default_script  # noqa: E402

FROM = "2026-10-30T00:00:00-07:00"


# --- the arbiter: what waits, in play order --------------------------------------


class TestArbiterQueue(unittest.TestCase):
    def setUp(self) -> None:
        self.a = PriorityArbiter(maxsize=32)
        self.changes = 0

        def changed():
            self.changes += 1

        self.a.register_change_callback(changed)

    def put(self, text: str, priority: int = Priority.EXPLICIT_MCP) -> QueueItem:
        return self.a.enqueue(QueueItem(priority=priority, payload=text, line_text=text))

    def texts(self) -> list[str]:
        return [q.line_text for q in self.a.waiting()]

    def test_waiting_is_the_play_order_across_bands_with_ids_stable_while_they_wait(self):
        n1 = self.put("narration", Priority.TOOL_NARRATION)
        e1 = self.put("explicit 1")
        e2 = self.put("explicit 2")
        self.assertEqual(self.texts(), ["explicit 1", "explicit 2", "narration"])
        ids = [q.queue_id for q in self.a.waiting()]
        self.assertEqual(ids, [e1.queue_id, e2.queue_id, n1.queue_id])
        self.assertEqual(len(set(ids)), 3)
        self.a.dequeue(block=False)
        self.assertEqual([q.queue_id for q in self.a.waiting()], [e2.queue_id, n1.queue_id], "ids do not change")

    def test_move_within_a_band_and_a_line_stays_in_its_band(self):
        a, b, c = self.put("a"), self.put("b"), self.put("c")
        n = self.put("n", Priority.TOOL_NARRATION)
        self.assertEqual(self.a.move(c.queue_id, 0), 0)
        self.assertEqual(self.texts(), ["c", "a", "b", "n"])
        self.assertEqual(self.a.move(c.queue_id, 9), 2, "an explicit line stops at its band's end")
        self.assertEqual(self.texts(), ["a", "b", "c", "n"])
        self.assertEqual(self.a.move(n.queue_id, 0), 3, "a narration line stops at its band's start")
        self.assertIsNone(self.a.move("q-none", 0))
        del a, b

    def test_every_change_is_told(self):
        x = self.put("x")
        self.put("y")
        before = self.changes
        self.a.move(x.queue_id, 1)
        self.a.remove_where(lambda q: q.queue_id == x.queue_id)
        self.a.dequeue(block=False)
        self.assertEqual(self.changes, before + 3)


# --- the narrator: the queue tab's edits on the real queue ------------------------------


class _GatedSink(_Sink):
    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()
        self.gate.set()

    def play(self, wav_path, timeout=None, **kw) -> None:
        super().play(wav_path, timeout, **kw)
        self.gate.wait(10)

    def interrupt(self) -> None:
        self.gate.set()


class _Live(_Daemon):
    synth_s = 0.02

    def service(self):
        self.sink = _GatedSink()
        svc = super().service()
        svc._history = SpeechHistory(self.tmp / "history", store=HistoryStore(self.tmp / "history.toml"))
        return svc

    def say(self, text: str, line_id: str) -> None:
        self.speak(text, line_id)
        self.wait_for(lambda: self.line(line_id)["state"] == DONE, 5, lambda: self.line(line_id))
        self.wait_for(lambda: any(h.text == text for h in self.svc._history.load()), 5)

    def driver(self) -> ControlDriver:
        return ControlDriver(HistoryStore(self.tmp / "history.toml"), history=self.svc._history,
                             missed=self.svc._missed, actions=self.svc._control_actions(),
                             installed=lambda: False, icon=None, start_worker=False)

    def hold_first_of(self, d: ControlDriver, ids: list[str]) -> None:
        """A play-from whose first line stays in hand; the rest wait."""
        self.sink.gate.clear()
        before = len(self.sink.played)
        d.handle_event({"event": "play-from", "id": CONTROL_ID, "from": FROM, "items": ids})
        self.wait_for(lambda: len(self.sink.played) > before, 5)


class TestQueueTab(_Live):
    def setUp(self) -> None:
        super().setUp()
        for k in range(1, 5):
            self.say(f"Line {k}.", f"2026-10-30-09550{k}.000-desk")
        self.lines = self.svc._history.load()
        self.d = self.driver()
        self.logs: list[str] = []
        p = patch.object(narrator_service, "_log", self.logs.append)
        p.start()
        self.addCleanup(p.stop)

    def view(self) -> list[dict]:
        return self.svc.dispatch_json({"action": "queue"})["items"]

    def test_the_queue_is_what_will_play_in_order_with_text_time_and_source(self):
        self.hold_first_of(self.d, [h.id for h in self.lines])
        self.speak("Fresh from a desk.", "2026-10-30-095510.000-desk")
        rows = self.view()
        self.assertEqual([r["text"] for r in rows], ["Line 2.", "Line 3.", "Line 4.", "Fresh from a desk."])
        self.assertTrue(all(r["id"].startswith("q") and r["time"] for r in rows), rows)
        self.assertEqual(rows, self.view(), "ids stable while they wait")
        self.sink.gate.set()

    def test_clear_empties_the_queue_the_line_playing_plays_on_nothing_goes_on_the_missed_list(self):
        self.hold_first_of(self.d, [h.id for h in self.lines])
        self.speak("Fresh from a desk.", "2026-10-30-095511.000-desk")
        before = len(self.sink.played)
        self.d._on_event({"event": "queue", "id": CONTROL_ID, "action": "clear"})
        self.assertEqual(self.view(), [])
        self.assertIn("queue: cleared 4", self.logs)
        self.sink.gate.set()
        time.sleep(0.3)
        self.assertEqual(len(self.sink.played), before, "the line playing ended by itself; nothing after it")
        self.assertEqual(self.svc._missed.load(), [], "cleared lines are not put on the missed list")
        again = self.speak("Fresh from a desk.", "2026-10-30-095511.000-desk")
        self.assertFalse(again.get("duplicate"), "a cleared line may be sent again")

    def test_remove_and_move_change_the_real_queue(self):
        self.hold_first_of(self.d, [h.id for h in self.lines])
        rows = self.view()
        self.d._on_event({"event": "queue", "id": CONTROL_ID, "action": "remove", "items": [rows[0]["id"]]})
        self.assertEqual([r["text"] for r in self.view()], ["Line 3.", "Line 4."])
        self.d._on_event({"event": "queue", "id": CONTROL_ID, "action": "move", "item": rows[2]["id"], "to": 0})
        self.assertEqual([r["text"] for r in self.view()], ["Line 4.", "Line 3."])
        self.assertIn(f"queue: removed 1 ({rows[0]['id']})", self.logs)
        self.assertIn(f"queue: moved {rows[2]['id']} to 0", self.logs)
        before = len(self.sink.played)
        self.sink.gate.set()
        self.wait_for(lambda: len(self.sink.played) >= before + 2, 5)
        self.assertEqual(self.sink.played[before:], [self.lines[3].wav, self.lines[2].wav])


class TestReplayRows(_Live):
    def test_a_replay_selects_its_own_row_with_its_words_and_adds_none(self):
        self.say("Heard once.", "2026-10-30-095520.000-desk")
        [line] = self.svc._history.load()
        d = self.driver()
        self.svc._control = d
        self.hold_first_of(d, [line.id])
        items = d.items()
        self.assertEqual([i["id"] for i in items], [line.id], "no now-row for a replay")
        self.assertEqual(d._model.current, line.id)
        self.assertTrue(items[0].get("words"), "the row playing carries its words")
        self.sink.gate.set()

    def test_a_fresh_line_is_a_now_row_with_its_sender_and_words(self):
        self.svc._speaker.name_for.return_value = "Master Desk"
        d = self.driver()
        self.svc._control = d
        self.sink.gate.clear()
        self.svc.dispatch_json({"action": "speak", "text": "A fresh line.", "priority": 2,
                                "line_id": "2026-10-30-095530.000-desk", "session_id": "s-1"})
        self.wait_for(lambda: d._model.now is not None, 5)
        [now] = [i for i in d.items() if i["id"].startswith("now-")]
        self.assertEqual(now["source"], "Master Desk")
        self.assertTrue(now.get("words"))
        self.sink.gate.set()


# --- the wire, against the stand-in UI Utility ------------------------------------------


class TestWire(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="auto-speech-wire-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.sockdir = Path(tempfile.mkdtemp(prefix="as-w-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.sockdir, True)
        self.store = HistoryStore(self.root / "history.toml")
        self.store.set_player(True)
        self.history = SpeechHistory(self.root / "history", store=self.store)
        self.clock = Clock(100.0)
        self.calls: list[tuple] = []
        self.position: float | None = None
        self.queue = [{"id": "q7", "text": "Next line.", "time": "2026-10-30T09:55:00-07:00", "source": "Master Desk"}]

    def stand_in(self, script=default_script) -> StandIn:
        s = StandIn(self.sockdir / "ui.sock", script=script).start()
        self.addCleanup(s.stop)
        return s

    def driver(self) -> ControlDriver:
        def rec(name, result=True):
            return lambda *a: (self.calls.append((name, *a)), result)[1]

        d = ControlDriver(
            self.store, history=self.history, missed=MissedList(self.root / "missed.json"),
            actions=ControlActions(restart=rec("restart"), skip=rec("skip"), seek=rec("seek"), pause=rec("pause"),
                                   resume=rec("resume"), set_rate=lambda r: r, position=lambda: self.position,
                                   queue_items=lambda: list(self.queue)),
            installed=lambda: True, client_factory=lambda on_event: UiUtilityClient(
                self.sockdir / "ui.sock", reply_timeout=1.0, on_event=on_event),
            launch=lambda: None, icon=None, start_worker=False, clock=self.clock,
        )
        self.addCleanup(d.close)
        return d

    def heard(self) -> object:
        wav = write_wav(self.root / "src" / "a.wav", seconds=2.0)
        return self.history.export(wav, text="Two words.", sender="Master Desk", session_id="s1",
                                   word_timings=[{"w": "Two", "start": 0.0, "end": 0.5}, {"w": "words.", "start": 0.6, "end": 1.2}])

    def test_open_carries_the_queue_and_a_replay_sends_its_row_with_words_and_the_position(self):
        s = self.stand_in()
        line = self.heard()
        d = self.driver()
        self.assertEqual(d.sync(), "opened")
        [q] = [r for r in s.requests if r.get("cmd") == "queue"]
        self.assertEqual(q, {"v": 1, "req": q["req"], "cmd": "queue", "id": CONTROL_ID, "items": self.queue})

        d.playing(line.text, None, line.id)
        self.clock.t += 1.5
        d.sync()
        [item] = [r for r in s.requests if r.get("cmd") == "item"]
        self.assertEqual(item["item"]["id"], line.id)
        self.assertEqual(item["item"]["words"][0], {"w": "Two", "start": 0.0, "end": 0.5})
        self.assertEqual(item["item"]["source"], "Master Desk")
        state = [r for r in s.requests if r.get("cmd") == "state"][-1]
        self.assertEqual((state["state"], state["current"], state["position"]), ("playing", line.id, 1.5))

        self.position = 0.8  # the player's own word wins over the estimate
        d.handle_event({"event": "control", "id": CONTROL_ID, "action": "hold"})
        d.sync()
        state = [r for r in s.requests if r.get("cmd") == "state"][-1]
        self.assertEqual((state["state"], state["position"]), ("held", 0.8))

    def test_transport_start_end_and_seconds(self):
        self.stand_in()
        line = self.heard()
        d = self.driver()
        d.sync()
        d.playing(line.text, None, line.id)
        for action, extra in (("start", {}), ("end", {}), ("skip-back", {"seconds": 5}),
                              ("skip-forward", {"seconds": 10}), ("skip-forward", {"seconds": True})):
            d._on_event({"event": "control", "id": CONTROL_ID, "action": action, **extra})
        self.assertEqual(self.calls, [("restart",), ("skip",), ("seek", -5.0), ("seek", 10.0), ("skip",)])

    def test_a_queue_change_is_sent_once_per_debounce(self):
        s = self.stand_in()
        d = self.driver()
        d.sync()
        for _ in range(5):
            d.queue_changed()
        d.sync()
        self.assertEqual(len([r for r in s.requests if r.get("cmd") == "queue"]), 1, "held back inside the debounce")
        self.clock.t += sc.QUEUE_DEBOUNCE_SECONDS
        d.sync()
        self.assertEqual(len([r for r in s.requests if r.get("cmd") == "queue"]), 2)

    def test_a_copy_before_1_6_refuses_the_queue_and_the_control_stays_open(self):
        def old(request):
            if request.get("cmd") == "queue":
                return {"ok": False, "error": "unknown cmd"}
            return default_script(request)

        s = self.stand_in(old)
        d = self.driver()
        self.assertEqual(d.sync(), "opened")
        self.clock.t += 1
        d.queue_changed()
        d.sync()
        self.assertEqual(len([r for r in s.requests if r.get("cmd") == "open" and r.get("panel") == "control"]), 1, "not opened again")
        self.assertEqual(len([r for r in s.requests if r.get("cmd") == "queue"]), 1, "asked once per connection")


# --- `auto-speech queue` -------------------------------------------------------------


class TestQueueCli(unittest.TestCase):
    def run_cli(self, *argv, client):
        import io

        import cli

        out = io.StringIO()
        with patch("sys.stdout", out):
            rc = cli.main(["queue", *argv], client=client)
        return rc, out.getvalue()

    def test_it_lists_the_waiting_lines_in_play_order(self):
        from daemon_client import DaemonResponse

        client = type("C", (), {"queue": lambda self: DaemonResponse(status="ok", action="queue", raw={"items": [
            {"id": "q3", "text": "First to play.", "time": "2026-10-30T09:55:01-07:00", "source": "Master Desk"},
            {"id": "q1", "text": "Then this.", "time": "2026-10-30T09:54:00-07:00"},
        ]})})()
        rc, out = self.run_cli(client=client)
        self.assertEqual(rc, 0)
        self.assertIn("queue: 2 lines waiting, in the order they will play", out)
        self.assertLess(out.index("First to play."), out.index("Then this."))
        self.assertIn("09:55:01  Master Desk  First to play.  [q3]", out)

    def test_nothing_waiting(self):
        from daemon_client import DaemonResponse

        client = type("C", (), {"queue": lambda self: DaemonResponse(status="ok", action="queue", raw={"items": []})})()
        self.assertEqual(self.run_cli(client=client), (0, "queue: nothing waits\n"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
