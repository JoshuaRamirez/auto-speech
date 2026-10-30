"""Unit tests for ADR-027: play every line from a chosen time, in order
(#m1030-0850-a448).

His 2026-10-30 08:47: "I want to play from a particular past time all the
messages up to the latest." The speech control (contract 1.5) sends
play-from with the ids of every listed line at or after the time, oldest
first. Here a live worker plays them into a recording sink: each line is
heard once, in the order given, to its end; a replay is not exported to the
history again and never lands on the missed list, even when his stop comes
in the middle.

Nothing is heard: the sink records and never starts mpv, and the runner sets
AUTO_SPEECH_MPV_AO=null besides. Everything is in a temporary folder. Run
directly or through tests/run_all.sh."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
for _p in (str(SRC), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("AUTO_SPEECH_TMP_ROOT", tempfile.mkdtemp(prefix="auto-speech-tests-"))

import unittest  # noqa: E402

from line_ledger import DONE  # noqa: E402
from missed_speech import REASON_LONGEST_WAIT, MissedLine  # noqa: E402
from priority_arbiter import QueueItem  # noqa: E402
from speech_control import CONTROL_ID, MISSED_PREFIX, ControlDriver  # noqa: E402
from speech_history import HistoryStore, SpeechHistory  # noqa: E402
from test_line_id import _Daemon, _Sink  # noqa: E402

FROM = "2026-10-30T08:00:00-07:00"


class _Live(_Daemon):
    synth_s = 0.02

    def service(self):
        svc = super().service()
        svc._history = SpeechHistory(self.tmp / "history", store=HistoryStore(self.tmp / "history.toml"))
        return svc

    def settle(self) -> None:
        time.sleep(0.3)

    def say(self, text: str, line_id: str) -> None:
        self.speak(text, line_id)
        self.wait_for(lambda: self.line(line_id)["state"] == DONE, 5, lambda: self.line(line_id))
        # The history line is written just after the ledger says done: wait for it too.
        heard = len(self.svc._history.load())
        self.wait_for(lambda: any(h.text == text for h in self.svc._history.load()[max(0, heard - 1):]), 5)

    def driver(self, logged: list[str]) -> ControlDriver:
        return ControlDriver(
            HistoryStore(self.tmp / "history.toml"),
            history=self.svc._history,
            missed=self.svc._missed,
            actions=self.svc._control_actions(),
            installed=lambda: False,
            log=logged.append,
            icon=None,
            start_worker=False,
        )


class TestPlayFrom(_Live):
    def test_each_line_plays_once_in_order_to_its_end_and_nothing_is_written_twice(self):
        for n in (1, 2, 3):
            self.say(f"Line {n}.", f"2026-10-30-08000{n}.000-desk")
        lines = self.svc._history.load()  # oldest first
        self.assertEqual([h.text for h in lines], ["Line 1.", "Line 2.", "Line 3."])
        self.svc._missed.miss(MissedLine(id="m1", text="Missed between.", queued_at=time.time()),
                              reason=REASON_LONGEST_WAIT)
        played_before = len(self.sink.played)

        logged: list[str] = []
        d = self.driver(logged)
        ids = [lines[0].id, MISSED_PREFIX + "m1", lines[1].id, lines[2].id]
        d.handle_event({"event": "play-from", "id": CONTROL_ID, "from": FROM, "items": ids})
        self.wait_for(lambda: len(self.sink.played) >= played_before + 4, 5)
        self.settle()

        played = self.sink.played[played_before:]
        self.assertEqual(len(played), 4, "each line once")
        self.assertEqual(played[0], lines[0].wav)
        self.assertNotIn(played[1], [h.wav for h in lines], "the missed line, synthesized")
        self.assertEqual(played[2:], [lines[1].wav, lines[2].wav], "in the order given, none cut off")
        self.assertEqual(logged, [f"control: play-from {FROM} (4 lines)"])

        texts = [h.text for h in self.svc._history.load()]
        self.assertEqual(texts, ["Line 1.", "Line 2.", "Line 3.", "Missed between."],
                         "the replays are not exported again; the missed line, heard, is exported once")
        self.assertEqual(self.svc._missed.load(), [], "the missed line left the list; no replay was added")

    def test_play_all_already_plays_in_order_none_cutting_another_off(self):
        for n in (1, 2, 3):
            self.say(f"Line {n}.", f"2026-10-30-08003{n}.000-desk")
        lines = self.svc._history.load()
        before = len(self.sink.played)
        d = self.driver([])
        d.handle_event({"event": "play-all", "id": CONTROL_ID, "items": [h.id for h in reversed(lines)]})
        self.wait_for(lambda: len(self.sink.played) >= before + 3, 5)
        self.settle()
        self.assertEqual(self.sink.played[before:], [h.wav for h in reversed(lines)])

    def test_his_stop_in_the_middle_puts_no_replay_on_the_missed_list(self):
        self.say("Heard once.", "2026-10-30-080010.000-desk")
        [line] = self.svc._history.load()
        self.svc._stop_speech(by="test")
        replay = QueueItem(priority=2, payload=str(line.wav), line_text=line.text, history_id=line.id, hold=False)
        self.assertFalse(self.svc._play_item(line.wav, replay, text=line.text))
        self.assertEqual(self.svc._missed.load(), [], "a heard line is in the history already, not missed")

    def test_normal_lines_still_play_once_and_a_resend_is_refused(self):
        self.say("Plain line.", "2026-10-30-080020.000-desk")
        again = self.speak("Plain line.", "2026-10-30-080020.000-desk")
        self.assertTrue(again.get("duplicate"))
        self.settle()
        self.assertEqual(len(self.sink.played), 1)
        self.assertEqual(len(self.svc._history.load()), 1)


class _GatedSink(_Sink):
    """A sink whose plays wait at a gate once it is shut: the line in hand
    stays "playing" while the test queues more behind it."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()
        self.gate.set()

    def play(self, wav_path, timeout=None, **kw) -> None:
        super().play(wav_path, timeout, **kw)
        self.gate.wait(10)


