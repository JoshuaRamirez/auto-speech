"""Unit tests for missed_speech: the missed list's store, its text, and the
shared CLI/MCP action runner. No daemon, no audio; a fake client records
what `play` would send. Run directly."""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import missed_speech as ms  # noqa: E402
from missed_speech import (  # noqa: E402
    HELD,
    MISSED,
    REASON_INTERRUPTED,
    REASON_LONGEST_WAIT,
    REASON_RESTART,
    REASON_STALE,
    MissedLine,
    MissedList,
    format_lines,
    run_action,
)

T0 = 1_700_000_000.0


class FakeClient:
    def __init__(self, fail: Exception | None = None):
        self.speaks: list[tuple[str, dict]] = []
        self.plays: list[tuple[str, dict]] = []
        self.fail = fail

    def speak(self, text, **kwargs):
        if self.fail:
            raise self.fail
        self.speaks.append((text, kwargs))

    def play_cache(self, source_hash, **kwargs):
        if self.fail:
            raise self.fail
        self.plays.append((source_hash, kwargs))


class TestMissedLine(unittest.TestCase):
    def test_round_trip_and_defaults(self):
        line = MissedLine(id="a1", text="hello", queued_at=T0, held_for=("Music",), source_hash="f" * 64)
        back = MissedLine.from_dict(json.loads(json.dumps(line.to_dict())))
        self.assertEqual(back, line)
        self.assertEqual(back.status, HELD)
        self.assertEqual(MissedLine.from_dict({"text": "x"}).status, MISSED)  # a bare record is a missed line
        self.assertTrue(MissedLine.from_dict({}).id)  # always has an id

    def test_label_names_cached_audio_without_text(self):
        self.assertEqual(MissedLine(id="a", text="", queued_at=T0, source_hash="abcdef0123456789" * 4).label, "(cached audio abcdef012345)")
        self.assertEqual(MissedLine(id="a", text="", queued_at=T0).label, "(no text)")


