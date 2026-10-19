"""Unit tests for sound_yield: settings, store, probe-free gate logic.

Everything here runs against a fake audio layer (FakeProbe + a canned
process table + a fake clock); Core Audio is never loaded. Run directly.
"""

from __future__ import annotations

import os
import sys
import tempfile
import tomllib
import unittest
from dataclasses import replace
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import sound_yield as sy  # noqa: E402
from sound_yield import (  # noqa: E402
    ACTIONS,
    DEFAULT_PLAYERS,
    run_action,
    ABORTED,
    CLEAR,
    DISABLED,
    EXPIRED,
    MODE_DEVICE_FALLBACK,
    MODE_DISABLED,
    MODE_PROCESS_LIST,
    MODE_UNAVAILABLE,
    RESUMED,
    UNAVAILABLE,
    AudioProcess,
    ProbeUnavailable,
    ProcessTable,
    SoundYieldGate,
    SoundYieldSettings,
    SoundYieldStore,
    format_assessment,
    format_settings,
)

OWN_PID = 4242
MUSIC = AudioProcess(pid=501, bundle_id="com.apple.Music")
SPOTIFY = AudioProcess(pid=502, bundle_id="com.spotify.client")
AFPLAY = AudioProcess(pid=503, bundle_id="")  # a CLI tool: no bundle id
OUR_MPV = AudioProcess(pid=777, bundle_id="")

TABLE = {
    OWN_PID: (1, "/Users/x/Developer/auto-speech/.venv/bin/python narrator_service.py"),
    501: (1, "/System/Applications/Music.app/Contents/MacOS/Music"),
    502: (1, "/Applications/Spotify.app/Contents/MacOS/Spotify"),
    503: (900, "afplay /tmp/song.wav"),
    900: (1, "-zsh"),
    777: (OWN_PID, "/opt/homebrew/bin/mpv --really-quiet /tmp/auto-speech-x/full.wav"),
    778: (779, "/opt/homebrew/bin/mpv --really-quiet /tmp/y.wav"),
    779: (1, "/Users/x/Developer/auto-speech/.venv/bin/python web_server.py"),
}


class FakeProbe:
    """A scripted audio layer: a list of snapshots served in order (the last
    one repeats), or ProbeUnavailable to force the device fallback."""

    def __init__(self, snapshots=None, *, process_list_available=True, device=None):
        self._snapshots = list(snapshots or [[]])
        self._i = 0
        self._list_ok = process_list_available
        self._device = list(device) if isinstance(device, (list, tuple)) else device
        self._device_i = 0
        self.list_calls = 0
        self.device_calls = 0

    def playing_processes(self):
        self.list_calls += 1
        if not self._list_ok:
            raise ProbeUnavailable("prs# unsupported")
        snap = self._snapshots[min(self._i, len(self._snapshots) - 1)]
        self._i += 1
        return list(snap)

    def device_running_somewhere(self):
        self.device_calls += 1
        if self._device is None:
            raise ProbeUnavailable("no device")
        if isinstance(self._device, list):
            v = self._device[min(self._device_i, len(self._device) - 1)]
            self._device_i += 1
            return v
        return self._device


class FakeTable:
    def __init__(self, table=None):
        self.table = dict(TABLE if table is None else table)
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        return dict(self.table)

    name_of = staticmethod(ProcessTable.name_of)


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps: list[float] = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def _store(tmp: Path, text: str | None = None) -> SoundYieldStore:
    p = tmp / "sound-yield.toml"
    if text is not None:
        p.write_text(text, encoding="utf-8")
    return SoundYieldStore(p)


def _gate(tmp, probe, *, table=None, config=None, mpv_pid=None, log=None):
    clock = FakeClock()
    mpv_path = tmp / "mpv.pid"
    if mpv_pid is not None:
        mpv_path.write_text(str(mpv_pid))
    gate = SoundYieldGate(
        store=_store(tmp, config),
        probe=probe,
        process_table=table or FakeTable(),
        own_pid=lambda: OWN_PID,
        mpv_pid_path=mpv_path,
        clock=clock.now,
        sleeper=clock.sleep,
        log=log or (lambda m: None),
    )
    return gate, clock


