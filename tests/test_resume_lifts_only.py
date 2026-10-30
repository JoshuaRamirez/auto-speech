"""A resume over the narrator's socket lifts his stop, and only that (#m1030-1241-6e0a).

His stop was at 11:00 on 2026-10-30; at 12:37 he turned speech back on and a
10:55 line the stop had set aside played by itself. desk-speaker (Desk-Core
0b09707), on `desk-speak on` or `desk-speaker resume` while the narrator
reports stopped, sends exactly
    {"action": "resume", "play_set_aside": false, "source": "desk-speak on"}
to the narrator's socket. Here that payload goes over a real Unix socket to a
narrator's socket server: the stop lifts, the set-aside line stays on the
missed list, nothing is queued, and the log names the source. A resume with no
play_set_aside (`auto-speech resume`) lifts only too; play_set_aside true is
the one way to ask for the stop's lines again.

Never the live socket: a private one under /tmp. No audio, no worker."""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
import json
import shutil
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
for _p in (str(SRC), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import narrator_service  # noqa: E402
from missed_speech import MISSED, REASON_STOPPED, MissedList  # noqa: E402
from priority_arbiter import QueueItem  # noqa: E402
from speech_history import HistoryStore, SpeechHistory  # noqa: E402

DESK_SPEAKER_PAYLOAD = {"action": "resume", "play_set_aside": False, "source": "desk-speak on"}
LINE = "2026-10-30-105507.372-desk"
TEXT = "Your speech player closed and reopened once."


class TestResumeOverTheSocket(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="as-resume-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = patch.object(narrator_service, "STOPPED_FILE", self.tmp / "stopped.json")
        p.start()
        self.addCleanup(p.stop)
        self.logs: list[str] = []
        lp = patch.object(narrator_service, "_log", self.logs.append)
        lp.start()
        self.addCleanup(lp.stop)
        cfg = narrator_service.load_config()
        store = HistoryStore(self.tmp / "history.toml")
        self.svc = narrator_service.NarratorService(
            socket_path=self.tmp / "d.sock", sink=MagicMock(), synth=MagicMock(), config=cfg,
            sound_yield_gate=MagicMock(), missed=MissedList(self.tmp / "missed.json"),
            speaker=MagicMock(), history=SpeechHistory(self.tmp / "history", store=store), control=MagicMock(),
        )
        self.svc._start_socket_server()
        self.addCleanup(self.svc._stop_socket_server)

    def send(self, payload: dict) -> dict:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(str(self.tmp / "d.sock"))
            s.sendall(json.dumps(payload).encode() + b"\n")
            data = b""
            while b"\n" not in data:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
        return json.loads(data.partition(b"\n")[0])

    def stop_with_a_line_set_aside(self) -> None:
        self.svc._stop_speech(by="test")
        self.svc._set_aside(TEXT, QueueItem(priority=2, payload=TEXT, line_id=LINE), reason=REASON_STOPPED)
        [m] = self.svc._missed.load()
        self.assertEqual((m.status, m.reason), (MISSED, REASON_STOPPED))

    def test_desk_speakers_exact_payload_lifts_the_stop_only_and_logs_its_source(self):
        self.stop_with_a_line_set_aside()
        reply = self.send(DESK_SPEAKER_PAYLOAD)
        self.assertEqual((reply["status"], reply["was_stopped"], reply["queued"], reply["set_aside"]),
                         ("ok", True, 0, 1))
        self.assertFalse(self.svc._is_stopped())
        self.assertIsNone(narrator_service._read_stopped(), "the stop's file is cleared")
        self.assertEqual(self.svc._tts_queue.qsize(), 0, "nothing set aside is queued")
        self.assertEqual([m.line_id for m in self.svc._missed.load()], [LINE], "it stays on the missed list")
        resumed = [line for line in self.logs if line.startswith("resumed:")]
        self.assertEqual(len(resumed), 1)
        self.assertIn("a socket resume (desk-speak on)", resumed[0])
        self.assertIn("stay on the missed list", resumed[0])

    def test_a_resume_without_the_flag_lifts_only_too(self):
        self.stop_with_a_line_set_aside()
        reply = self.send({"action": "resume"})  # `auto-speech resume`, playback_control resume
        self.assertEqual((reply["was_stopped"], reply["queued"], reply["set_aside"]), (True, 0, 1))
        self.assertEqual(self.svc._tts_queue.qsize(), 0)

    def test_play_set_aside_true_is_the_one_way_to_queue_the_stops_lines_again(self):
        self.stop_with_a_line_set_aside()
        reply = self.send({"action": "resume", "play_set_aside": True, "source": "a test"})
        self.assertEqual((reply["was_stopped"], reply["queued"]), (True, 1))
        self.assertEqual(self.svc._tts_queue.qsize(), 1)

    def test_not_stopped_is_answered_and_changes_nothing(self):
        reply = self.send(DESK_SPEAKER_PAYLOAD)
        self.assertEqual((reply["was_stopped"], reply["queued"], reply["set_aside"]), (False, 0, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
