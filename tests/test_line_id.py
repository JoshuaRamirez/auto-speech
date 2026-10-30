"""Unit tests for ADR-025: one line id, one play, even under heavy load
(#m1022-0045-542f).

On 2026-10-22 a pegged Mac took 2.5 minutes to synthesize one line, and the
same line was sent three more times and played four times. Here a slow
synthesizer stub stands in for the load: it sleeps (AUTO_SPEECH_TEST_SLOW_SYNTH_S,
default 3 s) before it writes a short silent WAV.

Nothing is heard: the sink records and never starts mpv, and the runner sets
AUTO_SPEECH_MPV_AO=null besides. The ledger, the missed list and the stopped
state are in a temporary folder; the daemon client talks to a stand-in on its
own socket. Run directly or through tests/run_all.sh."""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
import argparse
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
import wave
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("AUTO_SPEECH_TMP_ROOT", tempfile.mkdtemp(prefix="auto-speech-tests-"))

import cli  # noqa: E402
import narrator_service  # noqa: E402
from config_constants import DEFAULT_SPEED, DEFAULT_VOICE_ID  # noqa: E402
from daemon_client import DaemonClient  # noqa: E402
from line_ledger import (  # noqa: E402
    DEDUP_DONE_S,
    DONE,
    DROPPED,
    KEEP_S,
    PLAYING,
    QUEUED,
    LineLedger,
    clean_line_id,
)
from missed_speech import REASON_STOPPED, MissedList  # noqa: E402

SLOW_S = float(os.environ.get("AUTO_SPEECH_TEST_SLOW_SYNTH_S") or 3.0)
LINE = "2026-10-22-003532.636-desk"


class _Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _silent_wav(path: Path, seconds: float = 0.05) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * int(16000 * seconds))


