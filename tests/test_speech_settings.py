"""Unit tests for speech_settings and the settings piece the speech control's
driver keeps open beside the control (contract v1 rev 1.3; ADR-023).
Against a stand-in UI Utility on a private socket that holds the settings
pieces it is sent: never the real app, never a launch. Every setting's file
or marker is in a temporary folder: never his. No audio. Run directly."""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
for _p in (str(SRC), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import speech_settings as ss
from missed_speech import REASON_LONGEST_WAIT, MissedLine, MissedList
from sound_yield import SoundYieldStore
from speaker import SpeakerStore
from speech_control import CONTROL_ID, ControlActions, ControlDriver
from speech_history import HistoryStore, SpeechHistory
from speech_settings import SETTINGS_ID, SettingsPlaces, SpeechSettings
from ui_utility_client import UiUtilityClient
from ui_utility_standin import SettingsPieces, StandIn, rev_1_2_script

SPEECH_ITEMS = ["mute", "autoplay_mode", "summary_size", "sound_yield", "rate", "speaker"]
TRANSCRIPT_ITEMS = ["show_list", "keep", "player", "clear_missed"]


class _Base(unittest.TestCase):
    script = None

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="auto-speech-settings-")
        self.root = Path(self.tmpdir.name)
        self.store = HistoryStore(self.root / "history.toml")
        self.missed = MissedList(self.root / "missed.json")
        self.history = SpeechHistory(self.root / "history", store=self.store)
        self.places = SettingsPlaces(
            mute_marker=self.root / "claude" / "auto-speech.disabled",
            autoplay_path=self.root / "autoplay.toml",
            autoplay_default=self.root / "autoplay.toml.example",
            sound_yield=SoundYieldStore(self.root / "sound-yield.toml"),
            speaker=SpeakerStore(self.root / "speaker.toml"),
            history=self.store,
            missed=self.missed,
        )
        self.rates: list[float] = []
        self.logs: list[str] = []
        self.pieces = SettingsPieces()
        # /tmp, not $TMPDIR: a Unix socket path on macOS must fit in 104 bytes.
        self.sockdir = Path(tempfile.mkdtemp(prefix="as-ss-", dir="/tmp"))
        self.sock = self.sockdir / "ui.sock"
        self.standin = StandIn(self.sock, script=self.script or self.pieces).start()
        self.drivers: list[ControlDriver] = []

    def tearDown(self) -> None:
        for d in self.drivers:
            d.close()
        self.standin.stop()
        shutil.rmtree(self.sockdir, ignore_errors=True)
        self.tmpdir.cleanup()

    def set_rate(self, rate: float) -> float:
        self.rates.append(rate)
        return rate

    def driver(self) -> ControlDriver:
        d = ControlDriver(
            self.store,
            history=self.history,
            missed=self.missed,
            actions=ControlActions(set_rate=self.set_rate),
            installed=lambda: True,
            log=self.logs.append,
            client_factory=lambda on_event: UiUtilityClient(self.sock, reply_timeout=2.0, on_event=on_event),
            launch=lambda: None,
            icon=None,
            start_worker=False,
            settings=SpeechSettings(self.places, set_rate=self.set_rate),
        )
        self.drivers.append(d)
        return d

    def opened(self) -> ControlDriver:
        self.store.set_player(True)
        d = self.driver()
        d.start()
        self.assertEqual(d.sync(), "opened")
        return d

    def requests(self, cmd: str, piece_id: str | None = None) -> list[dict]:
        return [r for r in self.standin.requests
                if r.get("cmd") == cmd and (piece_id is None or r.get("id") == piece_id)]

    def change(self, d: ControlDriver, item: str, value=True) -> dict:
        """The person changes an item: the event the window sends, handled as
        the worker would. Returns the `values` the driver sent back."""
        before = len(self.requests("values", SETTINGS_ID))
        d.handle_event({"event": "setting", "id": SETTINGS_ID, "item": item, "value": value, "by": "user"})
        sent = self.requests("values", SETTINGS_ID)[before:]
        return sent[-1]["values"] if sent else {}


