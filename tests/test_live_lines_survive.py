"""Clearing the waiting replays keeps the live lines that were waiting (T27,
#m1030-1008-fac1).

On 2026-10-30 two play-from runs left 130-odd replays queued ahead of a live
desk line. The replays may be cleared three ways, and each keeps a live line
(a line not yet heard, from a desk or a session) that was waiting:

  (a) his stop at the control (ADR-024, #16): the replays go; a waiting live
      line goes on the missed list as `stopped`. His next play lifts the stop
      only (#m1030-1241-6e0a); his Play all plays the live lines. Proven end
      to end here: test_a_live_line_waiting_behind_replays_stays_on_the_list_and_plays_on_his_play_all.
  (b) a shutdown set-aside (_set_aside_queued / _set_aside_item): replays
      dropped, live lines on the missed list as `restart`. Proven by
      test_stop_and_drain.TestSigterm.test_whatever_is_still_queued_goes_on_the_missed_list_in_order.
  (c) a new play-from replacing an older run (#17): only the old run's
      replays go; a fresh line and a play-item stay queued and play. Proven by
      test_play_from.TestANewRunReplacesTheOld.test_a_second_play_from_replaces_the_first_runs_waiting_replays.

Nothing is heard (a recording sink); everything is in a temporary folder."""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
import os
import sys
import tempfile
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
for _p in (str(SRC), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("AUTO_SPEECH_TMP_ROOT", tempfile.mkdtemp(prefix="auto-speech-tests-"))

import unittest  # noqa: E402

from line_ledger import DONE  # noqa: E402
from missed_speech import REASON_STOPPED  # noqa: E402
from speech_control import CONTROL_ID, ControlDriver  # noqa: E402
from speech_history import HistoryStore, SpeechHistory  # noqa: E402
from test_line_id import _Daemon  # noqa: E402
from test_stop_is_playback import _GatedSink  # noqa: E402

FROM = "2026-10-30T00:00:00-07:00"
STOP = {"event": "control", "id": CONTROL_ID, "action": "stop"}
PLAY = {"event": "control", "id": CONTROL_ID, "action": "play"}
LIVE = (("Live from the Master Desk.", "2026-10-30-094044.543-desk"),
        ("Live from a session.", "2026-10-30-094500.000-desk"))


class TestLiveLinesSurviveAStop(_Daemon):
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

    def test_a_live_line_waiting_behind_replays_stays_on_the_list_and_plays_on_his_play_all(self):
        for k in range(1, 5):
            self.say(f"Heard {k}.", f"2026-10-30-09300{k}.000-desk")
        heard = self.svc._history.load()
        d = ControlDriver(HistoryStore(self.tmp / "history.toml"), history=self.svc._history, missed=self.svc._missed,
                          actions=self.svc._control_actions(), installed=lambda: False, icon=None, start_worker=False)
        before = len(self.sink.played)
        self.sink.gate.clear()
        d.handle_event({"event": "play-from", "id": CONTROL_ID, "from": FROM, "items": [h.id for h in heard]})
        self.wait_for(lambda: len(self.sink.played) > before, 5)
        for text, line_id in LIVE:
            self.speak(text, line_id)
        self.assertEqual(self.svc._tts_queue.qsize(), 5, "three replays, then the two live lines, wait")

        d._on_event(dict(STOP))  # his stop, on the client's reader thread
        self.wait_for(lambda: self.svc._tts_queue.qsize() == 0, 5)
        self.wait_for(lambda: len(self.svc._missed.load()) == 2, 5, self.svc._missed.load)
        waiting = self.svc._missed.load()
        self.assertEqual([m.text for m in waiting], [t for t, _ in LIVE], "both live lines kept, in order")
        self.assertEqual({m.reason for m in waiting}, {REASON_STOPPED})
        self.assertEqual(len(self.sink.played), before + 1, "nothing played after the stop")

        d._on_event(dict(PLAY))  # his next play: the stop lifts, and only that (#m1030-1241-6e0a)
        self.assertIsNone(self.svc._stopped_since, "his play ended the stop")
        time.sleep(0.2)
        self.assertEqual(len(self.sink.played), before + 1, "nothing his stop set aside plays by itself")
        self.assertEqual([m.text for m in self.svc._missed.load()], [t for t, _ in LIVE], "still on the list")
        rows = [i["id"] for i in d.items() if i.get("unplayed")]
        d.handle_event({"event": "play-all", "id": CONTROL_ID, "items": list(reversed(rows))})  # his Play all, oldest first
        self.wait_for(lambda: all(self.line(i)["state"] == DONE for _, i in LIVE), 5,
                      lambda: [self.line(i) for _, i in LIVE])
        self.wait_for(lambda: self.svc._tts_queue.qsize() == 0, 5)

        self.assertEqual(len(self.sink.played), before + 3, "the two live lines played, and no replay")
        self.assertNotIn(self.sink.played[-1], [h.wav for h in heard])
        self.assertNotIn(self.sink.played[-2], [h.wav for h in heard])
        texts = [h.text for h in self.svc._history.load()]
        self.assertEqual(texts[-2:], [t for t, _ in LIVE], "heard once each, in the order they came")
        self.assertEqual(self.svc._missed.load(), [], "off the missed list once heard")


if __name__ == "__main__":
    unittest.main(verbosity=2)
