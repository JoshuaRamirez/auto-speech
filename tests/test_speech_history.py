"""Unit tests for speech_history and speech_player: the settings file, the
word-timing estimate, the export and its last-N pruning, the shared
CLI/MCP verb, and the player launcher. No daemon, no audio: WAVs are
written with the wave module into a temp dir. Run directly."""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
for _p in (str(SRC), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import speech_history as sh  # noqa: E402
from speech_history import (  # noqa: E402
    BYTES_PER_GB,
    DEFAULT_KEEP,
    DEFAULT_MAX_GB,
    HistorySettings,
    HistoryStore,
    SpeechHistory,
    clean_origin_file,
    estimate_word_timings,
    run_action,
)
import speech_player as sp  # noqa: E402
from speech_player import PlayerLauncher  # noqa: E402
from ui_utility_client import UiUtilityClient  # noqa: E402
from ui_utility_standin import StandIn, default_script  # noqa: E402

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

    def _line_bytes(self) -> int:
        probe = SpeechHistory(self.root / "probe", store=self.store, clock=self.clock)
        line = probe.export(self.wav, text="line 0")
        return line.wav.stat().st_size + line.json.stat().st_size

    def export(self, text: str = "Hello there.", **kw):
        line = self.history.export(self.wav, text=text, **kw)
        self.clock.t += 1.0
        return line


class TestSettings(_Tmp):
    def test_defaults_without_a_file(self):
        s = self.store.load()
        self.assertEqual((s.enabled, s.max_gb, s.keep, s.player), (True, DEFAULT_MAX_GB, DEFAULT_KEEP, False))
        self.assertEqual((DEFAULT_MAX_GB, DEFAULT_KEEP), (20.0, 200))

    def test_round_trip_through_the_file(self):
        self.store.save(HistorySettings(enabled=False, max_gb=0.5, keep=12, player=True))
        s = self.store.load()
        self.assertEqual((s.enabled, s.max_gb, s.keep, s.player), (False, 0.5, 12, True))
        self.assertEqual(s.config_path, str(self.store.path))

    def test_bad_values_fall_back_with_a_warning(self):
        s = HistorySettings.from_mapping({"enabled": "yes", "max_gb": 0, "keep": 0, "player": 1}, config_path="x")
        self.assertEqual((s.enabled, s.max_gb, s.keep, s.player), (True, DEFAULT_MAX_GB, DEFAULT_KEEP, False))
        self.assertEqual(len(s.problems), 4)
        self.assertEqual(HistorySettings.from_mapping({"max_gb": "big"}).max_gb, DEFAULT_MAX_GB)
        self.assertEqual(HistorySettings.from_mapping({"keep": True}).keep, DEFAULT_KEEP)

    def test_an_unparsable_file_uses_defaults(self):
        self.store.path.write_text("[history\nkeep = ", encoding="utf-8")
        s = self.store.load()
        self.assertEqual(s.keep, DEFAULT_KEEP)
        self.assertTrue(s.problems)

    def test_config_validation_knows_the_section(self):
        from config_validation import validate_user_configs

        (self.root / "history.toml").write_text("[history]\nkeep = 0\nmax_gb = \"lots\"\ncolour = 1\n", encoding="utf-8")
        problems = validate_user_configs(self.root)
        self.assertIn("[history] max_gb should be a number, got 'lots'", problems)
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

    def test_say_commands_are_not_words_and_slnc_is_silence(self):
        # The desk's lines carry say markup (2026-10-21 08:53:18Z: "[[rate 180]] … [[slnc 600]] …").
        words = estimate_word_timings("[[rate 180]] aaaa bbbb. [[slnc 600]] cccc dddd", 4.0)
        self.assertEqual([w["w"] for w in words], ["aaaa", "bbbb.", "cccc", "dddd"])
        plain = estimate_word_timings("aaaa bbbb. cccc dddd", 4.0)
        gap = words[2]["start"] - words[1]["end"]
        plain_gap = plain[2]["start"] - plain[1]["end"]
        # The 600 ms comes out of the same 4 s, so the rest shrinks a little.
        self.assertGreater(gap, 0.6, "the 600 ms silence sits after bbbb.")
        self.assertGreater(gap, plain_gap)
        self.assertEqual(words[-1]["end"], 4.0)
        lead = estimate_word_timings("[[slnc 500]] aaaa bbbb", 2.0)
        self.assertEqual(lead[0]["start"], 0.5)
        glued = estimate_word_timings("aaaa[[rate 200]]bbbb", 2.0)
        self.assertEqual([w["w"] for w in glued], ["aaaa", "bbbb"])

    def test_silences_longer_than_the_clip_are_ignored(self):
        words = estimate_word_timings("aaaa [[slnc 9000]] bbbb", 2.0)
        self.assertEqual(words[0]["start"], 0.0)
        self.assertEqual(words[-1]["end"], 2.0)
        self.assertLess(words[1]["start"] - words[0]["end"], 0.01)

    def test_strip_say_markup(self):
        self.assertEqual(sh.strip_say_markup("[[rate 180]] The build is green. [[slnc 600]] Next."), "The build is green. Next.")
        self.assertEqual(sh.strip_say_markup("Line one.\n[[slnc 300]] Line two."), "Line one.\nLine two.")
        self.assertEqual(sh.strip_say_markup("No  markup\n\nkept as is "), "No  markup\n\nkept as is ")
        self.assertEqual(sh.strip_say_markup("[[ RATE 200 ]]"), "")
        self.assertEqual(sh.strip_say_markup("a [single] bracket stays"), "a [single] bracket stays")


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

    def test_the_desk_say_file_name_is_kept_and_the_threading_keys_stay(self):
        # Spoken Transcript (its ADR-006) threads on spoken_at_epoch and
        # sender.session_id, and matches a desk-say line by its file name.
        sc = self.export("Desk here.", session_id="sess-1", origin_file="2026-10-20-150900.674-desk.txt").sidecar
        self.assertEqual(sc["origin_file"], "2026-10-20-150900.674-desk.txt")
        self.assertEqual(sc["spoken_at_epoch"], T0)
        self.assertEqual(sc["sender"]["session_id"], "sess-1")
        sc = self.export("From a path.", origin_file="/x/Spoken/pending/2026-10-20-150901.000-desk.txt").sidecar
        self.assertEqual(sc["origin_file"], "2026-10-20-150901.000-desk.txt", "a path is cut to its name")
        self.assertIsNone(self.export("A direct speak call.").sidecar["origin_file"])

    def test_an_unusable_origin_file_is_dropped(self):
        for bad in (None, 7, "", "   ", "..", "/", "a\nb.txt", "x" * 256):
            self.assertEqual(clean_origin_file(bad), "", repr(bad))
        self.assertEqual(clean_origin_file(" a-desk.txt "), "a-desk.txt")

    def test_the_sidecar_text_is_as_heard_without_say_markup(self):
        sc = self.export("[[rate 180]] The build is green. [[slnc 600]] Tests pass.").sidecar
        self.assertEqual(sc["text"], "The build is green. Tests pass.")
        self.assertEqual([w["w"] for w in sc["word_timings"]["words"]], ["The", "build", "is", "green.", "Tests", "pass."])

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

    def test_the_store_is_bounded_by_size_oldest_first(self):
        per = self._line_bytes()
        self.store.set_max_gb(3.5 * per / BYTES_PER_GB)  # room for three lines, not four
        for i in range(5):
            self.export(f"line {i}")
        kept = self.history.load()
        self.assertEqual([x.text for x in kept], ["line 2", "line 3", "line 4"])
        self.assertEqual(len(list(self.history.directory.glob("*.wav"))), 3)
        self.assertLessEqual(self.history.size_bytes(), 3.5 * per)

    def test_keep_deletes_nothing(self):
        self.store.set_keep(1)
        for i in range(4):
            self.export(f"line {i}")
        self.assertEqual(self.history.count(), 4)
        self.assertEqual([x.text for x in self.history.load(limit=2)], ["line 2", "line 3"])

    def test_the_newest_line_stays_even_past_the_bound(self):
        self.store.set_max_gb(1 / BYTES_PER_GB)  # one byte
        self.export("old")
        self.export("new")
        self.assertEqual([x.text for x in self.history.load()], ["new"])

    def test_a_wav_without_its_sidecar_is_pruned_once_old(self):
        self.export("kept")
        young = write_wav(self.history.directory / "20990101-000000-000-young.wav", 0.1)
        old = write_wav(self.history.directory / "20000101-000000-000-old.wav", 0.1)
        os.utime(old, (T0 - 3600, T0 - 3600))
        os.utime(young, (self.clock.t, self.clock.t))
        self.history.prune()
        self.assertFalse(old.exists())
        self.assertTrue(young.exists(), "an export may still be writing its sidecar")
        tmp = self.history.directory / ".20000101-000000-000-x.wav.tmp"
        tmp.write_bytes(b"partial")
        os.utime(tmp, (T0 - 3600, T0 - 3600))
        self.history.prune()
        self.assertFalse(tmp.exists(), "a .tmp left by an export cut short goes too")
        self.assertEqual([x.text for x in self.history.load()], ["kept"])

    def test_names_sort_in_time_order_in_utc(self):
        line = self.export()
        self.assertTrue(line.stem.startswith(time.strftime("%Y%m%d-%H%M%S", time.gmtime(T0))), line.stem)

    def test_no_temp_files_are_left(self):
        self.export()
        self.assertEqual([p.name for p in self.history.directory.glob(".*.tmp")], [])


class TestRunAction(_Tmp):
    def run_(self, action, **kw):
        return run_action(action, store=self.store, history=self.history, **kw)

    def test_unknown_action(self):
        r = self.run_("shout")
        self.assertFalse(r.ok)
        self.assertIn("list | status | on | off | max-gb | keep | player-on | player-off", r.text)

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
        self.store.set_keep(1)
        self.assertIn("the last 1 of 2 lines", self.run_("list").text, "list shows the newest `keep` by default")
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
        self.assertTrue(text.startswith(
            "speech history: on\nmax_gb: 20 GB on disk, the oldest pruned first\nheld now: 0 lines, 0.0 MB\n"
            "keep: the player's view shows the newest 200 lines\nspeech control: off"
        ), text)
        self.assertIn("speech history: off", self.run_("off").text)
        self.assertFalse(self.store.load().enabled)
        self.assertIn("speech history: on", self.run_("on").text)
        r = run_action("player-on", store=self.store, history=self.history, player_note=lambda s: "the UI Utility is not installed; nothing opens")
        self.assertIn("speech control: on (the UI Utility is not installed; nothing opens)", r.text)
        self.assertTrue(self.store.load().player)
        self.assertIn("speech control: off", self.run_("player-off").text)

    def test_max_gb_bounds_the_store_and_prunes_at_once(self):
        per = self._line_bytes()
        for i in range(4):
            self.export(f"line {i}")
        r = self.run_("max-gb", gb=2.5 * per / BYTES_PER_GB)
        self.assertTrue(r.ok, r.text)
        self.assertTrue(r.text.startswith("pruned the 2 oldest lines\nspeech history: on\nmax_gb: "), r.text)
        self.assertEqual([x.text for x in self.history.load()], ["line 2", "line 3"])
        self.assertFalse(self.run_("max-gb").ok)
        self.assertFalse(self.run_("max-gb", gb=0).ok)
        self.assertFalse(self.run_("max-gb", gb=True).ok)

    def test_keep_sets_the_view_and_deletes_nothing(self):
        for i in range(4):
            self.export(f"line {i}")
        r = self.run_("keep", count=2)
        self.assertTrue(r.ok)
        self.assertIn("keep: the player's view shows the newest 2 lines", r.text)
        self.assertTrue(r.text.startswith("speech history: on\n"), "no pruned-lines head")
        self.assertEqual(self.store.load().keep, 2)
        self.assertEqual(self.history.count(), 4)
        self.assertFalse(self.run_("keep").ok)
        self.assertFalse(self.run_("keep", count=0).ok)
        self.assertFalse(self.run_("keep", count=True).ok)


class TestPlayerLauncher(_Tmp):
    """The launcher against a stand-in UI Utility on a private socket:
    never the real app, never a launch (specs/001-player-launch)."""

    def setUp(self) -> None:
        super().setUp()
        # /tmp, not $TMPDIR: a Unix socket path on macOS must fit in 104 bytes.
        self.sockdir = Path(tempfile.mkdtemp(prefix="as-pl-", dir="/tmp"))
        self.sock = self.sockdir / "ui.sock"
        self.standin: StandIn | None = None
        self.launches: list[int] = []
        self.launchers: list[PlayerLauncher] = []

    def tearDown(self) -> None:
        for launcher in self.launchers:
            launcher.close()
        if self.standin is not None:
            self.standin.stop()
        shutil.rmtree(self.sockdir, ignore_errors=True)
        super().tearDown()

    def serve(self, script=None) -> StandIn:
        self.standin = StandIn(self.sock, script=script).start()
        return self.standin

    def no_launch(self):
        self.launches.append(1)
        return None

    def launcher(self, *, holding=lambda: False, installed=lambda: True, log=None, launch=None,
                 start_worker=True, reply_timeout=1.0):
        launcher = PlayerLauncher(
            self.store,
            holding=holding,
            installed=installed,
            log=log,
            client_factory=lambda on_event: UiUtilityClient(self.sock, reply_timeout=reply_timeout, on_event=on_event),
            launch=launch or self.no_launch,
            outcome_file=self.root / "player.json",
            start_worker=start_worker,
        )
        self.launchers.append(launcher)
        return launcher

    def wait_outcome(self, launcher, want, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            last = launcher.last_outcome()
            if last is not None and last["outcome"] == want:
                return last
            time.sleep(0.01)
        self.fail(f"outcome {want!r} not reached in {timeout} s; last: {launcher.last_outcome()!r}")

    def requests(self):
        return [] if self.standin is None else list(self.standin.requests)

    # US2: off by default, silent during a hold, quiet when absent

    def test_off_by_default(self):
        self.serve()
        line = self.export()
        launcher = self.launcher()
        self.assertEqual(launcher.after_line(line), sp.OFF)
        self.assertEqual(launcher.after_line(None), sp.OFF)
        self.assertEqual(self.launcher(installed=lambda: False).note(), "")
        time.sleep(0.1)
        self.assertEqual((self.requests(), self.launches), ([], []))

    def test_never_during_a_hold(self):
        self.serve()
        self.store.set_player(True)
        line = self.export()
        self.assertEqual(self.launcher(holding=lambda: True).after_line(line), sp.HELD)

        def broken():
            raise RuntimeError("probe gone")

        self.assertEqual(self.launcher(holding=broken).after_line(line), sp.HELD)
        time.sleep(0.1)
        self.assertEqual((self.requests(), self.launches), ([], []))

    def test_nothing_while_the_utility_is_not_installed(self):
        self.serve()
        self.store.set_player(True)
        logs: list[str] = []
        launcher = self.launcher(installed=lambda: False, log=logs.append)
        line = self.export()
        self.assertEqual(launcher.after_line(line), sp.NOT_INSTALLED)
        self.assertEqual(launcher.after_line(line), sp.NOT_INSTALLED)
        self.assertEqual(len(logs), 1, "said once, not per line")
        self.assertEqual(launcher.note(), "the UI Utility is not installed; nothing opens")
        time.sleep(0.1)
        self.assertEqual((self.requests(), self.launches), ([], []))

    def test_a_hold_that_starts_while_the_line_waits_stops_it(self):
        self.serve()
        self.store.set_player(True)
        hold = [False]
        launcher = self.launcher(holding=lambda: hold[0], start_worker=False)
        line = self.export()
        self.assertEqual(launcher.after_line(line), sp.QUEUED)
        hold[0] = True
        self.assertEqual(launcher._hand_over(launcher._take(timeout=0)), sp.HELD)
        self.assertEqual(self.requests(), [])
        self.assertEqual(launcher.last_outcome()["outcome"], "held")

    # US1: the line just spoken appears in the player

    def test_the_line_just_heard_opens_in_the_one_panel_ready_to_play(self):
        self.serve()
        self.store.set_player(True)
        launcher = self.launcher()
        line = self.export("Hello there, the build is green.", sender="Master Desk")
        self.assertEqual(launcher.after_line(line), sp.QUEUED)
        last = self.wait_outcome(launcher, sp.OPENED)
        self.assertEqual(last["line_id"], line.id)
        self.assertEqual(self.standin.cmds(), ["hello", "open"])
        opened = self.standin.wait_for("open")[0]
        self.assertEqual(opened["panel"], "player")
        self.assertEqual(opened["id"], "auto-speech")
        self.assertEqual(opened["clip"], {"path": str(line.wav)})
        self.assertEqual(opened["text"], {"words": line.sidecar["word_timings"]["words"]})
        self.assertIs(opened["play"], False)
        self.assertTrue(opened["title"].startswith("Master Desk · "), opened["title"])

        second = self.export("And the next one.")
        self.assertEqual(launcher.after_line(second), sp.QUEUED)
        opens = self.standin.wait_for("open", n=2)
        self.assertEqual([o["clip"]["path"] for o in opens], [str(line.wav), str(second.wav)])
        self.assertEqual({o["id"] for o in opens}, {"auto-speech"})
        self.assertEqual(self.standin.cmds().count("hello"), 1, "one held connection, one hello")
        self.assertEqual(self.standin.connections, 1)
        self.assertEqual(self.launches, [])

    def test_a_utility_not_running_is_launched_then_opened(self):
        self.store.set_player(True)

        def launch():
            self.launches.append(1)
            self.serve()
            return ["open", "-g", "-a", "UI Utility"]

        launcher = self.launcher(launch=launch)
        self.assertEqual(launcher.after_line(self.export()), sp.QUEUED)
        self.wait_outcome(launcher, sp.OPENED)
        self.assertEqual(self.launches, [1])

    def test_launching_off_and_no_socket_says_so(self):
        self.store.set_player(True)
        logs: list[str] = []
        launcher = self.launcher(log=logs.append)
        self.assertEqual(launcher.after_line(self.export()), sp.QUEUED)
        last = self.wait_outcome(launcher, sp.NO_SOCKET)
        self.assertEqual(last["detail"], "launching is off")
        self.assertEqual(self.launches, [1])
        self.assertIn("last: no socket", launcher.note())

    def test_a_pruned_wav_is_skipped_and_empty_words_still_open(self):
        self.serve()
        self.store.set_player(True)
        launcher = self.launcher(start_worker=False)
        gone = self.export()
        gone.wav.unlink()
        self.assertEqual(launcher._hand_over(gone), sp.SKIPPED)
        self.assertEqual(self.requests(), [])
        bare = self.export()
        bare.sidecar["word_timings"]["words"] = []
        self.assertEqual(launcher._hand_over(bare), sp.OPENED)
        self.assertEqual(self.standin.wait_for("open")[0]["text"], {"words": []})

    # US3: the player never slows or stops speech

    def test_a_hung_utility_costs_the_speech_path_nothing(self):
        from ui_utility_standin import never

        self.serve(never)
        self.store.set_player(True)
        logs: list[str] = []
        launcher = self.launcher(log=logs.append, reply_timeout=0.3)
        settings = self.store.load()
        for i in range(5):
            line = self.export(f"line {i}")
            t0 = time.monotonic()
            self.assertEqual(launcher.after_line(line, settings), sp.QUEUED)
            self.assertLess(time.monotonic() - t0, 0.05)
        self.wait_outcome(launcher, sp.TIMEOUT)
        time.sleep(0.4)
        self.assertEqual(sum("timeout" in x for x in logs), 1, logs)

    def test_a_refusal_is_recorded_and_logged_once(self):
        def script(r):
            if r["cmd"] == "open":
                return {"ok": False, "error": "bad-value", "detail": "clip.path is not readable"}
            return default_script(r)

        self.serve(script)
        self.store.set_player(True)
        logs: list[str] = []
        launcher = self.launcher(log=logs.append, start_worker=False)
        for _ in range(3):
            self.assertEqual(launcher._hand_over(self.export()), sp.REFUSED)
        last = launcher.last_outcome()
        self.assertTrue(last["detail"].startswith("bad-value:"), last)
        self.assertEqual(len(logs), 1, logs)
        self.assertEqual(self.standin.cmds().count("hello"), 1, "a refusal keeps the connection")

    def test_the_newest_line_wins(self):
        self.serve()
        self.store.set_player(True)
        launcher = self.launcher(start_worker=False)
        lines = [self.export(f"line {i}") for i in range(3)]
        for line in lines:
            self.assertEqual(launcher.after_line(line), sp.QUEUED)
        taken = launcher._take(timeout=0)
        self.assertEqual(taken.id, lines[-1].id)
        self.assertIsNone(launcher._take(timeout=0))
        self.assertEqual(launcher._hand_over(taken), sp.OPENED)
        self.assertEqual([o["clip"]["path"] for o in self.standin.wait_for("open")], [str(lines[-1].wav)])

    def test_a_dropped_connection_reconnects_on_the_next_line(self):
        self.serve()
        self.store.set_player(True)
        launcher = self.launcher(start_worker=False)
        self.assertEqual(launcher._hand_over(self.export()), sp.OPENED)
        client = launcher._client
        self.standin.drop_connections()
        deadline = time.monotonic() + 2.0
        while client.connected and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(client.connected)
        self.assertEqual(launcher._hand_over(self.export()), sp.OPENED)
        self.assertEqual(self.standin.cmds(), ["hello", "open", "hello", "open"])

    def test_close_stops_the_worker(self):
        self.serve()
        self.store.set_player(True)
        launcher = self.launcher()
        launcher.after_line(self.export())
        self.wait_outcome(launcher, sp.OPENED)
        worker = launcher._worker
        launcher.close()
        launcher.close()
        worker.join(1.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(launcher.after_line(self.export()), sp.OFF)

    # US4: status

    def test_status_shows_the_last_outcome(self):
        self.store.set_player(True)
        launcher = self.launcher(start_worker=False)
        self.assertEqual(launcher.note(), "opens after each line")
        (self.root / "player.json").write_text(json.dumps(
            {"outcome": "refused", "detail": "bad-value: clip", "at": "2026-10-21T01:02:03", "line_id": "x"}))
        r = run_action("status", store=self.store, history=self.history, player_note=lambda s: launcher.note(s))
        self.assertIn("speech control: on (opens after each line; last: refused 01:02:03: bad-value: clip)", r.text)


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
        self.assertEqual(p.parse_args(["history", "max-gb", "2.5"]).gb, 2.5)
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