class TestOpen(_Base):
    def test_it_opens_beside_the_control_with_both_groups_and_the_current_values(self):
        self.places.mute_marker.parent.mkdir(parents=True)
        self.places.mute_marker.touch()
        self.places.autoplay_path.write_text('[autoplay]\nmode = "verbatim"\nsummary_size = "large"\n')
        self.places.sound_yield.set_enabled(False)
        self.places.speaker.set_enabled(False)
        self.store.set_keep(120)
        self.store.set_show_list(True)
        self.store.set_rate(1.5)
        self.opened()
        [control] = self.requests("open", CONTROL_ID)
        [settings] = self.requests("open", SETTINGS_ID)
        self.assertEqual(control["show"], {"list": True})
        self.assertEqual(control["rate"], 1.5, "the kept rate, at start")
        self.assertEqual(self.rates, [1.5], "and applied to the sink")
        self.assertEqual((settings["panel"], settings["source"]["name"]), ("settings", "Speech"))
        self.assertEqual([g["id"] for g in settings["groups"]], ["speech", "transcript"])
        self.assertEqual([g["title"] for g in settings["groups"]], ["Speech", "Transcript"])
        self.assertEqual([i["id"] for i in settings["groups"][0]["items"]], SPEECH_ITEMS)
        self.assertEqual([i["id"] for i in settings["groups"][1]["items"]], TRANSCRIPT_ITEMS)
        self.assertEqual(self.pieces.values(SETTINGS_ID), {
            "mute": True, "autoplay_mode": "verbatim", "summary_size": "large", "sound_yield": False,
            "rate": 1.5, "speaker": False, "show_list": True, "keep": 120, "player": True,
            "clear_missed": None,
        })
        self.assertEqual(self.requests("open", CONTROL_ID)[0]["id"], CONTROL_ID, "the control keeps its id")

    def test_out_of_range_values_on_disk_are_shown_inside_the_items_range(self):
        self.store.set_keep(5)
        self.store.set_rate(3.0)
        self.opened()
        values = self.pieces.values(SETTINGS_ID)
        self.assertEqual((values["keep"], values["rate"]), (ss.KEEP_MIN, ss.RATE_MAX))

    def test_a_copy_without_the_settings_piece_still_gets_the_control(self):
        self.standin.script = rev_1_2_script
        self.opened()
        self.assertEqual(len(self.requests("open", CONTROL_ID)), 1)
        self.assertEqual(self.requests("open", SETTINGS_ID), [], "never asked of a copy that does not offer it")

    def test_off_opens_neither(self):
        d = self.driver()
        d.start()
        self.assertEqual(d.sync(), "off")
        self.assertEqual(self.standin.connections, 0)