class _SlowSynth:
    """Synthesis under a pegged Mac: every line takes `delay` seconds."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls: list[str] = []
        self.started = threading.Event()

    def synthesize_one(self, text, profile, out) -> bool:
        self.calls.append(text)
        self.started.set()
        time.sleep(self.delay)
        _silent_wav(Path(out))
        return True


class _Sink:
    """Records what it was asked to play; never plays anything."""

    def __init__(self) -> None:
        self.played: list[Path] = []
        self.last_hold = None
        self.is_playing = False

    def play(self, wav_path, timeout=None, *, hold=True, already_waited=0.0, on_hold=None, on_start=None) -> None:
        if on_start is not None:
            on_start()
        self.played.append(Path(wav_path))

    def interrupt(self) -> None:
        pass


# --- the ledger -------------------------------------------------------------------


class TestLedger(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="auto-speech-lines-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.clock = _Clock()
        self.path = self.tmp / "lines.json"
        self.ledger = LineLedger(self.path, clock=self.clock)

    def test_a_second_send_of_a_queued_or_playing_line_is_acknowledged_not_queued(self):
        self.assertIsNone(self.ledger.admit(LINE, "The 00:35 line."))
        again = self.ledger.admit(LINE, "The 00:35 line.")
        self.assertEqual((again.state, again.sends), (QUEUED, 2))
        self.ledger.taken(LINE)
        self.ledger.playing(LINE)
        self.assertEqual(self.ledger.admit(LINE).state, PLAYING)

    def test_a_done_line_blocks_while_it_is_kept_then_may_play_again(self):
        self.ledger.admit(LINE)
        self.ledger.done(LINE)
        self.clock.t += DEDUP_DONE_S - 1
        self.assertEqual(self.ledger.admit(LINE).state, DONE)
        self.clock.t += 2
        self.assertIsNone(self.ledger.admit(LINE), "past its keeping (a day) the same id is a new line")

    def test_a_dropped_line_is_admitted_again(self):
        self.ledger.admit(LINE)
        self.ledger.dropped(LINE, REASON_STOPPED)
        self.assertEqual(self.ledger.answer(LINE)["state"], DROPPED)
        self.assertIsNone(self.ledger.admit(LINE), "a replay follows a real interruption")
        self.assertEqual(self.ledger.answer(LINE)["sends"], 2)

    def test_ids_are_kept_a_day(self):
        self.ledger.admit(LINE)
        self.ledger.done(LINE)
        self.clock.t += KEEP_S - 5
        self.ledger.admit("other")
        self.assertEqual(self.ledger.answer(LINE)["state"], DONE)
        self.clock.t += 10
        self.ledger.admit("third")
        self.assertEqual(self.ledger.answer(LINE)["state"], "unknown")

    def test_a_restart_keeps_the_ids_and_reads_lines_in_flight_as_lost(self):
        self.ledger.admit("played")
        self.ledger.done("played")
        self.ledger.admit("in-flight")
        self.ledger.taken("in-flight")
        self.ledger.synth_started("in-flight")
        after = LineLedger(self.path, clock=self.clock)
        self.assertEqual(after.answer("played")["state"], DONE)
        self.assertIsNotNone(after.admit("played"), "a done line still dedups after a restart")
        lost = after.answer("in-flight")
        self.assertEqual((lost["state"], lost["reason"]), (DROPPED, "restart"))

    def test_the_answer_has_its_timestamps_and_no_internal_fields(self):
        self.ledger.admit(LINE, "x" * 200)
        self.ledger.taken(LINE)
        self.clock.t += 3
        self.ledger.synth_started(LINE)
        ans = self.ledger.answer(LINE)
        self.assertEqual(ans["state"], QUEUED)
        self.assertEqual(ans["synth_started"] - ans["queued_at"], 3)
        self.assertNotIn("in_pass", ans)
        self.assertEqual(ans["id"], LINE, "the shape desk-speaker reads: \"line\": {\"id\", \"state\"}")
        self.assertLessEqual(len(ans["text"]), 60)
        json.dumps(ans)

    def test_settle_drops_only_a_line_the_worker_had_in_hand(self):
        self.ledger.admit("a")
        self.ledger.settle("a", "failed")
        self.assertEqual(self.ledger.answer("a")["state"], QUEUED, "not taken: still queued")
        self.ledger.taken("a")
        self.ledger.requeued("a", retry=True)
        self.ledger.settle("a", "failed")
        self.assertEqual(self.ledger.answer("a")["state"], QUEUED, "queued again by a retry")
        self.ledger.taken("a")
        self.ledger.settle("a", "failed")
        self.assertEqual((self.ledger.answer("a")["state"], self.ledger.answer("a")["reason"]), (DROPPED, "failed"))

    def test_clean_line_id(self):
        self.assertEqual(clean_line_id("  " + LINE + " "), LINE)
        for bad in (None, "", "   ", True, "a\nb", "x" * 500, {"id": 1}):
            self.assertEqual(clean_line_id(bad), "", bad)


# --- the daemon under a slow synthesizer --------------------------------------------


class _Daemon(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="auto-speech-lineid-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for name, value in (("STOPPED_FILE", self.tmp / "stopped.json"), ("SYNTH_DIR", self.tmp / "synth")):
            p = patch.object(narrator_service, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.sink = _Sink()
        self.synth = _SlowSynth(getattr(self, "synth_s", SLOW_S))
        self.svc = self.service()
        self.worker = threading.Thread(target=self.svc._tts_worker, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop_worker)

    def service(self):
        svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
        svc._config = {"synthesis_timeout_factor": 0}  # no budget: the stub is slow on purpose
        svc._sink = self.sink
        svc._synth = self.synth
        svc._tts_executor = None
        svc._tts_initialized = True
        svc._profile = types.SimpleNamespace(voice_id=DEFAULT_VOICE_ID, speed=DEFAULT_SPEED, chars_per_second=15.0)
        svc._state_lock = threading.Lock()
        svc._engine_state = "IDLE"
        svc._active_priority = None
        svc._active_item = None
        svc._interrupted = False
        svc._preempted = False
        svc._dropped_phases = 0
        svc._missed = MissedList(self.tmp / "missed.json")
        svc._history = None
        svc._lines = LineLedger(self.tmp / "lines.json")
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
        svc._tts_queue  # built here, as __init__ does, before the worker thread can race to build its own
        return svc

    def stop_worker(self) -> None:
        self.svc._tts_queue.put(None)
        self.worker.join(timeout=SLOW_S * 4 + 5)

    def speak(self, text: str, line_id: str = LINE) -> dict:
        return self.svc.dispatch_json({"action": "speak", "text": text, "priority": 2, "line_id": line_id})

    def line(self, line_id: str = LINE) -> dict:
        return self.svc.dispatch_json({"action": "status", "line_id": line_id})["line"]

    def wait_for(self, check, timeout: float, show=None) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if check():
                return
            time.sleep(0.02)
        self.fail(f"not reached in {timeout:g} s" + (f": {show()}" if show else ""))


class TestSlowSynthesis(_Daemon):
    def test_the_same_id_sent_three_times_plays_once_with_three_acknowledgements(self):
        text = "The Master Desk's 00:35 line."
        first = self.speak(text)
        self.assertEqual((first["status"], first["line_id"]), ("queued", LINE))
        self.assertTrue(self.synth.started.wait(SLOW_S + 5), "synthesis began")

        # desk-speaker times out on the socket and sends the line again, twice.
        acks = [first, self.speak(text), self.speak(text)]
        for ack in acks[1:]:
            self.assertEqual(ack["status"], "ok")
            self.assertTrue(ack["duplicate"])
            self.assertEqual(ack["line"]["state"], QUEUED)
            self.assertIsNotNone(ack["line"]["synth_started"], "the caller's wait is informed: it synthesizes")

        self.wait_for(lambda: self.line()["state"] == DONE, SLOW_S * 3 + 5)
        self.assertEqual(len(self.synth.calls), 1, "synthesized once")
        self.assertEqual(len(self.sink.played), 1, "played once")
        done = self.line()
        self.assertEqual((done["sends"], done["plays"]), (3, 1))
        self.assertTrue(done["queued_at"] <= done["synth_started"] <= done["playing_at"] <= done["done_at"])

        late = self.speak(text)
        self.assertEqual((late["duplicate"], late["line"]["state"]), (True, DONE), "a send after it played: done")
        time.sleep(0.2)
        self.assertEqual(len(self.sink.played), 1)

    def test_while_synthesizing_the_status_says_queued_with_synth_started(self):
        self.speak("A slow line.")
        self.assertTrue(self.synth.started.wait(SLOW_S + 5))
        reply = self.svc.dispatch_json({"action": "status", "line_id": LINE})
        self.assertEqual(reply["line"]["state"], QUEUED)
        self.assertIsNotNone(reply["line"]["synth_started"])
        self.assertEqual(reply["line_in_flight"]["line_id"], LINE)
        self.assertEqual(self.svc.dispatch_json({"action": "status", "line_id": "never-sent"})["line"]["state"], "unknown")
        self.assertNotIn("line", self.svc.dispatch_json({"action": "status"}))

    def test_different_ids_each_play(self):
        self.speak("One.", "a")
        self.speak("Two.", "b")
        self.wait_for(lambda: self.line("b")["state"] == DONE, SLOW_S * 4 + 5)
        self.assertEqual(len(self.sink.played), 2)

    def test_a_line_dropped_by_a_stop_is_reported_dropped_and_admitted_again(self):
        self.speak("In flight.", "first")
        self.assertTrue(self.synth.started.wait(SLOW_S + 5))
        self.speak("Waiting behind it.", "second")
        self.svc.dispatch_json({"action": "interrupt"})  # his stop
        self.wait_for(lambda: self.line("second")["state"] == DROPPED, SLOW_S * 3 + 5)
        self.wait_for(lambda: self.line("first")["state"] == DROPPED, SLOW_S * 3 + 5, lambda: self.line("first"))
        self.assertEqual(self.sink.played, [], "nothing plays after a stop")
        dropped = self.line("second")
        self.assertEqual(dropped["reason"], REASON_STOPPED)
        self.assertIsNotNone(dropped["dropped_at"])
        self.assertEqual(self.line("first")["reason"], "interrupted", "cut mid-synthesis by the stop")

        # His resume lifts the stop only (#m1030-1241-6e0a); his play of the
        # set-aside line plays it, under the same id.
        self.svc.dispatch_json({"action": "resume"})
        time.sleep(SLOW_S)
        self.assertEqual((self.line("second")["state"], self.sink.played), (DROPPED, []), "not played by the resume")
        [entry] = [m for m in self.svc._missed.load() if m.line_id == "second"]
        self.assertEqual(self.svc._play_missed_ids([entry.id]), 1)
        self.wait_for(lambda: self.line("second")["state"] == DONE, SLOW_S * 3 + 5)
        self.assertEqual(len(self.sink.played), 1)
        again = self.speak("Waiting behind it.", "second")
        self.assertEqual((again.get("duplicate"), again["line"]["state"]), (True, DONE))

    def test_a_line_that_fails_is_reported_dropped(self):
        self.synth.synthesize_one = lambda text, profile, out: False  # no audio
        self.speak("Nothing comes out.")
        self.wait_for(lambda: self.line()["state"] == DROPPED, 5)
        self.assertEqual(self.line()["reason"], "failed")
        self.assertIsNone(self.speak("Nothing comes out.").get("duplicate"), "a failed line may be sent again")


# --- ADR-026: one line, one play across his stop and resume (#m1027-1628-c253) -------------
#
# His 2026-10-27 16:27: "Why am I hearing duplication when you say things?" At 16:23 a desk line
# came in while speech was stopped; desk-speaker kept it in its pending folder AND put it on the
# missed list with no id. His play replayed the missed copy (no id), then desk-speaker's resend
# (with the id) played it again. At 13:03 three lines each played three times: each was on the
# missed list three times (two set-asides in a meeting and desk-speaker's own add), none with an id.


class TestOnePlayAcrossStop(_Daemon):
    synth_s = 0.02

    def missed(self):
        return self.svc._missed.load()

    def played_count(self) -> int:
        return len(self.sink.played)

    def settle(self) -> None:
        time.sleep(0.3)

    def stop(self) -> None:
        self.svc._stop_speech(by="test")

    def test_a_line_queued_while_stopped_stays_set_aside_on_resume_and_plays_once_on_his_play(self):
        # #m1030-1241-6e0a replaces "plays once on resume": a resume lifts the
        # stop only. The line stays on the missed list (a resend of it stays
        # there too) until his Play all or Play from, and then plays once.
        self.stop()
        self.assertEqual(self.speak("Held across his stop.")["status"], "queued")
        self.wait_for(lambda: self.line()["state"] == DROPPED, 5, self.line)
        [held] = self.missed()
        self.assertEqual((held.line_id, held.reason), (LINE, REASON_STOPPED), "the narrator holds it, under its id")
        self.assertEqual(self.played_count(), 0)

        self.svc.dispatch_json({"action": "resume"})
        resend = self.speak("Held across his stop.")  # a caller's copy after the stop lifts
        self.assertEqual((resend.get("duplicate"), resend.get("set_aside"), resend.get("missed_id")),
                         (True, True, held.id), "kept on the missed list, not queued")
        self.settle()
        self.assertEqual(self.played_count(), 0, "nothing set aside plays by itself after the resume")
        self.assertEqual([m.id for m in self.missed()], [held.id], "one entry, still there")

        self.assertEqual(self.svc._play_missed_ids([held.id]), 1)  # his Play all
        self.wait_for(lambda: self.line()["state"] == DONE, 5, self.line)
        again = self.speak("Held across his stop.")
        self.assertEqual((again.get("duplicate"), again["line"]["state"]), (True, DONE))
        self.settle()
        self.assertEqual(self.played_count(), 1, "played exactly once")
        self.assertEqual(self.missed(), [], "off the missed list once heard")

    def test_the_same_line_by_both_paths_plays_once_whichever_comes_first(self):
        # The 16:23 order: held while stopped, his play replays the missed copy, desk-speaker resends.
        self.stop()
        self.svc.dispatch_json({"action": "missed_add", "text": "My advice.", "source": "desk-say x.txt",
                                "line_id": LINE})
        [entry] = self.missed()
        self.assertEqual(self.svc._play_missed_ids([entry.id]), 1)
        self.wait_for(lambda: self.line()["state"] == DONE, 5, self.line)
        self.assertTrue(self.speak("My advice.").get("duplicate"))
        self.settle()
        self.assertEqual(self.played_count(), 1)

        # The other order: the resend lands first, then his play of the (stale) missed copy.
        self.stop()
        other = "2026-10-27-162557.391-desk"
        self.svc.dispatch_json({"action": "missed_add", "text": "Comments.", "line_id": other})
        [entry] = self.missed()
        self.svc._resume_speech()
        self.speak("Comments.", other)
        self.wait_for(lambda: self.line(other)["state"] == DONE, 5)
        self.assertEqual(self.missed(), [], "the copy on the list left when the line played")
        self.svc._play_missed_ids([entry.id])
        self.settle()
        self.assertEqual(self.played_count(), 2, "each line once")

    def test_a_line_set_aside_twice_and_added_by_its_caller_is_one_entry_and_plays_once(self):
        # The 13:03 triples: two set-asides in a meeting (a send and its retry) and desk-speaker's add.
        from missed_speech import REASON_MEETING
        from missed_speech import run_action
        from priority_arbiter import QueueItem

        for _ in range(2):
            self.svc._lines.admit(LINE, "Status update.")
            self.svc._set_aside("Status update.", QueueItem(priority=2, payload="Status update.", line_id=LINE,
                                                            origin_file=f"{LINE}.txt"), reason=REASON_MEETING)
        added = self.svc.dispatch_json({"action": "missed_add", "text": "Status update.", "source": f"desk-say {LINE}.txt",
                                        "line_id": LINE})
        self.assertTrue(added.get("duplicate"), "the narrator holds it already: the same entry")
        [entry] = self.missed()
        self.assertEqual((entry.line_id, entry.reason, entry.origin_file), (LINE, REASON_MEETING, f"{LINE}.txt"))

        result = run_action("play", missed=self.svc._missed, client=narrator_service._InProcessClient(self.svc))
        self.assertTrue(result.ok, result.text)
        self.wait_for(lambda: self.line()["state"] == DONE, 5, self.line)
        self.settle()
        self.assertEqual(self.played_count(), 1)
        self.assertEqual(self.missed(), [])

    def test_a_line_already_heard_is_not_added_to_the_missed_list(self):
        self.speak("Heard once.")
        self.wait_for(lambda: self.line()["state"] == DONE, 5)
        reply = self.svc.dispatch_json({"action": "missed_add", "text": "Heard once.", "line_id": LINE})
        self.assertEqual((reply["status"], reply.get("duplicate")), ("ok", True))
        self.assertEqual(self.missed(), [])

    def test_a_replay_carries_the_lines_sender_and_file(self):
        self.stop()
        self.svc.dispatch_json({"action": "speak", "text": "From the desk.", "priority": 2, "line_id": LINE,
                                "session_id": "sess-1", "origin_file": f"{LINE}.txt"})
        self.wait_for(lambda: self.line()["state"] == DROPPED, 5)
        client = MagicMock()
        from missed_speech import play_ids

        play_ids([self.missed()[0].id], missed=self.svc._missed, client=client)
        kwargs = client.speak.call_args.kwargs
        self.assertEqual((kwargs["line_id"], kwargs["session_id"], kwargs["origin_file"]), (LINE, "sess-1", f"{LINE}.txt"))


class TestLedgerBound(unittest.TestCase):
    def test_the_ledger_keeps_at_most_max_lines_dropping_the_oldest_settled(self):
        import line_ledger

        tmp = Path(tempfile.mkdtemp(prefix="auto-speech-lines-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        clock = _Clock()
        with patch.object(line_ledger, "MAX_LINES", 5):
            ledger = LineLedger(tmp / "lines.json", clock=clock)
            for i in range(8):
                clock.t += 1
                ledger.admit(f"l{i}")
                ledger.done(f"l{i}")
            kept = json.loads((tmp / "lines.json").read_text())["lines"]
        self.assertLessEqual(len(kept), 6)  # the bound, plus the line just admitted
        self.assertEqual(ledger.answer("l0")["state"], "unknown", "the oldest went first")
        self.assertEqual(ledger.answer("l7")["state"], DONE)

    def test_the_old_tmp_file_is_read_once_when_the_new_one_is_not_there(self):
        tmp = Path(tempfile.mkdtemp(prefix="auto-speech-lines-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        old = LineLedger(tmp / "old.json")
        old.admit(LINE)
        old.done(LINE)
        moved = LineLedger(tmp / "new.json", legacy=tmp / "old.json")
        self.assertEqual(moved.answer(LINE)["state"], DONE)
        self.assertTrue((tmp / "new.json").exists())

    def test_the_live_ledger_sits_beside_the_missed_list_and_a_test_root_moves_it(self):
        from line_ledger import default_lines_path

        with patch.dict(os.environ, {"AUTO_SPEECH_LINES_FILE": "", "AUTO_SPEECH_TMP_ROOT": ""}):
            self.assertEqual(default_lines_path(), Path.home() / ".local/state/auto-speech/lines.json")
        with patch.dict(os.environ, {"AUTO_SPEECH_LINES_FILE": "", "AUTO_SPEECH_TMP_ROOT": "/x/y"}):
            self.assertEqual(default_lines_path(), Path("/x/y/auto-speech-lines.json"))


class TestOrphanedPlayer(unittest.TestCase):
    """The 11:01 narrator's mpv outlived it (SIGKILL after a paused line held it): the next start stops it."""

    OWN = "/opt/homebrew/bin/mpv --really-quiet --no-video --keep-open=no --idle=no --input-media-keys=no /tmp/x.wav"

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="auto-speech-mpvpid-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pid_file = self.tmp / "auto-speech-mpv.pid"
        self.pid_file.write_text("4242")
        self.killed: list[tuple[int, int]] = []

    def reap(self, found):
        from native_audio_sink import reap_orphaned_player

        alive = {"v": found}

        def ps(pid):
            return alive["v"]

        def kill(pid, sig):
            self.killed.append((pid, sig))
            alive["v"] = None

        return reap_orphaned_player(self.pid_file, ps=ps, kill=kill, wait_s=0.2)

    def test_an_orphaned_player_of_ours_is_stopped(self):
        import signal as _signal

        self.assertEqual(self.reap((1, self.OWN)), 4242)
        self.assertEqual(self.killed, [(4242, _signal.SIGTERM)])
        self.assertFalse(self.pid_file.exists())

    def test_a_player_with_a_live_parent_is_never_touched(self):
        self.assertIsNone(self.reap((777, self.OWN)))
        self.assertEqual(self.killed, [])

    def test_a_process_that_is_not_our_player_is_never_touched(self):
        self.assertIsNone(self.reap((1, "/opt/homebrew/bin/mpv some-song.mp3")))
        self.assertIsNone(self.reap((1, "/usr/bin/python3 --really-quiet --no-video --input-media-keys=no")))
        self.assertEqual(self.killed, [])

    def test_a_stale_pid_file_is_cleared(self):
        self.assertIsNone(self.reap(None))
        self.assertFalse(self.pid_file.exists())



