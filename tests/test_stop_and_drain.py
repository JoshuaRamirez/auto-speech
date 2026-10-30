"""Unit tests for ADR-024: a stop that stops, a restart that drains first,
only live sessions heard, and `missed add` (#m1021-2343-74ba).

Nothing is heard: the sinks here record and never start mpv, and the runner
sets AUTO_SPEECH_MPV_AO=null besides. Every file is in a temporary folder;
the stopped-state file is patched to one. The control is the stand-in on a
private socket. Run directly or through tests/run_all.sh."""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
import argparse
import io
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
for _p in (str(SRC), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("AUTO_SPEECH_TMP_ROOT", tempfile.mkdtemp(prefix="auto-speech-tests-"))

import cli  # noqa: E402
import narrator_restart as nr  # noqa: E402
import narrator_service  # noqa: E402
from autoplay_enrollment import SessionEnrollment  # noqa: E402
from doctor import Doctor  # noqa: E402
from health_report import HealthReport, Status  # noqa: E402
from missed_speech import (  # noqa: E402
    MISSED,
    REASON_ADDED,
    REASON_RESTART,
    REASON_STOPPED,
    MissedList,
)
from priority_arbiter import QueueItem  # noqa: E402
from session_liveness import SessionLiveness  # noqa: E402
from speech_control import CONTROL_ID, ControlActions, ControlDriver  # noqa: E402
from speech_history import HistoryStore, SpeechHistory  # noqa: E402
from ui_utility_client import UiUtilityClient  # noqa: E402
from ui_utility_standin import StandIn, rev_1_2_script  # noqa: E402


class _Sink:
    """Records what it was asked to play; never plays anything."""

    def __init__(self) -> None:
        self.played: list[Path] = []
        self.interrupts = 0
        self.last_hold = None

    def play(self, wav_path, timeout=None, *, hold=True, already_waited=0.0, on_hold=None, on_start=None) -> None:
        if on_start is not None:
            on_start()
        self.played.append(Path(wav_path))

    def interrupt(self) -> None:
        self.interrupts += 1


def _item(text: str, **kw) -> QueueItem:
    kw.setdefault("priority", 2)
    return QueueItem(payload=text, enqueued_at=time.time(), **kw)


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="auto-speech-stop-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = patch.object(narrator_service, "STOPPED_FILE", self.tmp / "stopped.json")
        p.start()
        self.addCleanup(p.stop)
        self.sink = _Sink()
        self.svc = self.service()

    def service(self):
        svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
        svc._config = {}
        svc._sink = self.sink
        svc._synth = MagicMock()
        svc._profile = None  # synthesis is never reached in these tests
        svc._state_lock = threading.Lock()
        svc._engine_state = "IDLE"
        svc._active_priority = None
        svc._active_item = None
        svc._interrupted = False
        svc._preempted = False
        svc._dropped_phases = 0
        svc._tts_queue = queue.Queue()
        svc._missed = MissedList(self.tmp / "missed.json")
        svc._history = SpeechHistory(self.tmp / "history", store=HistoryStore(self.tmp / "history.toml"))
        svc._stop = threading.Event()
        svc._stopped_since = None
        svc._last_event_ts = time.time()
        svc._sink_takes_hold_kwargs = None
        svc._last_hold_ended_at = None
        gate = MagicMock()
        gate.settings.return_value = MagicMock(max_wait_seconds=180.0)
        gate.assess.return_value = MagicMock(reason="", meeting_names=lambda: ())
        svc._yield_gate = gate
        svc._control = None
        speaker = MagicMock()
        speaker.plan.return_value = None
        svc._speaker = speaker
        return svc

    def queued_texts(self) -> list[str]:
        out = []
        while True:
            try:
                q = self.svc._tts_queue.get_nowait()
            except queue.Empty:
                return out
            out.append(q.payload if isinstance(q, QueueItem) else q)


# --- 1. a stop must stop --------------------------------------------------------


class TestStop(_Base):
    def test_a_stop_holds_every_later_line_from_any_caller_on_the_missed_list(self):
        reply = self.svc.dispatch_json({"action": "interrupt"})
        self.assertEqual(reply["stopped"], True)
        self.assertTrue(self.svc.dispatch_json({"action": "status"})["stopped"])
        # Parts 3 and 4 of a briefing arrive from desk-say, then a cached line.
        self.svc._speak("Part 3.", item=_item("Part 3.", session_id="desk"))
        self.svc._speak("Part 4.", item=_item("Part 4.", session_id="desk"))
        wav = self.tmp / "cached.wav"
        wav.write_bytes(b"RIFF")
        self.assertFalse(self.svc._play_cached(wav, _item(str(wav), line_text="Part 5."), text="Part 5."))
        self.assertEqual(self.sink.played, [], "nothing plays while stopped")
        lines = self.svc._missed.load()
        self.assertEqual([m.text for m in lines], ["Part 3.", "Part 4.", "Part 5."], "unplayed, in order")
        self.assertEqual({(m.status, m.reason) for m in lines}, {(MISSED, REASON_STOPPED)})

    def test_resume_ends_the_stop_and_queues_the_set_aside_lines_in_order(self):
        self.svc.dispatch_json({"action": "interrupt"})
        self.svc._speak("Part 3.", item=_item("Part 3."))
        self.svc._speak("Part 4.", item=_item("Part 4."))
        reply = self.svc.dispatch_json({"action": "resume"})
        self.assertEqual((reply["was_stopped"], reply["queued"]), (True, 2))
        self.assertFalse(self.svc.dispatch_json({"action": "status"})["stopped"])
        self.assertEqual(self.queued_texts(), ["Part 3.", "Part 4."])
        again = self.svc.dispatch_json({"action": "resume"})
        self.assertEqual((again["was_stopped"], again["queued"]), (False, 0), "not stopped: nothing to do")

    def test_the_stop_is_kept_across_a_restart(self):
        self.svc._stop_speech()
        since = narrator_service._read_stopped()
        self.assertIsNotNone(since)
        self.assertEqual(since, self.svc._stopped_since)
        self.svc._resume_speech()
        self.assertIsNone(narrator_service._read_stopped())

    def test_his_typing_barge_in_is_not_a_stop(self):
        self.svc._handle_p1_interrupt()  # UserPromptSubmit's barge-in
        self.assertFalse(self.svc._is_stopped())
        self.assertFalse(self.svc.dispatch_json({"action": "status"})["stopped"])

    def test_a_play_item_ends_the_stop_without_playing_the_rest(self):
        self.svc._stop_speech()
        self.svc._speak("Part 3.", item=_item("Part 3."))
        line = MagicMock(wav=self.tmp / "h.wav", text="Heard.", id="h1")
        self.svc._replay_history_line(line)
        self.assertFalse(self.svc._is_stopped())
        self.assertEqual(len(self.queued_texts()), 1, "only the line he asked for")
        self.assertEqual([m.text for m in self.svc._missed.load()], ["Part 3."], "the rest stays unplayed")

    def test_the_controls_stop_and_play_glyphs_are_the_stop_and_the_resume(self):
        actions = self.svc._control_actions()
        actions.stop()
        self.assertTrue(self.svc._is_stopped())
        self.svc._speak("Part 3.", item=_item("Part 3."))
        self.assertEqual(actions.unstop(), 1)
        self.assertFalse(self.svc._is_stopped())
        self.assertEqual(actions.unstop(), -1)

    def test_narrate_status_shows_the_stopped_state(self):
        client = MagicMock()
        client._socket_path.is_socket.return_value = True
        client.status.return_value = MagicMock(raw={
            "status": "ok", "stopped": True, "stopped_since": time.time(), "daemon_state": "IDLE", "queued": 0})
        out = io.StringIO()
        with patch.object(cli, "DaemonClient", return_value=client), redirect_stdout(out), \
                patch("pathlib.Path.home", return_value=self.tmp):  # never his markers
            rc = cli.handle_narrate(argparse.Namespace(narrate_action="status", session_id="s1"))
        self.assertEqual(rc, 0)
        self.assertIn("stopped: yes, since", out.getvalue())


class TestControlPlayAfterStop(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="auto-speech-stop-control-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sockdir = Path(tempfile.mkdtemp(prefix="as-sd-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.sockdir, True)
        self.standin = StandIn(self.sockdir / "ui.sock", script=rev_1_2_script).start()
        self.addCleanup(self.standin.stop)

    def driver(self, unstop):
        self.calls: list = []
        store = HistoryStore(self.tmp / "history.toml")
        store.set_player(True)
        d = ControlDriver(
            store,
            history=SpeechHistory(self.tmp / "history", store=store),
            missed=MissedList(self.tmp / "missed.json"),
            actions=ControlActions(unstop=unstop, replay=lambda line: self.calls.append(("replay", line.id))),
            installed=lambda: True,
            client_factory=lambda on_event: UiUtilityClient(self.sockdir / "ui.sock", reply_timeout=2.0, on_event=on_event),
            launch=lambda: None,
            icon=None,
            start_worker=False,
        )
        self.addCleanup(d.close)
        return d

    def test_play_after_a_stop_plays_what_the_stop_set_aside(self):
        d = self.driver(unstop=lambda: (self.calls.append(("unstop",)), 2)[1])
        d.sync()
        d.handle_event({"event": "control", "id": CONTROL_ID, "action": "play"})
        self.assertEqual(self.calls, [("unstop",)], "the set-aside lines play; no replay of the newest")

    def test_play_with_no_stop_is_as_before(self):
        d = self.driver(unstop=lambda: -1)
        d.sync()
        d.handle_event({"event": "control", "id": CONTROL_ID, "action": "play"})
        self.assertEqual(self.calls, [], "an empty history: nothing to replay")


# --- 2. a restart drains first ----------------------------------------------------


class TestDrainedRestart(unittest.TestCase):
    def run_restart(self, statuses, *, pending=lambda: None, when_quiet=False, timeout=10.0):
        seq = iter(statuses)
        last = [None]

        def status():
            try:
                last[0] = next(seq)
            except StopIteration:
                pass
            return last[0]

        t = [0.0]
        restarted = []
        result = nr.restart(
            status=status, when_quiet=when_quiet, timeout_s=timeout, pending=pending,
            restart_job=lambda: (restarted.append(1), nr.RestartResult(True, "restarted"))[1],
            clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s),
        )
        return result, restarted

    def test_it_waits_for_the_line_and_the_queue_then_restarts(self):
        busy = {"daemon_state": "PLAYING", "active_priority": 2, "queue_depths": {"2": 9}}
        draining = {"daemon_state": "IDLE", "active_priority": None, "queue_depths": {"2": 1}}
        idle = {"daemon_state": "IDLE", "active_priority": None, "queue_depths": {"2": 0}}
        result, restarted = self.run_restart([busy, draining, idle])
        self.assertTrue(result.ok)
        self.assertEqual(restarted, [1])

    def test_on_timeout_it_refuses_and_says_what_is_queued(self):
        busy = {"daemon_state": "PLAYING", "active_priority": 2, "queue_depths": {"2": 9}}
        result, restarted = self.run_restart([busy], timeout=5.0)
        self.assertFalse(result.ok)
        self.assertIn("a line is playing", result.text)
        self.assertIn("9 lines queued", result.text)
        self.assertEqual(restarted, [])

    def test_desk_speakers_pending_lines_hold_the_restart(self):
        idle = {"daemon_state": "IDLE", "queue_depths": {}}
        left = iter([3, 1, 0])
        result, restarted = self.run_restart([idle], pending=lambda: next(left, 0))
        self.assertTrue(result.ok)
        result, restarted = self.run_restart([idle], pending=lambda: 3, timeout=2.0)
        self.assertIn("desk-speaker has 3 pending", result.text)
        self.assertEqual(restarted, [])

    def test_when_quiet_also_waits_for_a_hold(self):
        held = {"daemon_state": "IDLE", "queue_depths": {}, "sound_yield": {"holding": True, "held_seconds": 4.0}}
        self.assertEqual(nr.busy_reasons(held), [])
        result, _ = self.run_restart([held], when_quiet=True, timeout=2.0)
        self.assertFalse(result.ok)
        self.assertIn("held", result.text)

    def test_no_daemon_has_nothing_to_drain(self):
        result, restarted = self.run_restart([None])
        self.assertTrue(result.ok)

    def test_desk_speaker_status_is_read_as_json_or_text(self):
        tmp = Path(tempfile.mkdtemp(prefix="auto-speech-ds-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        exe = tmp / "desk-speaker"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)

        def ran(out, rc=0):
            return lambda *a, **k: MagicMock(returncode=rc, stdout=out)

        self.assertEqual(nr.desk_speaker_pending(exe, run=ran('{"pending": [1, 2]}')), 2)
        self.assertEqual(nr.desk_speaker_pending(exe, run=ran('{"pending": 0}')), 0)
        self.assertEqual(nr.desk_speaker_pending(exe, run=ran("pending: 4 lines")), 4)
        self.assertIsNone(nr.desk_speaker_pending(exe, run=ran("", rc=2)), "no status yet: not waited on")
        self.assertIsNone(nr.desk_speaker_pending(tmp / "absent"))

    def test_kickstart_names_the_launchd_label(self):
        seen = []
        result = nr.kickstart("dev.example.label", run=lambda argv, **k: (seen.append(argv), MagicMock(returncode=0))[1])
        self.assertTrue(result.ok)
        self.assertEqual(seen, [["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/dev.example.label"]])
        failed = nr.kickstart("x", run=lambda argv, **k: MagicMock(returncode=113, stderr="Could not find service", stdout=""))
        self.assertFalse(failed.ok)

    def test_the_cli_refuses_with_exit_1_on_timeout(self):
        err = io.StringIO()
        refused = nr.RestartResult(False, "narrate restart refused: still busy after 1 s: 2 lines queued.")
        with patch.object(nr, "restart", return_value=refused), redirect_stderr(err):
            rc = cli.handle_narrate(argparse.Namespace(narrate_action="restart", when_quiet=False, timeout=1.0))
        self.assertEqual(rc, 1)
        self.assertIn("2 lines queued", err.getvalue())


class TestSigterm(_Base):
    def test_the_first_signal_lets_the_line_finish_and_a_second_cuts_it(self):
        self.svc._fsm = MagicMock()
        self.svc._fsm.can.return_value = False
        self.svc._shutdown_cut = False
        self.svc._on_signal(15, None)
        self.assertTrue(self.svc._stop.is_set())
        self.assertEqual(self.sink.interrupts, 0, "the line in flight plays to its end")
        self.svc._on_signal(15, None)
        self.assertEqual((self.sink.interrupts, self.svc._shutdown_cut), (1, True))

    def test_whatever_is_still_queued_goes_on_the_missed_list_in_order(self):
        for n in (3, 4, 5):
            self.svc._tts_queue.put(_item(f"Part {n}."))
        self.svc._tts_queue.put(_item(str(self.tmp / "h.wav"), line_text="A replay.", history_id="h1"))
        self.assertEqual(self.svc._set_aside_queued(REASON_RESTART), 3)
        lines = self.svc._missed.load()
        self.assertEqual([m.text for m in lines], ["Part 3.", "Part 4.", "Part 5."])
        self.assertEqual({m.reason for m in lines}, {REASON_RESTART})

    def test_a_line_cut_by_the_shutdown_is_set_aside_not_heard(self):
        self.svc._shutdown_cut = True
        wav = self.tmp / "line.wav"
        wav.write_bytes(b"RIFF")
        heard = self.svc._play_item(wav, _item("Part 2."), text="Part 2.")
        self.assertFalse(heard)
        [m] = self.svc._missed.load()
        self.assertEqual((m.text, m.reason), ("Part 2.", REASON_RESTART))
        self.assertEqual(self.svc._history.load(), [])


# --- 3. only live sessions are heard ------------------------------------------------


class TestLiveness(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="auto-speech-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.now = time.time()
        self.old = self.now - 18 * 86400  # 3 October

    def marker(self, kind: str, sid: str, mtime: float) -> Path:
        p = self.home / ".claude" / kind / sid
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
        os.utime(p, (mtime, mtime))
        return p

    def transcript(self, sid: str, mtime: float) -> None:
        p = self.home / ".claude" / "projects" / "-Users-x-proj" / f"{sid}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}\n")
        os.utime(p, (mtime, mtime))

    def test_alive_is_a_young_marker_or_a_recent_transcript(self):
        live = SessionLiveness(self.home, clock=lambda: self.now)
        self.assertTrue(live.alive(self.marker("auto-speech-narrate-sessions", "new", self.now - 60)))
        old_but_used = self.marker("auto-speech-narrate-sessions", "used", self.old)
        self.transcript("used", self.now - 3600)
        self.assertTrue(live.alive(old_but_used))
        gone = self.marker("auto-speech-narrate-sessions", "gone", self.old)
        self.transcript("gone", self.old)
        self.assertFalse(live.alive(gone))
        self.assertFalse(live.alive(self.marker("auto-speech-autoplay-enabled", "never", self.old)))

    def test_stale_markers_are_listed_and_doctor_names_them(self):
        for n in range(3):
            self.marker("auto-speech-narrate-sessions", f"old-{n}", self.old)
        self.marker("auto-speech-autoplay-enabled", "old-ap", self.old)
        self.marker("auto-speech-autoplay-enabled", "fresh", self.now)
        stale = SessionLiveness(self.home, clock=lambda: self.now).stale()
        self.assertEqual(sorted(m.session_id for m in stale), ["old-0", "old-1", "old-2", "old-ap"])
        r = HealthReport()
        Doctor(home=self.home, tmp=self.home, now=lambda: self.now)._check_markers(r)
        [check] = r.checks
        self.assertEqual(check.status, Status.WARN)
        self.assertIn("4 stale", check.detail)

    def test_a_stale_autoplay_marker_is_not_an_enrolment(self):
        self.marker("auto-speech-autoplay-enabled", "gone", self.old)
        self.marker("auto-speech-autoplay-enabled", "here", self.now - 60)
        enrolment = SessionEnrollment(home=self.home, liveness=SessionLiveness(self.home, clock=lambda: self.now))
        self.assertFalse(enrolment.is_enrolled("gone"))
        self.assertTrue(enrolment.is_enrolled("here"))

    def test_the_daemon_skips_an_event_from_a_session_whose_marker_is_stale(self):
        svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
        svc._liveness = SessionLiveness(self.home, clock=lambda: self.now)
        gone = self.marker("auto-speech-narrate-sessions", "gone", self.old)
        here = self.marker("auto-speech-narrate-sessions", "here", self.now - 60)
        with patch.object(narrator_service, "_log") as log:
            self.assertFalse(svc._marker_live(gone, "gone"))
            self.assertFalse(svc._marker_live(gone, "gone"))
            self.assertTrue(svc._marker_live(here, "here"))
        self.assertEqual(log.call_count, 1, "logged once per stale session")


# --- T05: missed add ------------------------------------------------------------------


class TestMissedAdd(_Base):
    def test_a_caller_adds_a_cut_line_with_its_source_and_time(self):
        at = time.time() - 600
        reply = self.svc.dispatch_json(
            {"action": "missed_add", "text": "Part 6 of the briefing.", "source": "Master Desk", "at": at, "id": "brief-6"})
        self.assertEqual((reply["status"], reply["id"]), ("ok", "brief-6"))
        [m] = self.svc._missed.load()
        self.assertEqual((m.id, m.text, m.source, m.status, m.reason, m.queued_at),
                         ("brief-6", "Part 6 of the briefing.", "Master Desk", MISSED, REASON_ADDED, at))
        self.assertEqual(self.svc.dispatch_json({"action": "missed_add", "text": " "})["status"], "error")

    def test_the_control_lists_it_unplayed_under_its_source(self):
        self.svc.dispatch_json({"action": "missed_add", "text": "Part 6.", "source": "Master Desk", "id": "b6"})
        d = ControlDriver(HistoryStore(self.tmp / "history.toml"), history=self.svc._history, missed=self.svc._missed,
                          installed=lambda: False, start_worker=False, icon=None)
        self.addCleanup(d.close)
        [item] = [i for i in d.items() if i.get("unplayed")]
        self.assertEqual((item["id"], item["text"], item["source"]), ("missed:b6", "Part 6.", "Master Desk"))

    def test_the_cli_sends_it_through_the_daemon(self):
        client = MagicMock()
        client.missed_add.return_value = MagicMock(status="ok", raw={"id": "b7"})
        out = io.StringIO()
        args = cli.build_parser().parse_args(
            ["missed", "add", "--text", "Part 7.", "--source", "Master Desk", "--at", "23:36", "--id", "b7"])
        with redirect_stdout(out):
            rc = cli.handle_missed(args, client=client)
        self.assertEqual(rc, 0)
        kwargs = client.missed_add.call_args.kwargs
        self.assertEqual((client.missed_add.call_args.args[0], kwargs["source"], kwargs["line_id"]),
                         ("Part 7.", "Master Desk", "b7"))
        self.assertEqual(time.strftime("%H:%M", time.localtime(kwargs["at"])), "23:36")
        self.assertIn("b7", out.getvalue())

    def test_at_is_today_or_yesterday_when_still_ahead(self):
        now = time.mktime((2026, 10, 21, 23, 50, 0, 0, 0, -1))
        self.assertEqual(cli._parse_at("23:36", now), now - 14 * 60)
        self.assertEqual(cli._parse_at("23:55", now), now + 5 * 60 - 86400)
        with self.assertRaises(ValueError):
            cli._parse_at("25:00", now)


if __name__ == "__main__":
    unittest.main(verbosity=2)