class TestANewRunReplacesTheOld(_Live):
    """His 2026-10-30 09:41: a second play-from while the first was still
    playing stacked 82 more replays behind the 57 still waiting
    (#m1030-0939-c4f9). A new run from the control (play-from or play-all)
    replaces the replays still waiting from an earlier run; the line playing
    now plays to its end; nothing that is not a run's replay is dropped."""

    def service(self):
        self.sink = _GatedSink()
        return super().service()

    def lines(self, tag: str, n: int = 4):
        for k in range(1, n + 1):
            self.say(f"Line {k}.", f"2026-10-30-0{tag}{k}.000-desk")
        return self.svc._history.load()

    def shut_on_first(self, d: ControlDriver, evt: dict) -> None:
        """Shut the gate, start a run, and wait until its first line is in hand."""
        self.sink.gate.clear()
        before = len(self.sink.played)
        d.handle_event(evt)
        self.wait_for(lambda: len(self.sink.played) > before, 5)

    def test_a_second_play_from_replaces_the_first_runs_waiting_replays(self):
        a, b, c, e = self.lines("9000")
        before = len(self.sink.played)
        logged: list[str] = []
        d = self.driver(logged)
        self.shut_on_first(d, {"event": "play-from", "id": CONTROL_ID, "from": FROM,
                               "items": [a.id, b.id, c.id, e.id]})
        self.assertEqual(self.sink.played[before:], [a.wav], "the first run's first line is playing")
        # Between the runs: a fresh line from a desk, and a single play-item.
        self.speak("Fresh from a desk.", "2026-10-30-094000.000-desk")
        d.handle_event({"event": "play-item", "id": CONTROL_ID, "item": b.id})
        self.wait_for(lambda: self.svc._tts_queue.qsize() == 5, 5, lambda: self.svc._tts_queue.qsize())

        d.handle_event({"event": "play-from", "id": CONTROL_ID, "from": FROM, "items": [c.id, e.id]})
        self.assertEqual(self.svc._tts_queue.qsize(), 4, "fresh line, play-item, and the new run's two")
        self.sink.gate.set()
        self.wait_for(lambda: len(self.sink.played) >= before + 5, 5)
        self.settle()

        played = self.sink.played[before:]
        self.assertEqual(len(played), 5, played)
        self.assertEqual(played[0], a.wav, "the line playing at the second press played to its end")
        self.assertNotIn(played[1], [h.wav for h in (a, b, c, e)], "the fresh line, synthesized, once")
        self.assertEqual(played[2:], [b.wav, c.wav, e.wav], "the play-item, then the new run, in order")
        self.assertEqual(logged, [
            f"control: play-from {FROM} (4 lines)",
            "control: replaced 3 queued replays",
            f"control: play-from {FROM} (2 lines)",
        ])
        self.assertEqual(self.line("2026-10-30-094000.000-desk")["state"], DONE)
        texts = [h.text for h in self.svc._history.load()]
        self.assertEqual(texts.count("Fresh from a desk."), 1, "the fresh line heard and kept once")

    def test_play_all_and_play_from_replace_each_other(self):
        a, b, c, e = self.lines("9100")
        before = len(self.sink.played)
        logged: list[str] = []
        d = self.driver(logged)
        self.shut_on_first(d, {"event": "play-all", "id": CONTROL_ID, "items": [a.id, b.id, c.id, e.id]})
        d.handle_event({"event": "play-from", "id": CONTROL_ID, "from": FROM, "items": [b.id]})
        d.handle_event({"event": "play-all", "id": CONTROL_ID, "items": [e.id]})
        self.sink.gate.set()
        self.wait_for(lambda: len(self.sink.played) >= before + 2, 5)
        self.settle()
        self.assertEqual(self.sink.played[before:], [a.wav, e.wav])
        self.assertEqual(logged, [
            "control: replaced 3 queued replays",
            f"control: play-from {FROM} (1 lines)",
            "control: replaced 1 queued replays",
        ])


if __name__ == "__main__":
    unittest.main(verbosity=2)
