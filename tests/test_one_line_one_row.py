"""One spoken line is one row in the speech player, whatever path it takes (#m1030-1241-6e0a).

His 2026-10-30 12:41 "messed up rows". A 10:55 Master Desk line (id
2026-10-30-105507.372-desk) was set aside by his 11:00 stop: a missed row at
10:55. At 12:39 it was queued again from the missed list and played; while it
played the control selected its missed row, but once heard its history record
(9a1a526d) was a new row on top, timed 12:40:23, and the 10:55 row was gone:
the line had moved and looked new. And the dedupe of 0.7.1 (rows keyed by
line id) could not hold: the history never wrote the line id into its sidecar,
so a line with no origin file was a row per play.

Here, with the real queue, history and missed list (a live narrator worker
into a recording sink): the line keeps its row's time (when it came, not when
it was finally heard), the row heard is the one selected, a replay of it adds
none, and the same line id written twice is one row. Nothing is heard;
everything is in temporary folders."""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
import sys
import time
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
for _p in (str(SRC), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import speech_control as sc  # noqa: E402
from line_ledger import DONE  # noqa: E402
from missed_speech import MISSED, REASON_STOPPED  # noqa: E402
from speech_control import CONTROL_ID  # noqa: E402
from test_control_round_1030 import _Live  # noqa: E402

LINE = "2026-10-30-105507.372-desk"
TEXT = "Your speech player closed and reopened once."


class TestOneLineOneRow(_Live):
    def setUp(self) -> None:
        super().setUp()
        self.d = self.driver()
        self.svc._control = self.d

    def rows_for(self, text: str) -> list[dict]:
        return [i for i in self.d.items() if i["text"] == text]

    def test_a_line_set_aside_by_his_stop_and_heard_later_keeps_its_one_row(self):
        self.svc._stop_speech(by="test")
        self.svc.dispatch_json({"action": "speak", "text": TEXT, "priority": 2, "line_id": LINE,
                                "origin_file": f"{LINE}.txt", "session_id": "s-1"})
        self.wait_for(lambda: len(self.svc._missed.load()) == 1, 5)
        [entry] = self.svc._missed.load()
        self.assertEqual((entry.status, entry.reason), (MISSED, REASON_STOPPED))
        [row] = self.rows_for(TEXT)
        self.assertEqual(row["id"], sc.MISSED_PREFIX + entry.id)
        came = row["time"]

        time.sleep(1.1)  # heard a moment later than it came: the row must not move to the play's time
        self.svc._resume_speech(by="test")
        self.sink.gate.clear()
        self.d.handle_event({"event": "play-item", "id": CONTROL_ID, "item": row["id"]})
        self.wait_for(lambda: self.d._model.current == row["id"], 5, lambda: self.d._model.current)
        self.assertEqual([r["id"] for r in self.rows_for(TEXT)], [row["id"]], "while it plays: its own row")
        self.sink.gate.set()
        self.wait_for(lambda: self.line(LINE)["state"] == DONE, 5, lambda: self.line(LINE))
        self.wait_for(lambda: self.svc._history.load() and self.d._model.current != row["id"], 5)

        [heard] = self.svc._history.load()
        self.assertEqual(heard.sidecar.get("line_id"), LINE, "the history keeps the line's id")
        rows = self.rows_for(TEXT)
        self.assertEqual(len(rows), 1, f"one line, one row: {rows}")
        self.assertEqual(rows[0]["id"], heard.id)
        self.assertEqual(self.d._model.current, heard.id, "the row heard is the one selected")
        self.assertEqual(rows[0]["time"], came, "at the time the line came, where its row was")

        # Played again from its row: still that one row.
        self.d.handle_event({"event": "play-item", "id": CONTROL_ID, "item": heard.id})
        self.wait_for(lambda: len(self.sink.played) >= 2, 5)
        time.sleep(0.2)
        self.assertEqual([r["id"] for r in self.rows_for(TEXT)], [heard.id])

    def test_the_same_line_id_written_twice_with_no_origin_file_is_one_row(self):
        line = "2026-10-30-111111.000-desk"
        for _ in range(2):
            self.svc._history.export(self.svc_wav(), text="Twice.", line_id=line)
        self.assertEqual(len(self.svc._history.load()), 2)
        self.assertEqual(len(self.rows_for("Twice.")), 1)

    def test_a_missed_copy_of_a_line_already_heard_is_not_a_second_row(self):
        line = "2026-10-30-121212.000-desk"
        self.say("Heard, and a copy left on the list.", line)
        from missed_speech import MissedLine, new_id

        self.svc._missed._write([MissedLine(id=new_id(), text="Heard, and a copy left on the list.",
                                        queued_at=time.time(), status=MISSED, reason="added", line_id=line)])
        rows = self.rows_for("Heard, and a copy left on the list.")
        self.assertEqual(len(rows), 1, rows)
        self.assertFalse(rows[0].get("unplayed"), "the heard row stands for it")

    def svc_wav(self) -> Path:
        from test_speech_control import write_wav

        p = self.tmp / f"w{time.monotonic_ns()}.wav"
        write_wav(p, 0.2)
        return p


if __name__ == "__main__":
    unittest.main(verbosity=2)