class TestMissedList(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "state" / "missed.json"
        self.now = T0 + 500
        self.missed = MissedList(self.path, clock=lambda: self.now)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_missing_file_reads_empty_and_a_read_never_creates_it(self):
        self.assertEqual(self.missed.load(), [])
        self.assertEqual(self.missed.counts(), {"held": 0, "missed": 0, "meeting": 0})
        self.assertFalse(self.path.exists())
        self.assertFalse(self.path.parent.exists())

    def test_hold_then_miss_then_remove(self):
        line = MissedLine(id="a1", text="first", queued_at=T0)
        self.missed.hold(line, ("Music (pid 5)",))
        [got] = self.missed.load()
        self.assertEqual((got.status, got.held_for, got.reason, got.missed_at), (HELD, ("Music (pid 5)",), "", None))
        self.missed.miss(got, reason=REASON_LONGEST_WAIT, waited_seconds=180.0)
        [got] = self.missed.load()
        self.assertEqual((got.status, got.reason, got.waited_seconds, got.missed_at, got.held_for), (MISSED, REASON_LONGEST_WAIT, 180.0, self.now, ("Music (pid 5)",)))
        self.assertEqual(self.missed.counts(), {"held": 0, "missed": 1, "meeting": 0})
        self.assertTrue(self.missed.remove("a1"))
        self.assertFalse(self.missed.remove("a1"))
        self.assertEqual(self.missed.load(), [])

    def test_upsert_replaces_by_id_and_load_is_oldest_first(self):
        self.missed.upsert(MissedLine(id="b", text="second", queued_at=T0 + 10))
        self.missed.upsert(MissedLine(id="a", text="first", queued_at=T0))
        self.missed.upsert(MissedLine(id="b", text="second, edited", queued_at=T0 + 10))
        self.assertEqual([(m.id, m.text) for m in self.missed.load()], [("a", "first"), ("b", "second, edited")])

    def test_miss_without_held_for_keeps_the_held_apps(self):
        self.missed.hold(MissedLine(id="a", text="x", queued_at=T0), ("ChatGPT",))
        self.missed.miss(self.missed.load()[0], reason=REASON_STALE, waited_seconds=200)
        self.assertEqual(self.missed.load()[0].held_for, ("ChatGPT",))

    def test_clear_returns_the_count(self):
        self.missed.upsert(MissedLine(id="a", text="x", queued_at=T0))
        self.missed.upsert(MissedLine(id="b", text="y", queued_at=T0 + 1))
        self.assertEqual(self.missed.clear(), 2)
        self.assertEqual(self.missed.clear(), 0)
        self.assertEqual(self.missed.load(), [])

    def test_settle_held_turns_held_into_missed_for_a_restart(self):
        self.missed.hold(MissedLine(id="a", text="x", queued_at=T0), ("Music",))
        self.missed.miss(MissedLine(id="b", text="y", queued_at=T0 + 1), reason=REASON_INTERRUPTED)
        self.assertEqual(self.missed.settle_held(), 1)
        a, b = self.missed.load()
        self.assertEqual((a.status, a.reason, a.missed_at), (MISSED, REASON_RESTART, self.now))
        self.assertEqual((b.status, b.reason), (MISSED, REASON_INTERRUPTED))
        self.assertEqual(self.missed.settle_held(), 0)

    def test_corrupt_or_foreign_file_reads_empty(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.missed.load(), [])
        self.path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        self.assertEqual(self.missed.load(), [])
        self.path.write_text(json.dumps({"version": 1, "lines": [{"id": "a", "text": "ok", "queued_at": T0}, "junk"]}), encoding="utf-8")
        self.assertEqual([m.id for m in self.missed.load()], ["a"])

    def test_writes_are_atomic_and_concurrent_mutations_do_not_lose_lines(self):
        def add(i: int) -> None:
            self.missed.upsert(MissedLine(id=f"t{i}", text=str(i), queued_at=T0 + i))

        threads = [threading.Thread(target=add, args=(i,)) for i in range(25)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.missed.load()), 25)
        self.assertFalse(self.path.with_name("missed.json.tmp").exists())

    def test_default_path_honours_env_then_home(self):
        prev = os.environ.get(ms.MISSED_FILE_ENV)
        try:
            os.environ[ms.MISSED_FILE_ENV] = str(self.path)
            self.assertEqual(MissedList().path, self.path)
            os.environ.pop(ms.MISSED_FILE_ENV)
            self.assertEqual(MissedList().path, Path.home() / ".local" / "state" / "auto-speech" / "missed.json")
        finally:
            if prev is None:
                os.environ.pop(ms.MISSED_FILE_ENV, None)
            else:
                os.environ[ms.MISSED_FILE_ENV] = prev


def _lines() -> list[MissedLine]:
    return [
        MissedLine(id="a", text="First line.", queued_at=T0, status=MISSED, reason=REASON_LONGEST_WAIT, waited_seconds=180, held_for=("ChatGPT (pid 9)",)),
        MissedLine(id="b", text="Second line.", queued_at=T0 + 60, status=MISSED, reason=REASON_STALE, waited_seconds=191),
        MissedLine(id="c", text="Third line.", queued_at=T0 + 120, status=MISSED, reason=REASON_INTERRUPTED, waited_seconds=12, held_for=("Music",)),
        MissedLine(id="d", text="", queued_at=T0 + 180, status=MISSED, reason=REASON_RESTART, source_hash="c" * 64),
        MissedLine(id="e", text="Held line.", queued_at=T0 + 240, status=HELD, held_for=("ChatGPT (pid 9)",)),
    ]