# --- the callers ------------------------------------------------------------------------


class _SlowDaemon:
    """A stand-in daemon on its own socket that answers the first speak late,
    after the client's timeout, the way the pegged narrator did."""

    def __init__(self, path: str, late_s: float) -> None:
        self.path = path
        self.late_s = late_s
        self.payloads: list[dict] = []
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        self.sock.listen(8)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                data = b""
                while b"\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                payload = json.loads(data.partition(b"\n")[0] or b"{}")
                self.payloads.append(payload)
                if len(self.payloads) == 1:
                    time.sleep(self.late_s)
                try:
                    conn.sendall(json.dumps({"status": "queued", "line_id": payload.get("line_id")}).encode() + b"\n")
                except OSError:
                    pass

    def close(self) -> None:
        self.sock.close()


class TestClient(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="as-", dir="/tmp")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "d.sock")

    def test_the_clients_own_retry_after_a_timeout_carries_the_same_id(self):
        daemon = _SlowDaemon(self.path, late_s=0.6)
        self.addCleanup(daemon.close)
        client = DaemonClient(socket_path=self.path, timeout=0.2, max_retries=2)
        resp = client.speak("A line.")
        self.assertGreaterEqual(len(daemon.payloads), 2, "the first reply came too late, so the client sent again")
        ids = {p["line_id"] for p in daemon.payloads}
        self.assertEqual(len(ids), 1, "every retry carried one id")
        self.assertEqual(resp.raw["line_id"], ids.pop())

    def test_a_callers_id_is_sent_as_line_id(self):
        daemon = _SlowDaemon(self.path, late_s=0.0)
        self.addCleanup(daemon.close)
        DaemonClient(socket_path=self.path).speak("A line.", line_id=LINE)
        DaemonClient(socket_path=self.path).status(line_id=LINE)
        self.assertEqual(daemon.payloads[0]["line_id"], LINE)
        self.assertEqual(daemon.payloads[1], {"action": "status", "line_id": LINE})