class TestSettings(unittest.TestCase):
    def test_defaults(self):
        s = SoundYieldSettings()
        self.assertTrue(s.enabled)
        self.assertEqual(s.exceptions, ())
        self.assertEqual(s.max_wait_seconds, 180.0)
        self.assertEqual(s.problems, ())

    def test_from_mapping_accepts_valid_values(self):
        s = SoundYieldSettings.from_mapping(
            {"enabled": False, "exceptions": ["Music", "com.spotify.client"], "max_wait_seconds": 30, "poll_seconds": 0.25}
        )
        self.assertFalse(s.enabled)
        self.assertEqual(s.exceptions, ("Music", "com.spotify.client"))
        self.assertEqual(s.max_wait_seconds, 30.0)
        self.assertEqual(s.poll_seconds, 0.25)
        self.assertEqual(s.problems, ())

    def test_from_mapping_bad_values_fall_back_and_are_reported(self):
        s = SoundYieldSettings.from_mapping(
            {"enabled": "yes", "exceptions": "Music", "max_wait_seconds": -1, "after_max_wait": "ignore", "poll_seconds": 0, "bogus": 1}
        )
        self.assertTrue(s.enabled)
        self.assertEqual(s.exceptions, ("Music",))  # a lone string is accepted as one entry
        self.assertEqual(s.max_wait_seconds, 180.0)
        self.assertEqual(s.poll_seconds, 0.5)
        joined = "\n".join(s.problems)
        for key in ("enabled", "max_wait_seconds", "after_max_wait", "poll_seconds", "bogus"):
            self.assertIn(key, joined)
        self.assertIn("after_max_wait is gone since 0.4.0", joined)  # the old drop/play switch is named, not just "unknown"

    def test_from_mapping_dedupes_exceptions_case_insensitively(self):
        s = SoundYieldSettings.from_mapping({"exceptions": ["Music", "music", " MUSIC ", "", 7]})
        self.assertEqual(s.exceptions, ("Music",))
        self.assertEqual(len(s.problems), 2)

    def test_is_exception_matches_bundle_id_last_component_and_name(self):
        s = SoundYieldSettings(exceptions=("music", "Spotify"))
        self.assertTrue(s.is_exception(AudioProcess(pid=1, bundle_id="com.apple.Music")))
        self.assertTrue(s.is_exception(AudioProcess(pid=1, bundle_id="", name="Spotify")))
        self.assertTrue(s.is_exception(AudioProcess(pid=1, bundle_id="COM.APPLE.MUSIC")))
        self.assertFalse(s.is_exception(AudioProcess(pid=1, bundle_id="com.google.Chrome", name="Google Chrome")))
        self.assertFalse(SoundYieldSettings().is_exception(MUSIC))

    def test_toml_round_trip(self):
        s = SoundYieldSettings(enabled=False, exceptions=('Mu"sic', "Spotify"), max_wait_seconds=42.5)
        import tomllib

        back = SoundYieldSettings.from_mapping(tomllib.loads(s.to_toml())["sound_yield"])
        self.assertEqual((back.enabled, back.exceptions, back.max_wait_seconds), (False, ('Mu"sic', "Spotify"), 42.5))
        self.assertNotIn("after_max_wait", s.to_toml())


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_missing_file_gives_defaults_without_path(self):
        s = _store(self.tmp).load()
        self.assertTrue(s.enabled)
        self.assertIsNone(s.config_path)

    def test_parse_error_gives_defaults_and_a_problem(self):
        s = _store(self.tmp, "[sound_yield\nenabled = ").load()
        self.assertTrue(s.enabled)
        self.assertEqual(len(s.problems), 1)
        self.assertIn("could not parse", s.problems[0])

    def test_set_enabled_add_remove_clear_write_the_same_file(self):
        st = _store(self.tmp)
        self.assertFalse(st.set_enabled(False).enabled)
        s, added = st.add_exceptions(["Music", "music", "Spotify", ""])
        self.assertEqual(added, ["Music", "Spotify"])
        self.assertEqual(s.exceptions, ("Music", "Spotify"))
        s, added = st.add_exceptions(["spotify"])
        self.assertEqual(added, [])
        s, removed = st.remove_exceptions(["MUSIC", "nope"])
        self.assertEqual(removed, ["Music"])
        self.assertEqual(s.exceptions, ("Spotify",))
        self.assertEqual(st.clear_exceptions().exceptions, ())
        reread = st.load()
        self.assertFalse(reread.enabled)
        self.assertEqual(reread.config_path, str(self.tmp / "sound-yield.toml"))
        self.assertFalse((self.tmp / "sound-yield.toml.tmp").exists())

    def test_env_override_picks_the_config_path(self):
        target = self.tmp / "elsewhere.toml"
        old = os.environ.get(sy.CONFIG_ENV)
        os.environ[sy.CONFIG_ENV] = str(target)
        try:
            self.assertEqual(SoundYieldStore().path, target)
        finally:
            if old is None:
                os.environ.pop(sy.CONFIG_ENV, None)
            else:
                os.environ[sy.CONFIG_ENV] = old