class TestFormat(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(format_lines([]), "missed speech: nothing missed, nothing held")

    def test_every_reason_reads_as_a_sentence_oldest_first(self):
        text = format_lines(_lines(), now=T0 + 300)
        self.assertTrue(text.startswith("missed speech: 5 lines you have not heard (1 held, 4 missed), oldest first"))
        self.assertIn("missed: longest wait (180 s) while ChatGPT (pid 9) played", text)
        self.assertIn("missed: stale backlog (191 s old when its turn came)", text)
        self.assertIn("missed: interrupted after 12 s held", text)
        self.assertIn("missed: the daemon restarted while it was held", text)
        self.assertIn("(cached audio cccccccccccc)", text)
        self.assertIn("held 60 s so far while ChatGPT (pid 9) plays", text)
        order = [text.index(t) for t in ("First line.", "Second line.", "Third line.", "cached audio", "Held line.")]
        self.assertEqual(order, sorted(order))
        self.assertIn("replay the missed lines, oldest first: auto-speech missed --play", text)
        self.assertIn("1 held line will play by themselves when the sound stops", text)
        self.assertIn("forget all of them: auto-speech missed --clear", text)

    def test_a_meeting_line_reads_as_a_sentence_and_is_counted(self):
        from missed_speech import REASON_MEETING

        lst = MissedList(Path(tempfile.mkdtemp(prefix="auto-speech-missed-")) / "missed.json", clock=lambda: T0 + 100)
        lst.miss(MissedLine(id="m1", text="During the call.", queued_at=T0), reason=REASON_MEETING, held_for=("MSTeams (pid 1)",))
        lst.miss(MissedLine(id="m2", text="Music line.", queued_at=T0 + 1), reason=REASON_LONGEST_WAIT, waited_seconds=180)
        text = format_lines(lst.load(), now=T0 + 200)
        self.assertIn("missed: in a meeting (MSTeams (pid 1))", text)
        self.assertEqual(lst.counts(), {"held": 0, "missed": 2, "meeting": 1})
        self.assertEqual([m.id for m in lst.missed_in_meeting()], ["m1"])
        self.assertEqual(lst.missed_in_meeting(since=T0 + 101), [])
        self.assertEqual([m.id for m in lst.missed_in_meeting(since=T0 + 100)], ["m1"])

    def test_a_meeting_line_is_counted_by_one_summary_ever(self):
        # #m1021-1615-4497: every daemon start said the same "You missed 4
        # lines" again. The mark that a summary counted a line is in the file.
        from missed_speech import REASON_MEETING

        path = Path(tempfile.mkdtemp(prefix="auto-speech-missed-")) / "missed.json"
        lst = MissedList(path, clock=lambda: T0 + 100)
        lst.miss(MissedLine(id="m1", text="During the call.", queued_at=T0), reason=REASON_MEETING)
        lst.miss(MissedLine(id="m2", text="Music line.", queued_at=T0 + 1), reason=REASON_LONGEST_WAIT, waited_seconds=180)
        self.assertEqual([m.id for m in lst.take_meeting_summary()], ["m1"])
        self.assertEqual(lst.take_meeting_summary(), [])
        restarted = MissedList(path, clock=lambda: T0 + 200)
        self.assertEqual(restarted.take_meeting_summary(), [], "a new daemon on the same file says nothing again")
        restarted.miss(MissedLine(id="m3", text="Next call.", queued_at=T0 + 150), reason=REASON_MEETING)
        self.assertEqual([m.id for m in restarted.take_meeting_summary()], ["m3"], "the next meeting is counted alone")
        self.assertEqual([m.id for m in restarted.load()], ["m1", "m2", "m3"], "the lines stay for missed --play")

    def test_a_file_from_before_the_mark_loads_unannounced(self):
        m = MissedLine.from_dict({"id": "x", "text": "t", "queued_at": T0, "status": "missed", "reason": "meeting"})
        self.assertFalse(m.announced)
        self.assertTrue(MissedLine.from_dict({**m.to_dict(), "announced": True}).announced)

    def test_missed_lines_older_than_a_day_leave_the_list(self):
        from missed_speech import MAX_AGE_S

        path = Path(tempfile.mkdtemp(prefix="auto-speech-missed-")) / "missed.json"
        now = [T0]
        lst = MissedList(path, clock=lambda: now[0])
        lst.miss(MissedLine(id="old", text="Old.", queued_at=T0), reason=REASON_LONGEST_WAIT)
        lst.hold(MissedLine(id="held", text="Held.", queued_at=T0))
        now[0] = T0 + MAX_AGE_S - 10
        lst.miss(MissedLine(id="late", text="Asked long ago, missed now.", queued_at=T0 - 5), reason=REASON_STALE)
        now[0] = T0 + MAX_AGE_S + 1
        self.assertEqual([m.id for m in lst.load()], ["late", "held"], "old is gone; held lines never expire")
        self.assertEqual(lst.counts(), {"held": 1, "missed": 1, "meeting": 0})
        stored = json.loads(path.read_text())["lines"]
        self.assertEqual(len(stored), 3, "a read leaves the file alone")
        self.assertEqual(lst.expire(), 1)
        self.assertEqual(sorted(x["id"] for x in json.loads(path.read_text())["lines"]), ["held", "late"])
        self.assertEqual(lst.expire(), 0)
        self.assertIn("older than a day leave the list", format_lines(lst.load(), now=now[0]))
        self.assertEqual(MissedList(path.with_name("none.json")).expire(), 0)
        self.assertFalse(path.with_name("none.json").exists(), "expire writes no file that was not there")
        with self.assertRaises(ValueError):
            MissedList(path, max_age_s=0)

    def test_only_held_lines_offer_no_replay(self):
        text = format_lines([_lines()[-1]], now=T0 + 300)
        self.assertNotIn("--play", text)
        self.assertIn("1 held line will play", text)


class TestRunAction(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.missed = MissedList(Path(self.tmpdir.name) / "missed.json", clock=lambda: T0 + 300)  # the fixtures are dated T0

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_unknown_action(self):
        r = run_action("drop", missed=self.missed)
        self.assertFalse(r.ok)
        self.assertIn("expected list | play | clear", r.text)

    def test_list_is_the_default(self):
        self.assertEqual(run_action(None, missed=self.missed).text, "missed speech: nothing missed, nothing held")
        self.assertEqual(run_action("  LIST ", missed=self.missed).text, "missed speech: nothing missed, nothing first".replace("first", "held"))

    def test_play_sends_missed_lines_oldest_first_over_other_sound_and_skips_held(self):
        for line in _lines():
            self.missed.upsert(line)
        client = FakeClient()
        r = run_action("play", missed=self.missed, client=client)
        self.assertTrue(r.ok)
        self.assertIn("replaying 4 lines, oldest first, over any other sound", r.text)
        self.assertIn("1 held line will play by themselves", r.text)
        self.assertEqual([t for t, _ in client.speaks], ["First line.", "Second line.", "Third line."])
        for _, kw in client.speaks:
            self.assertIs(kw["hold"], False)
        self.assertEqual([kw["missed_id"] for _, kw in client.speaks], ["a", "b", "c"])
        self.assertEqual(client.plays, [("c" * 64, {"priority": 2, "missed_id": "d", "hold": False})])
        # Nothing is removed here: the daemon clears each line once it has played.
        self.assertEqual(len(self.missed.load()), 5)

    def test_play_with_nothing_missed(self):
        self.missed.upsert(_lines()[-1])
        client = FakeClient()
        r = run_action("play", missed=self.missed, client=client)
        self.assertTrue(r.ok)
        self.assertEqual(r.text, "missed speech: nothing to replay; 1 held line will play by themselves when the sound stops")
        self.assertEqual(client.speaks, [])
        self.assertEqual(run_action("play", missed=MissedList(Path(self.tmpdir.name) / "none.json"), client=client).text, "missed speech: nothing to replay")

    def test_play_drops_a_line_with_neither_text_nor_audio(self):
        self.missed.upsert(MissedLine(id="z", text="", queued_at=T0, status=MISSED))
        r = run_action("play", missed=self.missed, client=FakeClient())
        self.assertIn("replaying 0 lines", r.text)
        self.assertEqual(self.missed.load(), [])

    def test_play_reports_an_unreachable_daemon(self):
        self.missed.upsert(_lines()[0])
        r = run_action("play", missed=self.missed, client=FakeClient(fail=ConnectionError("no socket")))
        self.assertFalse(r.ok)
        self.assertIn("could not reach the auto-speech daemon (no socket); 0 line(s) queued before that", r.text)

    def test_clear(self):
        for line in _lines():
            self.missed.upsert(line)
        r = run_action("clear", missed=self.missed)
        self.assertEqual(r.text, "missed speech: cleared 5 lines")
        self.assertEqual(run_action("clear", missed=self.missed).text, "missed speech: cleared 0 lines")


if __name__ == "__main__":
    unittest.main(verbosity=1)
