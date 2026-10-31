"""Unit tests for speech_control: the UI Utility's speech control (contract
v1 rev 1.2, the control piece) driven by the daemon. Against a stand-in
UI Utility on a private socket: never the real app, never a launch. No
daemon, no audio: WAVs are written with the wave module. Run directly."""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
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

import speech_control as sc
from missed_speech import (
    REASON_LONGEST_WAIT,
    REASON_MEETING,
    MissedLine,
    MissedList,
    play_ids,
)
from speech_control import CONTROL_ID, ControlActions, ControlDriver
from sound_yield import SoundYieldStore
from speaker import SpeakerStore
from speech_history import HistoryStore, SpeechHistory
from speech_settings import SettingsPlaces, SpeechSettings
from ui_utility_client import UiUtilityClient
from ui_utility_standin import StandIn, rev_1_2_script

T0 = 1_760_000_000.0


def write_wav(path: Path, seconds: float = 1.0, rate: int = 8000) -> Path:
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


class Recorder:
    """ControlActions that record what was asked."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.can_pause = True

    def actions(self) -> ControlActions:
        def rec(name, result=None):
            def f(*args):
                self.calls.append((name, *args))
                return result
            return f

        return ControlActions(
            pause=lambda: (self.calls.append(("pause",)), self.can_pause)[1],
            resume=rec("resume", True),
            stop=rec("stop"),
            restart=rec("restart", True),
            skip=rec("skip", True),
            set_rate=lambda r: (self.calls.append(("set_rate", r)), min(r, 2.0))[1],
            replay=lambda line: self.calls.append(("replay", line.id)),
            play_missed=lambda ids: (self.calls.append(("play_missed", list(ids))), len(ids))[1],
        )


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="auto-speech-control-")
        self.root = Path(self.tmpdir.name)
        self.store = HistoryStore(self.root / "history.toml")
        self.clock = Clock()
        self.history = SpeechHistory(self.root / "history", store=self.store, clock=self.clock)
        self.missed = MissedList(self.root / "missed.json")
        self.wav = write_wav(self.root / "src" / "line.wav")
        # /tmp, not $TMPDIR: a Unix socket path on macOS must fit in 104 bytes.
        self.sockdir = Path(tempfile.mkdtemp(prefix="as-sc-", dir="/tmp"))
        self.sock = self.sockdir / "ui.sock"
        # A rev 1.2 copy: the control alone (the settings piece: test_speech_settings).
        self.standin = StandIn(self.sock, script=rev_1_2_script).start()
        self.rec = Recorder()
        self.drivers: list[ControlDriver] = []
        self.launches = 0
        # His "closed it" mark in this test's own folder: kept across drivers
        # (a restart), never across tests.
        self.closed_mark = self.root / "control-closed.json"

    def tearDown(self) -> None:
        for d in self.drivers:
            d.close()
        self.standin.stop()
        shutil.rmtree(self.sockdir, ignore_errors=True)
        self.tmpdir.cleanup()

    def no_launch(self):
        self.launches += 1

    def driver(self, *, holding=lambda: False, installed=lambda: True, start_worker=False, names=None):
        d = ControlDriver(
            self.store,
            history=self.history,
            missed=self.missed,
            actions=self.rec.actions(),
            holding=holding,
            installed=installed,
            names=names,
            client_factory=lambda on_event: UiUtilityClient(self.sock, reply_timeout=1.0, on_event=on_event),
            launch=self.no_launch,
            icon=None,
            start_worker=start_worker,
            closed_mark=self.closed_mark,
            settings=SpeechSettings(SettingsPlaces(
                mute_marker=self.root / "auto-speech.disabled",
                autoplay_path=self.root / "autoplay.toml",
                autoplay_default=self.root / "autoplay.toml.example",
                sound_yield=SoundYieldStore(self.root / "sound-yield.toml"),
                speaker=SpeakerStore(self.root / "speaker.toml"),
                history=self.store,
                missed=self.missed,
            )),
        )
        self.drivers.append(d)
        return d

    def export(self, text: str, sender: str | None = None):
        line = self.history.export(self.wav, text=text, sender=sender, session_id="s1" if sender else None)
        self.clock.t += 60.0
        return line

    def requests(self, cmd: str | None = None) -> list[dict]:
        return [r for r in self.standin.requests if cmd is None or r.get("cmd") == cmd]

    def wait(self, pred, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if pred():
                return True
            time.sleep(0.01)
        self.fail("condition not reached")


class TestOpen(_Base):
    def test_off_by_default_nothing_connects(self):
        d = self.driver()
        self.assertEqual(d.sync(), "off")
        self.assertEqual(self.standin.connections, 0)

    def test_a_line_opens_one_control_with_the_history_newest_first_and_missed_lines_unplayed(self):
        # Was "at start it opens": the control now opens when a line plays,
        # never at daemon start (his 2026-10-31 12:53, #m1031-1313-9a54).
        self.store.set_player(True)
        a = self.export("First line.", sender="Master Desk")
        b = self.export("Second line.")
        self.missed.miss(MissedLine(id="m1", text="Held behind Spotify.", queued_at=time.time() - 30),
                         reason=REASON_LONGEST_WAIT)
        d = self.driver(names=lambda sid: None)
        d.start()
        self.assertEqual(d.sync(), "no line", "the daemon's start opens nothing")
        self.assertEqual(self.standin.connections, 0)
        d.playing("Now.", None)
        self.assertEqual(d.sync(), "opened")
        self.assertEqual(self.requests("hello")[0]["cmd"], "hello")
        [req] = self.requests("open")
        now_id = req["items"][0]["id"]
        self.assertEqual((req["panel"], req["id"], req["state"], req["current"]), ("control", CONTROL_ID, "playing", now_id))
        self.assertEqual(req["source"], {"name": "auto-speech", "icon": None})
        self.assertEqual(req["rate"], 1.0)
        self.assertEqual(req["rates"], sc.RATES)
        ids = [i["id"] for i in req["items"]]
        self.assertEqual(ids, [now_id, "missed:m1", b.id, a.id], "the line playing on top; newest first")
        m, second, first = req["items"][1:]
        self.assertEqual((m["unplayed"], m["clip"], m["text"]), (True, None, "Held behind Spotify."))
        self.assertEqual(first["source"], "Master Desk")
        self.assertEqual(first["clip"], str(a.wav))
        self.assertNotIn("unplayed", second)
        self.assertRegex(first["at"], r"^\d\d:\d\d$")
        self.assertEqual(d.sync(), "none", "nothing more to send")

    def test_every_item_carries_its_date_time_with_the_local_offset(self):
        # Contract 1.5: the Date and Time columns read `time`; `at` stays the fallback.
        from datetime import datetime

        self.store.set_player(True)
        a = self.export("Heard.")
        self.missed.miss(MissedLine(id="m1", text="Missed.", queued_at=T0 + 3600), reason=REASON_LONGEST_WAIT)
        d = self.driver()
        d.playing("Now.", None)
        rfc3339 = r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$"
        items = {i["id"]: i for i in d.items()}
        for item in items.values():
            self.assertRegex(item["time"], rfc3339)
            parsed = datetime.fromisoformat(item["time"])
            self.assertEqual(parsed.strftime("%H:%M"), item["at"], "the same moment as `at`, in local time")
        self.assertEqual(datetime.fromisoformat(items[a.id]["time"]).timestamp(), int(a.spoken_at))
        self.assertEqual(datetime.fromisoformat(items["missed:m1"]["time"]).timestamp(), T0 + 3600)

    def test_never_opens_during_a_hold_and_opens_when_it_ends(self):
        self.store.set_player(True)
        holding = [True]
        d = self.driver(holding=lambda: holding[0])
        d.held()
        self.assertEqual(d.sync(), "no line", "a hold with no line playing opens nothing")
        d.playing("Now it plays.", "Master Desk")
        self.assertEqual(d.sync(), "held", "never opened while a hold stands")
        self.assertEqual(self.standin.connections, 0)
        holding[0] = False
        self.assertEqual(d.sync(), "opened")
        [req] = self.requests("open")
        self.assertEqual((req["state"], req["items"][0]["text"], req["current"]), ("playing", "Now it plays.", req["items"][0]["id"]))
        self.assertEqual(req["source"]["name"], "Master Desk")

    def test_not_installed_opens_nothing(self):
        self.store.set_player(True)
        d = self.driver(installed=lambda: False)
        d.playing("A line.", None)
        self.assertEqual(d.sync(), "not installed")
        self.assertEqual(self.standin.connections, 0)

    def test_turning_the_setting_off_closes_the_control(self):
        self.store.set_player(True)
        d = self.driver()
        d.playing("A line.", None)
        self.assertEqual(d.sync(), "opened")
        self.store.set_player(False)
        self.assertEqual(d.sync(), "off")
        self.assertEqual([r["id"] for r in self.requests("close")], [CONTROL_ID])

    def test_a_utility_without_the_control_piece_is_the_wrong_contract(self):
        self.standin.script = lambda r: {"ok": True, "contract": 1, "pieces": ["player"]} if r.get("cmd") == "hello" else {"ok": True}
        self.store.set_player(True)
        d = self.driver()
        d.playing("A line.", None)
        self.assertEqual(d.sync(), "wrong contract")
        self.assertEqual(self.requests("open"), [])

    def test_every_missed_and_held_line_is_listed_whatever_the_history_cap(self):
        # Live, 21:27: 50 history lines newer than 20 missed lines cut them all.
        self.store.set_player(True)
        old = time.time() - 3600
        for n in range(3):
            self.missed.miss(MissedLine(id=f"m{n}", text=f"Missed {n}.", queued_at=old + n),
                             reason=REASON_LONGEST_WAIT)
        self.missed.hold(MissedLine(id="h1", text="Held now.", queued_at=old + 10))
        self.clock.t = time.time()
        self.store.set_keep(sc.MAX_ITEMS)
        for n in range(sc.MAX_ITEMS + 5):
            self.export(f"Line {n}.")
        d = self.driver(names=lambda sid: None)
        d.start()
        d.playing("Now.", None)
        self.assertEqual(d.sync(), "opened")
        [req] = self.requests("open")
        items = req["items"][1:]  # after the line playing now
        unplayed = [i["id"] for i in items if i.get("unplayed")]
        self.assertEqual(unplayed, ["missed:h1", "missed:m2", "missed:m1", "missed:m0"])
        self.assertEqual(len(items), sc.MAX_ITEMS + 4, "the history capped at MAX_ITEMS, every unplayed line kept")
        self.assertEqual(items[-4:], [i for i in items if i.get("unplayed")], "still newest first by time")


class TestKeepCurrent(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.store.set_player(True)
        self.d = self.driver()
        self.d.reopen()  # he opened it: these tests start with the control open
        self.assertEqual(self.d.sync(), "opened")

    def test_a_line_goes_on_top_playing_then_the_list_and_idle_when_heard(self):
        self.d.playing("Build is green.", "Master Desk")
        self.assertEqual(self.d.sync(), "sent")
        [src] = self.requests("source")
        self.assertEqual(src["source"]["name"], "Master Desk")
        [item] = self.requests("item")
        now_id = item["item"]["id"]
        self.assertTrue(now_id.startswith(sc.NOW_PREFIX))
        self.assertEqual(item["item"]["text"], "Build is green.")
        [state] = self.requests("state")
        self.assertEqual((state["state"], state["current"]), ("playing", now_id))
        line = self.export("Master Desk here, Build is green.", sender="Master Desk")
        self.d.ended(line)
        self.d.sync()
        items = self.requests("items")[-1]["items"]
        self.assertEqual(items[0]["id"], line.id, "the heard line replaces the provisional one")
        self.assertNotIn(now_id, [i["id"] for i in items])
        state = self.requests("state")[-1]
        self.assertEqual((state["state"], state["current"]), ("idle", line.id))

    def test_the_same_speaker_sends_no_source_again(self):
        self.d.playing("One.", "Master Desk")
        self.d.sync()
        self.d.playing("Two.", "Master Desk")
        self.d.sync()
        self.assertEqual(len(self.requests("source")), 1)
        self.d.playing("Three.", "Development Desk")
        self.d.sync()
        self.assertEqual(self.requests("source")[-1]["source"]["name"], "Development Desk")

    def test_a_hold_is_held_and_a_line_set_aside_shows_unplayed(self):
        self.d.held()
        self.d.sync()
        self.assertEqual(self.requests("state")[-1]["state"], "held")
        self.missed.miss(MissedLine(id="m9", text="Set aside.", queued_at=time.time()), reason=REASON_MEETING)
        self.d.ended(None)
        self.d.sync()
        items = self.requests("items")[-1]["items"]
        self.assertEqual((items[0]["id"], items[0]["unplayed"]), ("missed:m9", True))
        self.assertEqual(self.requests("state")[-1]["state"], "idle")

    def test_a_played_missed_line_leaves_the_unplayed_items(self):
        self.missed.miss(MissedLine(id="m2", text="Missed.", queued_at=time.time()), reason=REASON_LONGEST_WAIT)
        self.d.list_changed()
        self.d.sync()
        self.assertTrue(self.requests("items")[-1]["items"][0]["unplayed"])
        self.missed.remove("m2")
        line = self.export("Missed.")
        self.d.ended(line)
        self.d.sync()
        items = self.requests("items")[-1]["items"]
        self.assertEqual([i.get("unplayed", False) for i in items], [False])
        self.assertEqual(items[0]["id"], line.id)

    def test_a_closed_connection_reopens_the_control_with_everything(self):
        self.standin.drop_connections()
        self.wait(lambda: not self.d._client.connected)
        self.d.playing("After the drop.", None)
        self.assertEqual(self.d.sync(), "opened")
        self.assertEqual(self.requests("open")[-1]["items"][0]["text"], "After the drop.")


class TestEvents(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.store.set_player(True)
        self.d = self.driver()
        self.d.reopen()  # he opened it: these tests start with the control open
        self.d.sync()

    def ev(self, **kw):
        self.d.handle_event({"id": CONTROL_ID, **kw})
        self.d.sync()

    def test_hold_pauses_and_play_resumes_the_line_playing(self):
        self.d.playing("Long line.", None)
        self.d.sync()
        self.ev(event="control", action="hold")
        self.assertEqual(self.rec.calls[-1], ("pause",))
        self.assertEqual(self.requests("state")[-1]["state"], "held")
        self.ev(event="control", action="play")
        self.assertEqual(self.rec.calls[-1], ("resume",))
        self.assertEqual(self.requests("state")[-1]["state"], "playing")

    def test_play_does_not_resume_a_hold_for_another_apps_sound(self):
        self.d.held()
        self.d.sync()
        self.ev(event="control", action="play")
        self.assertEqual(self.rec.calls, [])

    def test_play_when_idle_replays_the_newest_line_and_skip_back_the_one_before(self):
        a = self.export("Older.")
        b = self.export("Newer.")
        self.ev(event="control", action="play")
        self.assertEqual(self.rec.calls[-1], ("replay", b.id))
        self.d.ended(b)
        self.d.sync()
        self.ev(event="control", action="skip-back")
        self.assertEqual(self.rec.calls[-1], ("replay", a.id))

    def test_stop_skip_and_restart_go_to_the_daemon(self):
        self.d.playing("Playing.", None)
        self.d.sync()
        self.ev(event="control", action="skip-back")
        self.ev(event="control", action="skip-forward")
        self.ev(event="control", action="stop")
        self.assertEqual(self.rec.calls, [("restart",), ("skip",), ("stop",)])
        self.assertEqual(self.requests("state")[-1]["state"], "idle")

    def test_rate_goes_to_the_daemon_and_the_rate_in_effect_is_confirmed(self):
        self.ev(event="rate", rate=4)
        self.assertEqual(self.rec.calls, [("set_rate", 4.0)])
        self.assertEqual(self.requests("state")[-1]["rate"], 2.0)
        self.ev(event="rate", rate=True)
        self.ev(event="rate", rate=-1)
        self.assertEqual(len(self.rec.calls), 1, "a rate that is not a positive number is ignored")

    def test_play_item_replays_a_heard_line_or_plays_a_missed_one(self):
        line = self.export("Heard.")
        self.ev(event="play-item", item=line.id, clip=str(line.wav))
        self.ev(event="play-item", item="missed:m7", clip=None)
        self.ev(event="play-item", item="nope")
        self.assertEqual(self.rec.calls, [("replay", line.id), ("play_missed", ["m7"])])

    def test_play_all_plays_each_in_the_order_given(self):
        self.ev(event="play-all", items=["missed:m1", "missed:m3", "missed:m2"])
        self.assertEqual(self.rec.calls, [("play_missed", ["m1"]), ("play_missed", ["m3"]), ("play_missed", ["m2"])])

    def test_play_from_plays_every_line_given_oldest_first_and_logs_one_line(self):
        # His 2026-10-30 08:47: "i want to play from a particular past time all
        # the messages up to the latest" (contract 1.5, play-from).
        logged: list[str] = []
        self.d._log = logged.append
        a = self.export("Oldest.")
        b = self.export("Newer.")
        self.ev(event="play-from", **{"from": "2026-10-30T08:00:00-07:00"},
                items=[a.id, "missed:m1", b.id, "nope", 7])
        self.assertEqual(self.rec.calls, [("replay", a.id), ("play_missed", ["m1"]), ("replay", b.id)],
                         "in the order given, each once; unknown ids skipped")
        self.assertEqual(logged, ["control: play-from 2026-10-30T08:00:00-07:00 (3 lines)"])

    def test_play_from_never_restarts_the_line_playing_now(self):
        a = self.export("Heard.")
        self.d.playing("Playing now.", None)
        self.d.sync()
        now_id = self.d.items()[0]["id"]
        self.ev(event="play-from", **{"from": "2026-10-30T08:00:00-07:00"}, items=[a.id, now_id])
        self.assertEqual(self.rec.calls, [("replay", a.id)], "the line playing now plays on to its end")

    def test_play_from_with_no_items_queues_nothing(self):
        self.ev(event="play-from", **{"from": "2026-10-30T08:00:00-07:00"}, items="bad")
        self.ev(event="play-from", **{"from": "2026-10-30T08:00:00-07:00"})
        self.assertEqual(self.rec.calls, [])

    def test_another_panels_events_are_not_ours(self):
        self.d.handle_event({"id": "other", "event": "control", "action": "stop"})
        self.assertEqual(self.rec.calls, [])

    def test_closed_by_him_stays_closed_when_the_next_line_plays(self):
        # Was "until the next line": a line no longer clears his mark; only
        # his own reopening does (#m1031-1313-9a54).
        self.ev(event="closed", by="user")
        self.d.list_changed()
        self.assertEqual(self.d.sync(), "closed by him")
        self.d.playing("Next line.", None)
        self.assertEqual(self.d.sync(), "closed by him")
        self.assertEqual(len(self.requests("open")), 1, "only the open from setUp")

    def test_events_from_the_utility_are_acted_on_by_the_worker(self):
        d = self.driver(start_worker=True)
        d.start()
        d.playing("A line.", None)  # a line opens this driver's control
        self.wait(lambda: len(self.requests("open")) >= 2)
        self.standin.send_event({"event": "control", "id": CONTROL_ID, "action": "stop"})
        self.wait(lambda: ("stop",) in self.rec.calls)


class TestOpensOnlyWhenALinePlays(_Base):
    """His 2026-10-31 12:53: "why is my speech app opening on its own over
    and over" (#m1031-1313-9a54). The daemon idles out after 600 s and
    launchd starts it again; each start opened the control. Now a line that
    starts playing is the only thing that opens it, and his "closed it" mark
    is kept on disk across restarts until he reopens it himself."""

    def setUp(self) -> None:
        super().setUp()
        self.store.set_player(True)

    def test_the_daemons_start_opens_nothing_and_sends_nothing(self):
        self.store.set_rate(1.5)
        d = self.driver(start_worker=True)
        d.start()  # the worker is not started for this: no wake, no connection
        d.list_changed()
        d.queue_changed()
        d.held()
        time.sleep(0.3)
        self.assertEqual(self.standin.connections, 0, "no connection, no launch, nothing opened")
        self.assertEqual(self.launches, 0)
        self.assertIn(("set_rate", 1.5), self.rec.calls, "the kept rate still goes to the sink")
        self.assertEqual(d.sync(), "no line")
        self.assertEqual(self.requests("open"), [])

    def test_the_first_line_that_plays_opens_it_with_the_kept_rate(self):
        self.store.set_rate(1.5)
        d = self.driver()
        d.start()
        self.assertEqual(d.sync(), "no line")
        d.playing("First line.", "Master Desk")
        self.assertEqual(d.sync(), "opened")
        [req] = self.requests("open")
        self.assertEqual((req["state"], req["rate"], req["items"][0]["text"]), ("playing", 1.5, "First line."))
        d.ended(None)
        self.assertEqual(d.sync(), "sent", "a sync after the open goes to the open control")
        self.assertEqual(len(self.requests("open")), 1)

    def test_his_closed_mark_keeps_a_playing_line_from_opening_it(self):
        d = self.driver()
        d.playing("One.", None)
        self.assertEqual(d.sync(), "opened")
        d.handle_event({"id": CONTROL_ID, "event": "closed", "by": "user"})
        self.assertTrue(sc.read_closed_mark(self.closed_mark), "his close is kept on disk")
        for text in ("Two.", "Three."):
            d.playing(text, None)
            self.assertEqual(d.sync(), "closed by him")
        self.assertEqual(len(self.requests("open")), 1)
        self.assertTrue(sc.read_closed_mark(self.closed_mark), "a line playing never clears his mark")

    def test_his_mark_survives_a_restart(self):
        d = self.driver()
        d.playing("One.", None)
        d.sync()
        d.handle_event({"id": CONTROL_ID, "event": "closed", "by": "user"})
        d.close()
        again = self.driver()  # the daemon started again by launchd, over the same state file
        again.start()
        again.playing("After the restart.", None)
        self.assertEqual(again.sync(), "closed by him")
        self.assertEqual(len(self.requests("open")), 1)

    def test_his_switch_turned_on_reopens_it_and_clears_the_mark(self):
        from speech_settings import SETTINGS_ID

        sc.write_closed_mark(True, self.closed_mark)
        d = self.driver()
        d.playing("One.", None)
        self.assertEqual(d.sync(), "closed by him")
        d.handle_event({"id": SETTINGS_ID, "event": "setting", "item": "player", "value": True, "by": "user"})
        self.assertFalse(sc.read_closed_mark(self.closed_mark))
        self.assertEqual(d.sync(), "opened")

    def test_history_player_on_clears_the_mark_for_the_next_line(self):
        sc.write_closed_mark(True, self.closed_mark)
        d = self.driver()
        self.assertTrue(sc.reopened_by_him(self.closed_mark), "a mark stood")
        self.assertFalse(sc.read_closed_mark(self.closed_mark))
        self.assertEqual(d.sync(), "no line", "player-on clears the mark; the next line opens it")
        d.playing("Next.", None)
        self.assertEqual(d.sync(), "opened")
        self.assertFalse(sc.reopened_by_him(self.closed_mark), "no mark: nothing to clear")

    def test_a_close_that_is_not_his_leaves_no_mark(self):
        d = self.driver()
        d.playing("One.", None)
        d.sync()
        d.handle_event({"id": CONTROL_ID, "event": "closed", "by": "utility"})
        self.assertFalse(sc.read_closed_mark(self.closed_mark))
        d.playing("Two.", None)
        self.assertEqual(d.sync(), "opened")


class TestSinkVerbs(unittest.TestCase):
    """The audio sink's verbs for the control, with a fake mpv: no audio."""

    def setUp(self) -> None:
        from native_audio_sink import NativeAudioSink

        self.root = Path(tempfile.mkdtemp(prefix="auto-speech-control-"))
        fake = self.root / "mpv"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        self.sink = NativeAudioSink(mpv_path=fake, sound_yield=False, audio_output="null")
        self.wav = write_wav(self.root / "a.wav", 0.1)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_the_rate_is_on_every_later_line_and_clamped(self):
        self.assertNotIn("--speed=1", " ".join(self.sink.mpv_argv("mpv", self.wav)))
        self.assertEqual(self.sink.set_speed(1.5), 1.5)
        self.assertIn("--speed=1.5", self.sink.mpv_argv("mpv", self.wav))
        self.assertEqual(self.sink.set_speed(10), 4.0)
        self.assertEqual(self.sink.set_speed(0.01), 0.25)

    def test_nothing_playing_means_nothing_to_pause_resume_restart_or_skip(self):
        self.assertEqual([self.sink.pause(), self.sink.resume(), self.sink.restart(), self.sink.skip()], [False] * 4)

    def test_on_start_is_called_once_just_before_the_play(self):
        started: list[int] = []
        self.sink.play(self.wav, on_start=lambda: started.append(1))
        self.assertEqual(started, [1])


class TestMissedPlayIds(unittest.TestCase):
    def test_plays_the_named_missed_lines_in_order_and_skips_held_and_unknown(self):
        root = Path(tempfile.mkdtemp(prefix="auto-speech-control-"))
        try:
            lst = MissedList(root / "missed.json")
            lst.miss(MissedLine(id="a", text="A.", queued_at=time.time()), reason=REASON_LONGEST_WAIT)
            lst.miss(MissedLine(id="b", text="B.", queued_at=time.time()), reason=REASON_LONGEST_WAIT)
            lst.hold(MissedLine(id="h", text="Held.", queued_at=time.time()))
            sent: list[tuple] = []

            class Client:
                def speak(self, text, **kw):
                    sent.append((text, kw["missed_id"], kw["hold"]))

            self.assertEqual(play_ids(["b", "h", "zz", "a"], missed=lst, client=Client()), 2)
            self.assertEqual(sent, [("B.", "b", False), ("A.", "a", False)])
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