class TestProcessTable(unittest.TestCase):
    def test_snapshot_parses_ps_output_and_names(self):
        class R:
            stdout = " 12  1 /System/Applications/Music.app/Contents/MacOS/Music\n 13 12 afplay -v 1 /tmp/x.wav\nbad line\n"

        table = ProcessTable(runner=lambda *a, **k: R()).snapshot()
        self.assertEqual(table[12], (1, "/System/Applications/Music.app/Contents/MacOS/Music"))
        self.assertEqual(ProcessTable.name_of(table, 12), "Music")
        self.assertEqual(ProcessTable.name_of(table, 13), "afplay")
        self.assertEqual(ProcessTable.name_of(table, 99), "")

    def test_ps_failure_gives_empty_table(self):
        def boom(*a, **k):
            raise OSError("no ps")

        self.assertEqual(ProcessTable(runner=boom).snapshot(), {})


class TestAssess(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_nothing_playing_means_no_hold(self):
        gate, _ = _gate(self.tmp, FakeProbe([[]]))
        a = gate.assess()
        self.assertFalse(a.hold)
        self.assertEqual(a.mode, MODE_PROCESS_LIST)

    def test_another_app_playing_holds_and_is_named(self):
        gate, _ = _gate(self.tmp, FakeProbe([[MUSIC]]))
        a = gate.assess()
        self.assertTrue(a.hold)
        self.assertEqual([b.name for b in a.blockers], ["Music"])
        self.assertEqual(a.blockers[0].bundle_id, "com.apple.Music")

    def test_disabled_never_holds_and_never_probes(self):
        probe = FakeProbe([[MUSIC]])
        gate, _ = _gate(self.tmp, probe, config='[sound_yield]\nenabled = false\n')
        a = gate.assess()
        self.assertFalse(a.hold)
        self.assertEqual(a.mode, MODE_DISABLED)
        self.assertEqual(probe.list_calls, 0)

    def test_exception_list_lets_that_app_play_through(self):
        gate, _ = _gate(self.tmp, FakeProbe([[MUSIC, SPOTIFY]]), config='[sound_yield]\nexceptions = ["Music"]\n')
        a = gate.assess()
        self.assertTrue(a.hold)
        self.assertEqual([p.name for p in a.excepted], ["Music"])
        self.assertEqual([p.name for p in a.blockers], ["Spotify"])
        gate2, _ = _gate(self.tmp, FakeProbe([[MUSIC, SPOTIFY]]), config='[sound_yield]\nexceptions = ["com.apple.Music", "spotify"]\n')
        self.assertFalse(gate2.assess().hold)

    def test_own_playback_never_counts(self):
        # our own pid, the mpv in the pid file, a child mpv, and a sibling
        # auto-speech process's mpv (web server) are all ours
        snap = [AudioProcess(pid=OWN_PID), OUR_MPV, AudioProcess(pid=778)]
        gate, _ = _gate(self.tmp, FakeProbe([snap]), mpv_pid=777)
        a = gate.assess()
        self.assertFalse(a.hold)
        self.assertEqual(sorted(p.pid for p in a.own), [777, 778, OWN_PID])

    def test_mpv_pid_file_counts_even_without_a_process_table_entry(self):
        gate, _ = _gate(self.tmp, FakeProbe([[AudioProcess(pid=31337)]]), table=FakeTable({}), mpv_pid=31337)
        self.assertFalse(gate.assess().hold)

    def test_unknown_cli_tool_without_bundle_id_still_holds(self):
        gate, _ = _gate(self.tmp, FakeProbe([[AFPLAY]]))
        a = gate.assess()
        self.assertTrue(a.hold)
        self.assertEqual(a.blockers[0].name, "afplay")

    def test_process_list_unavailable_falls_back_to_device(self):
        gate, _ = _gate(self.tmp, FakeProbe(process_list_available=False, device=True))
        a = gate.assess()
        self.assertTrue(a.hold)
        self.assertEqual(a.mode, MODE_DEVICE_FALLBACK)
        self.assertIn("exceptions cannot apply", a.note)
        gate_idle, _ = _gate(self.tmp, FakeProbe(process_list_available=False, device=False))
        self.assertFalse(gate_idle.assess().hold)

    def test_device_fallback_ignores_our_own_live_mpv(self):
        gate, _ = _gate(self.tmp, FakeProbe(process_list_available=False, device=True), mpv_pid=os.getpid())
        a = gate.assess()
        self.assertFalse(a.hold)
        self.assertEqual(a.own[0].pid, os.getpid())

    def test_no_probe_at_all_fails_open_and_logs_once(self):
        logs: list[str] = []
        gate, _ = _gate(self.tmp, FakeProbe(process_list_available=False, device=None), log=logs.append)
        self.assertEqual(gate.assess().mode, MODE_UNAVAILABLE)
        self.assertEqual(gate.assess().mode, MODE_UNAVAILABLE)
        self.assertEqual(len(logs), 1)
        self.assertIn("will not hold", logs[0])

    def test_one_ps_snapshot_per_assessment_and_none_when_idle(self):
        table = FakeTable()
        gate, _ = _gate(self.tmp, FakeProbe([[MUSIC, SPOTIFY]]), table=table)
        gate.assess()
        self.assertEqual(table.calls, 1)
        gate_idle, _ = _gate(self.tmp, FakeProbe([[]]), table=table)
        gate_idle.assess()
        self.assertEqual(table.calls, 1)


class TestWaitUntilClear(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_clear_when_nothing_plays(self):
        gate, clock = _gate(self.tmp, FakeProbe([[]]))
        out = gate.wait_until_clear()
        self.assertEqual(out.status, CLEAR)
        self.assertTrue(out.may_play)
        self.assertEqual(clock.sleeps, [])

    def test_holds_then_resumes_when_the_other_sound_stops(self):
        logs: list[str] = []
        gate, clock = _gate(self.tmp, FakeProbe([[MUSIC], [MUSIC], [MUSIC], []]), log=logs.append)
        out = gate.wait_until_clear()
        self.assertEqual(out.status, RESUMED)
        self.assertTrue(out.may_play)
        self.assertEqual(len(clock.sleeps), 3)
        self.assertAlmostEqual(out.waited_seconds, 1.5)
        self.assertEqual(out.blockers, ("Music (com.apple.Music) (pid 501)",))
        self.assertIn("holding speech while Music", logs[0])
        self.assertIn("other sound stopped", logs[-1])

    def test_expires_as_missed_after_the_longest_wait_and_never_plays(self):
        logs: list[str] = []
        gate, clock = _gate(self.tmp, FakeProbe([[MUSIC]]), config='[sound_yield]\nmax_wait_seconds = 2\n', log=logs.append)
        out = gate.wait_until_clear()
        self.assertEqual(out.status, EXPIRED)
        self.assertFalse(out.may_play)
        self.assertGreaterEqual(out.waited_seconds, 2.0)
        self.assertEqual(sum(clock.sleeps), 2.0)
        self.assertIn("then set aside as missed", logs[0])
        self.assertIn("setting this speech aside as missed", logs[-1])

    def test_time_already_spent_in_the_queue_counts_against_the_longest_wait(self):
        logs: list[str] = []
        gate, clock = _gate(self.tmp, FakeProbe([[MUSIC]]), config='[sound_yield]\nmax_wait_seconds = 10\n', log=logs.append)
        out = gate.wait_until_clear(already_waited=8.5)
        self.assertEqual(out.status, EXPIRED)
        self.assertAlmostEqual(sum(clock.sleeps), 1.5)  # only the remaining budget is spent
        self.assertIn("up to 1.5s more, 8s of it already spent in the queue", logs[0])
        gate2, clock2 = _gate(self.tmp, FakeProbe([[MUSIC]]), config='[sound_yield]\nmax_wait_seconds = 10\n')
        self.assertEqual(gate2.wait_until_clear(already_waited=30).status, EXPIRED)
        self.assertEqual(clock2.sleeps, [])  # already past the longest wait: set aside at once

    def test_zero_max_wait_sets_aside_immediately_without_sleeping(self):
        gate, clock = _gate(self.tmp, FakeProbe([[MUSIC]]), config='[sound_yield]\nmax_wait_seconds = 0\n')
        self.assertEqual(gate.wait_until_clear().status, EXPIRED)
        self.assertEqual(clock.sleeps, [])

    def test_on_hold_is_called_once_with_the_blockers_when_a_hold_begins(self):
        seen: list[tuple[str, ...]] = []
        gate, _ = _gate(self.tmp, FakeProbe([[MUSIC], [MUSIC], []]))
        out = gate.wait_until_clear(on_hold=seen.append)
        self.assertEqual(out.status, RESUMED)
        self.assertEqual(seen, [("Music (com.apple.Music) (pid 501)",)])
        gate_idle, _ = _gate(self.tmp, FakeProbe([[]]))
        seen.clear()
        gate_idle.wait_until_clear(on_hold=seen.append)
        self.assertEqual(seen, [])  # no hold, no call

    def test_abort_releases_the_hold(self):
        gate, clock = _gate(self.tmp, FakeProbe([[MUSIC]]))
        polls = {"n": 0}

        def should_abort():
            polls["n"] += 1
            return polls["n"] >= 3

        out = gate.wait_until_clear(should_abort=should_abort)
        self.assertEqual(out.status, ABORTED)
        self.assertFalse(out.may_play)
        self.assertEqual(len(clock.sleeps), 2)

    def test_switching_off_mid_hold_releases_it(self):
        store_path = self.tmp / "sound-yield.toml"
        gate, clock = _gate(self.tmp, FakeProbe([[MUSIC]]))
        original_sleep = clock.sleep

        def sleep_then_switch_off(s):
            original_sleep(s)
            if len(clock.sleeps) == 2:
                store_path.write_text("[sound_yield]\nenabled = false\n")

        gate._sleep = sleep_then_switch_off
        out = gate.wait_until_clear()
        self.assertEqual(out.status, DISABLED)
        self.assertTrue(out.may_play)
        self.assertEqual(len(clock.sleeps), 2)

    def test_adding_an_exception_mid_hold_releases_it(self):
        store_path = self.tmp / "sound-yield.toml"
        gate, clock = _gate(self.tmp, FakeProbe([[MUSIC]]))
        original_sleep = clock.sleep

        def sleep_then_except(s):
            original_sleep(s)
            if len(clock.sleeps) == 1:
                store_path.write_text('[sound_yield]\nexceptions = ["Music"]\n')

        gate._sleep = sleep_then_except
        self.assertEqual(gate.wait_until_clear().status, RESUMED)

    def test_disabled_and_unavailable_short_circuit(self):
        gate, _ = _gate(self.tmp, FakeProbe([[MUSIC]]), config="[sound_yield]\nenabled = false\n")
        self.assertEqual(gate.wait_until_clear().status, DISABLED)
        gate2, _ = _gate(self.tmp, FakeProbe(process_list_available=False, device=None), config="[sound_yield]\nenabled = true\n")
        self.assertEqual(gate2.wait_until_clear().status, UNAVAILABLE)

    def test_device_fallback_hold_resumes_when_device_goes_quiet(self):
        gate, clock = _gate(self.tmp, FakeProbe(process_list_available=False, device=[True, True, False]))
        out = gate.wait_until_clear()
        self.assertEqual(out.status, RESUMED)
        self.assertEqual(len(clock.sleeps), 2)


class TestFormatting(unittest.TestCase):
    def test_format_settings_lines(self):
        text = format_settings(SoundYieldSettings(exceptions=("Music",), config_path="/x/sound-yield.toml", problems=("[sound_yield] unknown key 'q' (ignored)",)))
        self.assertIn("sound yield: ON", text)
        self.assertIn("exceptions (apps whose sound does not hold speech): Music", text)
        self.assertIn("longest hold: 180 s, then set aside as missed (auto-speech missed)", text)
        self.assertIn("config: /x/sound-yield.toml", text)
        self.assertIn("warning: [sound_yield] unknown key", text)
        self.assertIn("sound yield: OFF", format_settings(SoundYieldSettings(enabled=False)))

    def test_format_assessment_lines(self):
        tmp = Path(tempfile.mkdtemp())
        gate, _ = _gate(tmp, FakeProbe([[MUSIC, SPOTIFY, OUR_MPV]]), config='[sound_yield]\nexceptions = ["Spotify"]\n', mpv_pid=777)
        text = format_assessment(gate.assess())
        self.assertIn("playing: Music (com.apple.Music) (pid 501) — holds speech", text)
        self.assertIn("playing: Spotify (com.spotify.client) (pid 502) — on the exception list", text)
        self.assertIn("playing: mpv (pid 777) — auto-speech's own", text)
        self.assertIn("speech would HOLD (up to 180 s, then set aside as missed)", text)
        self.assertIn("detection: Core Audio process list", text)
        gate_idle, _ = _gate(tmp, FakeProbe([[]]))
        self.assertIn("nothing else is playing sound; speech would play now", format_assessment(gate_idle.assess()))
        gate_off, _ = _gate(tmp, FakeProbe([[MUSIC]]), config="[sound_yield]\nenabled = false\n")
        self.assertIn("sound yield is OFF", format_assessment(gate_off.assess()))


ANTIGRAVITY = AudioProcess(pid=2856, bundle_id="com.google.antigravity.helper")  # an editor's helper: stream open, silent
CHROME_HELPER = AudioProcess(pid=68655, bundle_id="com.google.Chrome.helper")  # Chrome plays through its helper
TABLE[2856] = (1, "/Applications/Antigravity.app/Contents/Frameworks/Antigravity Helper.app/Contents/MacOS/Antigravity Helper")
TABLE[68655] = (1, "/Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper")


class TestPlayersList(unittest.TestCase):
    """The Desk's call of 2026-10-18 19:45 (ADR-017): an open stream holds
    speech only when its app is on the players list. Core Audio can say that
    a process is running output, not whether that output is silent, and an
    editor's helper ran a silent stream for an afternoon and held every line."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)

    def test_defaults_hold_only_for_players(self):
        s = SoundYieldSettings()
        self.assertEqual(s.hold_for, "players")
        self.assertEqual(s.players, DEFAULT_PLAYERS)
        self.assertIn("com.apple.Music", DEFAULT_PLAYERS)
        self.assertIn("com.google.Chrome", DEFAULT_PLAYERS)

    def test_unknown_app_with_an_open_stream_does_not_hold(self):
        gate, _ = _gate(self.tmp, FakeProbe([[ANTIGRAVITY]]))
        a = gate.assess()
        self.assertFalse(a.hold)
        self.assertEqual([p.pid for p in a.others], [2856])
        self.assertEqual(a.blockers, [])
        self.assertEqual(gate.wait_until_clear().status, CLEAR)

    def test_players_hold_and_a_helper_matches_its_app_by_prefix(self):
        gate, _ = _gate(self.tmp, FakeProbe([[MUSIC, CHROME_HELPER, ANTIGRAVITY]]))
        a = gate.assess()
        self.assertTrue(a.hold)
        self.assertEqual(sorted(p.pid for p in a.blockers), [501, 68655])
        self.assertEqual([p.pid for p in a.others], [2856])

    def test_an_app_he_adds_becomes_a_player(self):
        store = _store(self.tmp)
        settings, added = store.add_players(["com.google.antigravity", "music"])
        self.assertEqual(added, ["com.google.antigravity"])  # Music is already a player
        gate, _ = _gate(self.tmp, FakeProbe([[ANTIGRAVITY]]))
        self.assertTrue(gate.assess().hold)
        settings, removed = store.remove_players(["COM.GOOGLE.ANTIGRAVITY", "nope"])
        self.assertEqual(removed, ["com.google.antigravity"])
        self.assertFalse(gate.assess().hold)

    def test_a_default_player_can_be_removed_and_reset_restores_the_defaults(self):
        store = _store(self.tmp)
        settings, removed = store.remove_players(["Chrome"])
        self.assertEqual(removed, ["com.google.Chrome"])
        gate, _ = _gate(self.tmp, FakeProbe([[CHROME_HELPER]]))
        self.assertFalse(gate.assess().hold)
        self.assertEqual(store.reset_players().players, DEFAULT_PLAYERS)
        self.assertTrue(gate.assess().hold)

    def test_a_player_name_matches_by_last_component_and_process_name(self):
        s = SoundYieldSettings(players=("Music", "afplay"))
        self.assertTrue(s.is_player(MUSIC))
        self.assertTrue(s.is_player(replace(AFPLAY, name="afplay")))
        self.assertFalse(s.is_player(SPOTIFY))

    def test_exception_beats_the_players_list(self):
        gate, _ = _gate(self.tmp, FakeProbe([[CHROME_HELPER]]), config='[sound_yield]\nexceptions = ["com.google.Chrome"]\n')
        a = gate.assess()
        self.assertFalse(a.hold)
        self.assertEqual([p.pid for p in a.excepted], [68655])

    def test_hold_for_any_restores_the_old_rule(self):
        gate, _ = _gate(self.tmp, FakeProbe([[ANTIGRAVITY]]), config='[sound_yield]\nhold_for = "any"\n')
        a = gate.assess()
        self.assertTrue(a.hold)
        self.assertEqual([p.pid for p in a.blockers], [2856])
        store = _store(self.tmp)
        self.assertEqual(store.set_hold_for("players").hold_for, "players")
        self.assertFalse(gate.assess().hold)

    def test_bad_hold_for_and_players_fall_back_and_are_reported(self):
        s = SoundYieldSettings.from_mapping({"hold_for": "some", "players": "Music"}, config_path="x")
        self.assertEqual(s.hold_for, "players")
        self.assertEqual(s.players, ("Music",))  # one string is one entry, as for exceptions
        self.assertTrue(any("hold_for" in p for p in s.problems))
        s2 = SoundYieldSettings.from_mapping({"players": 7}, config_path="x")
        self.assertEqual(s2.players, DEFAULT_PLAYERS)
        self.assertTrue(any("players" in p for p in s2.problems))

    def test_toml_round_trip_keeps_hold_for_and_players(self):
        s = SoundYieldSettings(hold_for="any", players=("Music", "com.google.Chrome"))
        back = SoundYieldSettings.from_mapping(tomllib.loads(s.to_toml())["sound_yield"])
        self.assertEqual(back.hold_for, "any")
        self.assertEqual(back.players, ("Music", "com.google.Chrome"))

    def test_status_and_check_say_the_rule_and_who_is_ignored(self):
        text = format_settings(SoundYieldSettings())
        self.assertIn("holds for: players only", text)
        self.assertIn("players (apps whose sound holds speech): com.apple.Music", text)
        gate, _ = _gate(self.tmp, FakeProbe([[ANTIGRAVITY, MUSIC]]))
        check = format_assessment(gate.assess())
        self.assertIn("Antigravity (com.google.antigravity.helper) (pid 2856) — not on the players list, does not hold", check)
        self.assertIn("Music (com.apple.Music) (pid 501) — holds speech", check)
        self.assertIn("speech would HOLD", check)
        self.assertIn("holds for: any app", format_settings(SoundYieldSettings(hold_for="any")))

    def test_run_action_player_verbs_share_the_file(self):
        store = _store(self.tmp)
        r = run_action("player-add", ["Antigravity"], store=store)
        self.assertTrue(r.ok)
        self.assertIn("players added: Antigravity", r.text)
        self.assertIn("Antigravity", store.load().players)
        r = run_action("player-remove", ["antigravity"], store=store)
        self.assertIn("players removed: Antigravity", r.text)
        r = run_action("player-reset", store=store)
        self.assertIn("players reset to the defaults", r.text)
        r = run_action("hold-for", ["any"], store=store)
        self.assertTrue(r.ok)
        self.assertEqual(store.load().hold_for, "any")
        self.assertFalse(run_action("hold-for", ["sometimes"], store=store).ok)
        self.assertFalse(run_action("player-add", [], store=store).ok)
        self.assertIn("player-add", " ".join(ACTIONS))

    def test_device_fallback_still_holds_and_says_the_list_cannot_apply(self):
        gate, _ = _gate(self.tmp, FakeProbe(process_list_available=False, device=True))
        a = gate.assess()
        self.assertTrue(a.hold)
        self.assertIn("players list cannot apply", format_assessment(a))


if __name__ == "__main__":
    unittest.main(verbosity=1)
