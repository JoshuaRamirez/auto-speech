"""Unit tests for speech_history and speech_player: the settings file, the
word-timing estimate, the export and its last-N pruning, the shared
CLI/MCP verb, and the player launcher. No daemon, no audio: WAVs are
written with the wave module into a temp dir. Run directly."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import speech_history as sh  # noqa: E402
from speech_history import (  # noqa: E402
    DEFAULT_KEEP,
    HistorySettings,
    HistoryStore,
    SpeechHistory,
    estimate_word_timings,
    run_action,
)
import speech_player as sp  # noqa: E402
from speech_player import PlayerLauncher  # noqa: E402

T0 = 1_760_000_000.0


def write_wav(path: Path, seconds: float, rate: int = 24000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return path


class Clock:
    def __init__(self, t: float = T0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class _Tmp(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="auto-speech-history-")
        self.root = Path(self.tmpdir.name)
        self.store = HistoryStore(self.root / "history.toml")
        self.clock = Clock()
        self.history = SpeechHistory(self.root / "history", store=self.store, clock=self.clock)
        self.wav = write_wav(self.root / "src" / "line.wav", 2.0)

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def export(self, text: str = "Hello there.", **kw):
        line = self.history.export(self.wav, text=text, **kw)
        self.clock.t += 1.0
        return line


class TestSettings(_Tmp):
    def test_defaults_without_a_file(self):
        s = self.store.load()
        self.assertEqual((s.enabled, s.keep, s.player), (True, DEFAULT_KEEP, False))
        self.assertEqual(DEFAULT_KEEP, 200)

    def test_round_trip_through_the_file(self):
        self.store.save(HistorySettings(enabled=False, keep=12, player=True))
        s = self.store.load()
        self.assertEqual((s.enabled, s.keep, s.player), (False, 12, True))
        self.assertEqual(s.config_path, str(self.store.path))

    def test_bad_values_fall_back_with_a_warning(self):
        s = HistorySettings.from_mapping({"enabled": "yes", "keep": 0, "player": 1}, config_path="x")
        self.assertEqual((s.enabled, s.keep, s.player), (True, DEFAULT_KEEP, False))
        self.assertEqual(len(s.problems), 3)
        self.assertEqual(HistorySettings.from_mapping({"keep": True}).keep, DEFAULT_KEEP)

    def test_an_unparsable_file_uses_defaults(self):
        self.store.path.write_text("[history\nkeep = ", encoding="utf-8")
        s = self.store.load()
        self.assertEqual(s.keep, DEFAULT_KEEP)
        self.assertTrue(s.problems)

    def test_config_validation_knows_the_section(self):
        from config_validation import validate_user_configs

        (self.root / "history.toml").write_text("[history]\nkeep = 0\ncolour = 1\n", encoding="utf-8")
        problems = validate_user_configs(self.root)
        self.assertIn("[history] unknown key 'colour' (ignored)", problems)
        self.assertIn("[history] keep must be >= 1, got 0", problems)


class TestWordTimings(unittest.TestCase):
    def test_spans_are_in_order_and_fill_the_clip(self):
        words = estimate_word_timings("The build is green, and the tests pass. Done.", 4.0)
        self.assertEqual([w["w"] for w in words], ["The", "build", "is", "green,", "and", "the", "tests", "pass.", "Done."])
        for a, b in zip(words, words[1:]):
            self.assertLessEqual(a["start"], a["end"])
            self.assertLessEqual(a["end"], b["start"])
        self.assertEqual(words[0]["start"], 0.0)
        self.assertEqual(words[-1]["end"], 4.0)

    def test_a_sentence_end_pauses_longer_than_a_comma(self):
        words = estimate_word_timings("aaaa, bbbb. cccc dddd", 10.0)
        comma_gap = words[1]["start"] - words[0]["end"]
        stop_gap = words[2]["start"] - words[1]["end"]
        plain_gap = words[3]["start"] - words[2]["end"]
        self.assertGreater(stop_gap, comma_gap)
        self.assertGreater(comma_gap, 0.0)
        self.assertAlmostEqual(plain_gap, 0.0, places=3)

    def test_longer_words_take_longer(self):
        words = estimate_word_timings("a extraordinarily", 3.0)
        self.assertGreater(words[1]["end"] - words[1]["start"], words[0]["end"] - words[0]["start"])

    def test_nothing_to_time(self):
        self.assertEqual(estimate_word_timings("", 3.0), [])
        self.assertEqual(estimate_word_timings("words here", 0.0), [])


class TestExport(_Tmp):
    def test_writes_the_wav_and_a_full_sidecar(self):
        line = self.export(
            "Master Desk here, the build is green.",
            session_id="sess-1",
            sender="Master Desk",
            priority=3,
            voice_id="Ava",
            speed=1.0,
            source_hash="a" * 64,
        )
        self.assertTrue(line.wav.is_file())
        self.assertEqual(line.wav.read_bytes(), self.wav.read_bytes())
        sc = json.loads(line.json.read_text(encoding="utf-8"))
        self.assertEqual(sc["version"], 1)
        self.assertEqual(sc["text"], "Master Desk here, the build is green.")
        self.assertEqual(sc["sender"], {"session_id": "sess-1", "name": "Master Desk"})
        self.assertEqual((sc["priority"], sc["priority_name"]), (3, "autoplay"))
        self.assertEqual(sc["duration_seconds"], 2.0)
        self.assertEqual(sc["spoken_at_epoch"], T0)
        self.assertTrue(sc["spoken_at"].startswith(time.strftime("%Y-%m-%dT", time.localtime(T0))))
        self.assertEqual(sc["wav"], line.wav.name)
        self.assertEqual(sc["word_timings"]["source"], "estimate")
        self.assertIn("method", sc["word_timings"])
        self.assertEqual(len(sc["word_timings"]["words"]), 7)
        self.assertEqual(sc["word_timings"]["words"][-1]["end"], 2.0)
        self.assertEqual(sc["source_hash"], "a" * 64)

    def test_synthesizer_timings_are_kept_as_given(self):
        given = [{"w": "Hi", "start": 0.1, "end": 0.4}]
        sc = self.export("Hi", word_timings=given).sidecar
        self.assertEqual(sc["word_timings"], {"source": "synthesizer", "words": given})

    def test_off_exports_nothing(self):
        self.store.set_enabled(False)
        self.assertIsNone(self.export())
        self.assertEqual(self.history.load(), [])

    def test_lines_load_oldest_first_and_get_by_id(self):
        a, b = self.export("one"), self.export("two")
        self.assertEqual([x.text for x in self.history.load()], ["one", "two"])
        self.assertEqual(self.history.get(b.id).text, "two")
        self.assertEqual(self.history.get(a.stem).text, "one")
        self.assertIsNone(self.history.get("nope"))

    def test_only_the_last_keep_lines_stay(self):
        self.store.set_keep(3)
        for i in range(5):
            self.export(f"line {i}")
        kept = self.history.load()
        self.assertEqual([x.text for x in kept], ["line 2", "line 3", "line 4"])
        self.assertEqual(len(list(self.history.directory.glob("*.wav"))), 3)

    def test_a_wav_without_its_sidecar_is_pruned_once_old(self):
        self.export("kept")
        young = write_wav(self.history.directory / "20990101-000000-000-young.wav", 0.1)
        old = write_wav(self.history.directory / "20000101-000000-000-old.wav", 0.1)
        os.utime(old, (T0 - 3600, T0 - 3600))
        os.utime(young, (self.clock.t, self.clock.t))
        self.history.prune()
        self.assertFalse(old.exists())
        self.assertTrue(young.exists(), "an export may still be writing its sidecar")
        self.assertEqual([x.text for x in self.history.load()], ["kept"])

    def test_no_temp_files_are_left(self):
        self.export()
        self.assertEqual([p.name for p in self.history.directory.glob(".*.tmp")], [])


class TestRunAction(_Tmp):
    def run_(self, action, **kw):
        return run_action(action, store=self.store, history=self.history, **kw)

    def test_unknown_action(self):
        r = self.run_("shout")
        self.assertFalse(r.ok)
        self.assertIn("list | status | on | off | keep | player-on | player-off", r.text)

    def test_list_is_the_default_and_newest_first(self):
        self.assertEqual(self.run_(None).text, "speech history: nothing played yet")
        self.export("alpha line", sender="Master Desk")
        self.export("beta line")
        text = self.run_(None).text
        self.assertTrue(text.startswith("speech history: the last 2 of 2 lines, newest first"))
        self.assertLess(text.index("beta line"), text.index("alpha line"))
        self.assertIn("Master Desk (timings estimated)", text)
        self.assertIn(".wav", text)
        self.assertIn("the last 1 of 2 lines", self.run_("list", count=1).text)
        self.assertFalse(self.run_("list", count=0).ok)

    def test_list_as_json_hands_over_the_paths(self):
        line = self.export("hello")
        data = json.loads(self.run_("list", as_json=True).text)
        self.assertEqual(data["total"], 1)
        [got] = data["lines"]
        self.assertEqual(got["wav_path"], str(line.wav))
        self.assertEqual(got["sidecar_path"], str(line.json))
        self.assertEqual(got["text"], "hello")

    def test_status_on_off_player(self):
        text = self.run_("status").text
        self.assertTrue(text.startswith("speech history: on\nkeep: the last 200 lines\nheld now: 0 lines, 0.0 MB\nplayer after each line: off"))
        self.assertIn("speech history: off", self.run_("off").text)
        self.assertFalse(self.store.load().enabled)
        self.assertIn("speech history: on", self.run_("on").text)
        r = run_action("player-on", store=self.store, history=self.history, player_note=lambda s: "the UI Utility is not installed; nothing opens")
        self.assertIn("player after each line: on (the UI Utility is not installed; nothing opens)", r.text)
        self.assertTrue(self.store.load().player)
        self.assertIn("player after each line: off", self.run_("player-off").text)

    def test_keep_sets_n_and_prunes_at_once(self):
        for i in range(4):
            self.export(f"line {i}")
        r = self.run_("keep", count=2)
        self.assertTrue(r.ok)
        self.assertTrue(r.text.startswith("pruned the 2 oldest lines\nspeech history: on\nkeep: the last 2 lines"))
        self.assertEqual(self.store.load().keep, 2)
        self.assertEqual([x.text for x in self.history.load()], ["line 2", "line 3"])
        self.assertFalse(self.run_("keep").ok)
        self.assertFalse(self.run_("keep", count=0).ok)
        self.assertFalse(self.run_("keep", count=True).ok)


class TestPlayerLauncher(_Tmp):
    def launcher(self, *, holding=lambda: False, installed=lambda: False, log=None):
        return PlayerLauncher(self.store, holding=holding, installed=installed, log=log)

    def test_off_by_default(self):
        line = self.export()
        self.assertEqual(self.launcher(installed=lambda: True).after_line(line), sp.OFF)
        self.assertEqual(self.launcher().note(), "")

    def test_never_during_a_hold(self):
        self.store.set_player(True)
        line = self.export()
        self.assertEqual(self.launcher(holding=lambda: True, installed=lambda: True).after_line(line), sp.HELD)

        def broken():
            raise RuntimeError("probe gone")

        self.assertEqual(self.launcher(holding=broken, installed=lambda: True).after_line(line), sp.HELD)

    def test_nothing_while_the_utility_is_not_installed(self):
        self.store.set_player(True)
        logs: list[str] = []
        launcher = self.launcher(log=logs.append)
        line = self.export()
        self.assertEqual(launcher.after_line(line), sp.NOT_INSTALLED)
        self.assertEqual(launcher.after_line(line), sp.NOT_INSTALLED)
        self.assertEqual(len(logs), 1, "said once, not per line")
        self.assertEqual(launcher.note(), "the UI Utility is not installed; nothing opens")

    def test_installed_but_not_wired_says_so(self):
        self.store.set_player(True)
        launcher = self.launcher(installed=lambda: True)
        self.assertEqual(launcher.after_line(self.export()), sp.CONTRACT_PENDING)
        self.assertIn("not wired in yet", launcher.note())


class TestCli(_Tmp):
    def test_history_subcommand_shares_the_verb(self):
        import argparse
        import contextlib
        import io

        import cli

        out = io.StringIO()
        self.export("from the cli")
        with contextlib.redirect_stdout(out):
            rc = cli.handle_history(argparse.Namespace(history_action="list", limit=None, json=False), store=self.store, history=self.history)
        self.assertEqual(rc, 0)
        self.assertIn("from the cli", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.handle_history(argparse.Namespace(history_action="keep", count=5), store=self.store, history=self.history)
        self.assertEqual(rc, 0)
        self.assertEqual(self.store.load().keep, 5)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = cli.handle_history(argparse.Namespace(history_action="keep", count=0), store=self.store, history=self.history)
        self.assertNotEqual(rc, 0)

    def test_parser_knows_the_verbs(self):
        import cli

        p = cli.build_parser()
        self.assertEqual(p.parse_args(["history", "keep", "50"]).count, 50)
        a = p.parse_args(["history", "list", "--limit", "3", "--json"])
        self.assertEqual((a.history_action, a.limit, a.json), ("list", 3, True))
        self.assertEqual(p.parse_args(["history", "player-on"]).history_action, "player-on")
        self.assertIsNone(p.parse_args(["history"]).history_action)


class TestDefaultsAreIsolatedUnderTheSuite(unittest.TestCase):
    def test_run_all_points_the_history_away_from_his(self):
        # run_all.sh sets both; a bare run must not reach ~/.local/state.
        if os.environ.get(sh.DIR_ENV):
            self.assertNotIn(str(Path.home() / ".local" / "state"), str(sh.default_history_dir()))
        if os.environ.get(sh.CONFIG_ENV):
            self.assertNotIn(str(Path.home() / ".config"), str(sh.default_config_path()))


if __name__ == "__main__":
    unittest.main(verbosity=1)