class TestApply(_Base):
    def test_each_change_lands_where_the_setting_lives_and_is_confirmed(self):
        d = self.opened()
        self.assertEqual(self.change(d, "mute", True), {"mute": True})
        self.assertTrue(self.places.mute_marker.exists())
        self.assertEqual(self.change(d, "mute", False), {"mute": False})
        self.assertFalse(self.places.mute_marker.exists())

        self.assertEqual(self.change(d, "autoplay_mode", "verbatim"),
                         {"autoplay_mode": "verbatim", "summary_size": "small"})
        self.assertEqual(self.change(d, "summary_size", "medium"), {"summary_size": "medium"})
        doc = tomllib.loads(self.places.autoplay_path.read_text())
        self.assertEqual(doc["autoplay"], {"mode": "verbatim", "summary_size": "medium"})

        self.assertEqual(self.change(d, "sound_yield", False), {"sound_yield": False})
        self.assertFalse(self.places.sound_yield.load().enabled)
        self.assertEqual(self.change(d, "speaker", False), {"speaker": False})
        self.assertFalse(self.places.speaker.load().enabled)

        self.assertEqual(self.change(d, "rate", 1.25), {"rate": 1.25})
        self.assertEqual((self.store.load().rate, self.rates[-1]), (1.25, 1.25), "kept and applied now")
        self.assertEqual(self.requests("state", CONTROL_ID), [], "the control's rate is sent at the next sync")
        d.sync()
        self.assertEqual(self.requests("state", CONTROL_ID)[-1]["rate"], 1.25)

        self.assertEqual(self.change(d, "show_list", True), {"show_list": True})
        self.assertTrue(self.store.load().show_list)
        self.assertEqual(self.change(d, "keep", 300), {"keep": 300})
        self.assertEqual(self.store.load().keep, 300)
        d.sync()
        self.assertEqual(len(self.requests("items", CONTROL_ID)), 1, "the list sent again at the new length")
        self.assertEqual(self.pieces.values(SETTINGS_ID)["keep"], 300, "the window shows what was confirmed")

    def test_clear_the_missed_list(self):
        d = self.opened()
        self.missed.miss(MissedLine(id="m1", text="Behind Spotify.", queued_at=1.0), reason=REASON_LONGEST_WAIT)
        self.change(d, "clear_missed", True)
        self.assertEqual(self.missed.load(), [])
        d.sync()
        [items] = self.requests("items", CONTROL_ID)
        self.assertEqual([i for i in items["items"] if i.get("unplayed")], [])
        self.assertIn("settings: missed list cleared", self.logs)

    def test_the_autoplay_file_keeps_its_other_keys(self):
        self.places.autoplay_path.write_text(
            '[autoplay]\nmode = "summary"\nsummary_size = "small"\ncoalesce_seconds = 2.5\n\n[other]\nx = "y"\n')
        d = self.opened()
        self.change(d, "summary_size", "large")
        doc = tomllib.loads(self.places.autoplay_path.read_text())
        self.assertEqual(doc, {"autoplay": {"mode": "summary", "summary_size": "large", "coalesce_seconds": 2.5},
                               "other": {"x": "y"}})

    def test_a_change_that_fails_sends_the_old_value_back_and_says_why(self):
        d = self.opened()
        self.assertEqual(self.change(d, "keep", 5), {"keep": 200}, "out of range: the old value")
        self.assertEqual(self.store.load().keep, 200)
        self.places.autoplay_path.mkdir()  # a write that cannot land
        self.assertEqual(self.change(d, "autoplay_mode", "verbatim"), {"autoplay_mode": "summary"})
        self.assertTrue(any("autoplay_mode" in m and "not applied" in m for m in self.logs), self.logs)
        self.assertEqual(self.change(d, "nonsense", 1), {}, "an unknown item: nothing to send back")


class TestRateKept(_Base):
    def test_the_rate_survives_a_daemon_restart(self):
        d = self.opened()
        self.change(d, "rate", 1.75)
        d.close()
        restarted = self.driver()
        self.rates.clear()
        restarted.start()
        self.assertEqual(self.rates, [1.75], "the new daemon's sink gets the kept rate")
        restarted.sync()
        self.assertEqual(self.requests("open", CONTROL_ID)[-1]["rate"], 1.75)

    def test_a_rate_set_at_the_control_is_kept_and_shown_in_settings(self):
        d = self.opened()
        d.handle_event({"event": "rate", "id": CONTROL_ID, "rate": 2.0, "by": "user"})
        self.assertEqual(self.store.load().rate, 2.0)
        self.assertEqual(self.requests("values", SETTINGS_ID)[-1]["values"], {"rate": 2.0})
        d.close()
        self.rates.clear()
        self.driver().start()
        self.assertEqual(self.rates, [2.0])


class TestPlayerOff(_Base):
    def test_the_player_switch_off_closes_the_control_and_the_settings(self):
        d = self.opened()
        self.assertEqual(self.change(d, "player", False), {"player": False})
        self.assertFalse(self.store.load().player)
        self.assertEqual(d.sync(), "off")
        self.assertEqual(len(self.requests("close", CONTROL_ID)), 1)
        self.assertEqual(len(self.requests("close", SETTINGS_ID)), 1)
        self.assertNotIn(SETTINGS_ID, self.pieces.pieces)

    def test_a_lost_connection_opens_both_again(self):
        d = self.opened()
        self.standin.drop_connections()
        deadline = 200
        while d._client is not None and d._client.connected and deadline:  # the reader sees the close
            deadline -= 1
            time.sleep(0.01)
        self.assertEqual(d.sync(), "opened")
        self.assertEqual(len(self.requests("open", SETTINGS_ID)), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
