"""The speech control's stop is a playback act (#m1030-0948).

His 2026-10-30 09:48: "the stop button in the speech app kills the server.
its just supposed to be a playback controller, not the server controller".
At 09:47 a play-from run had 130-odd replays waiting; his stop never reached
the narrator (no "stopped by the speech control's stop" line, no barge-in
line), and a stop that did reach it cleared only the narration bands, not
the explicit band the replays wait in.

Here: a stop from the control is handled on the event thread itself, never
behind a play-from or a sync; it ends the line playing, takes every waiting
replay out at once, cancels a run still being queued and plays still waiting
to be handled, and leaves the narrator running. Nothing is heard (a
recording sink); everything is in a temporary folder."""

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
from unittest.mock import patch  # noqa: E402

import narrator_service  # noqa: E402
from line_ledger import DONE  # noqa: E402
from missed_speech import REASON_STOPPED  # noqa: E402
from speech_control import CONTROL_ID, ControlActions, ControlDriver  # noqa: E402
from speech_history import HistoryStore, SpeechHistory  # noqa: E402
from test_line_id import _Daemon, _Sink  # noqa: E402

FROM = "2026-10-30T00:00:00-07:00"
STOP = {"event": "control", "id": CONTROL_ID, "action": "stop"}


class _GatedSink(_Sink):
    """Plays wait at the gate once it is shut; an interrupt opens it, as
    killing mpv ends the line playing."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = threading.Event()
        self.gate.set()
        self.interrupts = 0

    def play(self, wav_path, timeout=None, **kw) -> None:
        super().play(wav_path, timeout, **kw)
        self.gate.wait(10)

    def interrupt(self) -> None:
        self.interrupts += 1
        self.gate.set()


class TestStopWithARunQueued(_Daemon):
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

    def driver(self, logged: list[str]) -> ControlDriver:
        return ControlDriver(
            HistoryStore(self.tmp / "history.toml"), history=self.svc._history, missed=self.svc._missed,
            actions=self.svc._control_actions(), installed=lambda: False, log=logged.append,
            icon=None, start_worker=False,
        )

    def test_stop_on_the_event_thread_clears_the_replays_and_the_narrator_runs_on(self):
        for k in range(1, 5):
            self.say(f"Line {k}.", f"2026-10-30-09470{k}.000-desk")
        lines = self.svc._history.load()
        before = len(self.sink.played)
        d = self.driver([])
        self.sink.gate.clear()
        d.handle_event({"event": "play-from", "id": CONTROL_ID, "from": FROM, "items": [h.id for h in lines]})
        self.wait_for(lambda: len(self.sink.played) > before, 5)
        self.speak("Fresh from a desk.", "2026-10-30-094700.000-desk")
        self.assertEqual(self.svc._tts_queue.qsize(), 4, "three replays and the fresh line wait")

        logs: list[str] = []
        with patch.object(narrator_service, "_log", logs.append), \
                patch.object(narrator_service.os, "kill", side_effect=AssertionError("a stop never signals")):
            started = time.monotonic()
            d._on_event(dict(STOP))  # the client's reader thread: no worker runs in this driver
            took = time.monotonic() - started
            self.assertLess(took, 1.0, "handled at once, on the event thread")
            self.assertEqual(self.sink.interrupts, 1, "the line playing ends")
            replays = [q for q in self.svc._queued_items() if getattr(q, "history_id", "")]
            self.assertEqual(replays, [], "every waiting replay taken out at once")
            self.wait_for(lambda: self.svc._tts_queue.qsize() == 0, 5)
            self.wait_for(lambda: any(m.text == "Fresh from a desk." for m in self.svc._missed.load()), 5)

        self.assertIn("cleared 3 queued replays", " ".join(logs))
        self.assertTrue(any("stopped by the speech control's stop" in s for s in logs), logs)
        self.assertEqual(len(self.sink.played), before + 1, "nothing played after the stop")
        [fresh] = [m for m in self.svc._missed.load() if m.text == "Fresh from a desk."]
        self.assertEqual(fresh.reason, REASON_STOPPED, "a live line waiting goes on the missed list, as ADR-024 says")
        self.assertFalse(self.svc._stop.is_set(), "the narrator is not shutting down")
        self.assertTrue(self.worker.is_alive(), "the narrator's worker runs on")


class TestStopCancelsPlays(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="auto-speech-stop-"))
        self.calls: list[tuple] = []
        self.store = HistoryStore(self.tmp / "history.toml")
        self.history = SpeechHistory(self.tmp / "history", store=self.store)

    def driver(self, **actions) -> ControlDriver:
        base = dict(stop=lambda: self.calls.append(("stop",)))
        base.update(actions)
        return ControlDriver(self.store, history=self.history, actions=ControlActions(**base),
                             installed=lambda: False, icon=None, start_worker=False)

    def heard(self, n: int):
        wav = self.tmp / "a.wav"
        wav.write_bytes(b"RIFF")
        return [self.history.export(wav, text=f"Line {k}.", spoken_at=1_000_000.0 + k) for k in range(n)]

    def test_a_stop_during_a_run_being_queued_queues_none_of_the_rest(self):
        lines = self.heard(5)
        d = None

        def replay(line):
            self.calls.append(("replay", line.id))
            if len(self.calls) == 2:  # his stop arrives while the run is being queued
                d._on_event(dict(STOP))

        d = self.driver(replay=replay)
        d.handle_event({"event": "play-from", "id": CONTROL_ID, "from": FROM, "items": [h.id for h in lines if h]})
        self.assertEqual(self.calls, [("replay", lines[0].id), ("replay", lines[1].id), ("stop",)])

    def test_plays_still_waiting_to_be_handled_are_dropped_by_a_stop(self):
        lines = self.heard(2)
        d = self.driver(replay=lambda line: self.calls.append(("replay", line.id)))
        d._on_event({"event": "play-from", "id": CONTROL_ID, "from": FROM, "items": [h.id for h in lines]})
        d._on_event({"event": "play-item", "id": CONTROL_ID, "item": lines[0].id})
        d._on_event(dict(STOP))
        self.assertEqual(self.calls, [("stop",)])
        self.assertEqual(list(d._events), [], "nothing left for the worker to play")


if __name__ == "__main__":
    unittest.main(verbosity=2)