class TestCli(unittest.TestCase):
    def test_narrate_status_answers_for_an_id(self):
        now = time.time()
        raw = {
            "status": "ok",
            "daemon_state": "SYNTHESIZING",
            "queued": 0,
            "stopped": False,
            "line": {"line_id": LINE, "state": "queued", "queued_at": now - 150, "synth_started": now - 148, "sends": 4},
        }
        client = MagicMock()
        client._socket_path.is_socket.return_value = True
        client.status.return_value = MagicMock(raw=raw)
        args = argparse.Namespace(narrate_action="status", session_id="none", line_id=LINE)
        out = io.StringIO()
        with patch.object(cli, "DaemonClient", return_value=client), patch.object(Path, "home", return_value=Path("/nonexistent")), redirect_stdout(out):
            self.assertEqual(cli.handle_narrate(args), 0)
        client.status.assert_called_once_with(line_id=LINE)
        text = out.getvalue()
        self.assertIn(f"line {LINE}: queued since", text)
        self.assertIn("synthesizing since", text)
        self.assertIn("sent 4 times, queued once", text)

    def test_line_status_text_for_each_state(self):
        t = time.time()
        self.assertIn("done at", cli._line_status_text({"line_id": "x", "state": "done", "done_at": t}))
        self.assertIn("(stopped)", cli._line_status_text({"line_id": "x", "state": "dropped", "dropped_at": t, "reason": "stopped"}))
        self.assertIn("not seen in the last day", cli._line_status_text({"line_id": "x", "state": "unknown"}))

    def test_missed_add_passes_the_speech_line_id_and_file(self):
        client = MagicMock()
        client.missed_add.return_value = MagicMock(status="ok", raw={"id": "e1", "line_id": LINE})
        args = cli.build_parser().parse_args(
            ["missed", "add", "--text", "A line.", "--source", "desk-say", "--line-id", LINE, "--origin-file", f"{LINE}.txt"])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.handle_missed(args, client=client), 0)
        kwargs = client.missed_add.call_args.kwargs
        self.assertEqual((kwargs["speech_line_id"], kwargs["origin_file"], kwargs["line_id"]), (LINE, f"{LINE}.txt", None))

    def test_speak_passes_the_line_id(self):
        client = MagicMock()
        client.speak.return_value = MagicMock(status="ok", raw={"line": {"line_id": LINE, "state": "done"}})
        args = argparse.Namespace(
            words=["hello"], source_hash=None, session_id="s", priority=2, voice=DEFAULT_VOICE_ID,
            speed=DEFAULT_SPEED, socket_path=None, line_id=LINE,
        )
        with patch.object(cli, "AutoplayGate") as gate:
            gate.return_value.worker_gated_off.return_value = False
            self.assertEqual(cli.handle_speak(args, client=client), 0)
        self.assertEqual(client.speak.call_args.kwargs["line_id"], LINE)


if __name__ == "__main__":
    unittest.main()
