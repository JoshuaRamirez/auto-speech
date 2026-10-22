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

    def test_a_done_line_blocks_for_ten_minutes_then_may_play_again(self):
        self.ledger.admit(LINE)
        self.ledger.done(LINE)
        self.clock.t += DEDUP_DONE_S - 1
        self.assertEqual(self.ledger.admit(LINE).state, DONE)
        self.clock.t += 2
        self.assertIsNone(self.ledger.admit(LINE), "past ten minutes the same id is a new line")

    def test_a_dropped_line_is_admitted_again(self):
        self.ledger.admit(LINE)
        self.ledger.dropped(LINE, REASON_STOPPED)
        self.assertEqual(self.ledger.answer(LINE)["state"], DROPPED)
        self.assertIsNone(self.ledger.admit(LINE), "a replay follows a real interruption")
        self.assertEqual(self.ledger.answer(LINE)["sends"], 2)

    def test_ids_are_kept_an_hour(self):
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
        self.synth = _SlowSynth(SLOW_S)
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

        # His resume replays what the stop set aside, under the same id.
        self.svc.dispatch_json({"action": "resume"})
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
        self.assertIn("not seen in the last hour", cli._line_status_text({"line_id": "x", "state": "unknown"}))

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
