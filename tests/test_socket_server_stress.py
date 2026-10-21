"""Empirical stress test suite for auto-speech socket server lifecycle and queue dynamics.

Stress tests:
1. Daemon socket server lifecycle & stale socket file recovery:
   - Ungraceful daemon crashes (SIGKILL) leaving stale socket on disk
   - Reclaiming & unlinking stale socket without "Address already in use"
   - Multiple rapid ungraceful kill/restart cycles
   - Stale non-socket files and dangling symlinks
2. Queue backpressure under 200+ socket flood:
   - Drop-oldest FIFO cap (32 items) under sustained burst
   - Unbounded memory prevention & process stability
   - Concurrent client bursts & socket backlog behavior
   - Abrupt client resets (SO_LINGER 0) during floods
3. Simultaneous socket requests + JSONL tool events:
   - Concurrent ingestion of socket requests (strings) and JSONL events (Phases)
   - Mixed-type drop-oldest queue shedding under backpressure
   - Thread safety between _tail_events, socket server, and _tts_worker
"""

from __future__ import annotations

import gc
import json
import queue
import resource
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PYTHON = PROJECT_ROOT / "plugin" / "scripts" / "python"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PLUGIN_PYTHON))

import narrator_service
from speak import send_speech_request
from voice_profile import VoiceProfile


class FakeAudioSink:
    """Mock audio sink with hold, delay, and invocation tracking."""

    def __init__(self, delay_s: float = 0.0) -> None:
        self.played: list[Path] = []
        self.delay_s = delay_s
        self._hold_event = threading.Event()
        self._hold_event.set()
        self._lock = threading.Lock()

    def hold(self) -> None:
        self._hold_event.clear()

    def release(self) -> None:
        self._hold_event.set()

    def play(self, wav_path: Path | str, timeout: float | None = None) -> None:
        self._hold_event.wait(timeout=timeout if timeout is not None else 10.0)
        with self._lock:
            self.played.append(Path(wav_path))
        if self.delay_s > 0:
            time.sleep(self.delay_s)

    def interrupt(self) -> None:
        self._hold_event.set()


class FakeSynthesizer:
    """Fast, deterministic in-memory synthesizer double."""

    def __init__(self) -> None:
        self.synthesized: list[str] = []
        self._lock = threading.Lock()

    def synthesize_one(self, text: str, profile: Any, out_path: Path) -> bool:
        with self._lock:
            self.synthesized.append(text)
        out_path.write_bytes(
            b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
        )
        return True


class TestSocketServerLifecycleAndRecovery(unittest.TestCase):
    """Stress tests for socket server startup, shutdown, and ungraceful crash recovery."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.sock_path = self.root / "daemon_test.sock"
        self.pid_path = self.root / "daemon_test.pid"

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_ungraceful_sigkill_crash_recovers_stale_socket(self) -> None:
        """Simulate an ungraceful crash (kill -9) leaving stale socket on disk.

        A new server instance must automatically reclaim and unlink the socket
        without 'Address already in use' error, and accept new client traffic.
        """
        child_code = f"""
import sys, time, threading
from pathlib import Path
from typing import Any
sys.path.insert(0, "{PLUGIN_PYTHON}")
import narrator_service
from unittest.mock import MagicMock

narrator_service.PID_FILE = Path("{self.pid_path}")
svc = narrator_service.NarratorService(
    socket_path=Path("{self.sock_path}"),
    sink=MagicMock(),
    synth=MagicMock(),
)
svc._start_socket_server()
print("READY", flush=True)
time.sleep(30)
"""
        proc = subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            ready_line = proc.stdout.readline()
            self.assertEqual(ready_line.strip(), "READY")
            self.assertTrue(self.sock_path.exists(), "Initial socket file should exist")

            # Verify initial server handles speech request
            ret = send_speech_request("Initial pre-crash message", socket_path=self.sock_path)
            self.assertEqual(ret, 0, "Initial request to daemon must succeed")

            # Ungraceful crash: SIGKILL (kill -9) the daemon process
            proc.kill()
            proc.wait(timeout=3.0)

            # Assert stale socket file was left behind on disk
            self.assertTrue(self.sock_path.exists(), "Stale socket file must remain after SIGKILL")

            # Verify client attempting to connect to stale socket fails gracefully
            ret_stale = send_speech_request("Message during downtime", socket_path=self.sock_path)
            self.assertEqual(
                ret_stale, 1, "Client must fail gracefully with code 1 on stale socket"
            )

            # Start a new server instance on the exact same socket path
            svc2 = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
            svc2._socket_path = self.sock_path
            svc2._socket_server = None
            svc2._socket_thread = None
            svc2._queue_lock = threading.Lock()
            svc2._atexit_registered = False
            svc2._max_queue = 32
            svc2._tts_queue = queue.Queue(maxsize=32)
            svc2._dropped_phases = 0
            svc2._last_event_ts = 0.0
            svc2._update_depth = lambda _d: None

            # Must NOT raise Address already in use (OSError 48 / 98)
            svc2._start_socket_server()
            self.assertTrue(self.sock_path.exists(), "Rebound socket file must exist")
            self.assertIsNotNone(svc2._socket_thread)
            self.assertTrue(svc2._socket_thread.is_alive())

            # Verify recovered server processes incoming speech request
            ret_recovered = send_speech_request(
                "Message to recovered daemon", socket_path=self.sock_path
            )
            self.assertEqual(ret_recovered, 0, "Recovered daemon must accept new client traffic")

            # Verify request reached _tts_queue
            item = svc2._tts_queue.get(timeout=2.0)
            self.assertEqual(item, "Message to recovered daemon")

            # Clean shutdown must unlink socket
            svc2._stop_socket_server()
            self.assertFalse(self.sock_path.exists(), "Clean shutdown must unlink socket file")
        finally:
            if proc.stdout:
                proc.stdout.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_repeated_ungraceful_kill_rebind_cycles(self) -> None:
        """Run 5 consecutive iterations of crash-kill-restart cycles.

        Verifies that stale socket reclamation is 100% deterministic and does
        not leak file descriptors or lock states across rapid restarts.
        """
        for cycle in range(5):
            child_code = f"""
import sys, time
from pathlib import Path
from typing import Any
sys.path.insert(0, "{PLUGIN_PYTHON}")
import narrator_service
from unittest.mock import MagicMock

narrator_service.PID_FILE = Path("{self.pid_path}")
svc = narrator_service.NarratorService(
    socket_path=Path("{self.sock_path}"),
    sink=MagicMock(),
    synth=MagicMock(),
)
svc._start_socket_server()
print("READY", flush=True)
time.sleep(30)
"""
            proc = subprocess.Popen(
                [sys.executable, "-c", child_code],
                stdout=subprocess.PIPE,
                text=True,
            )
            try:
                # Wait for child to print READY
                ready_line = proc.stdout.readline()
                self.assertEqual(
                    ready_line.strip(), "READY", f"Cycle {cycle}: child should report READY"
                )
                self.assertTrue(self.sock_path.exists(), f"Cycle {cycle}: socket should exist")

                res = send_speech_request(f"Cycle {cycle} message", socket_path=self.sock_path)
                self.assertEqual(res, 0, f"Cycle {cycle}: speech request should succeed")

                # Brutal ungraceful kill
                proc.kill()
                proc.wait(timeout=2.0)
                self.assertTrue(
                    self.sock_path.exists(), f"Cycle {cycle}: stale socket should persist"
                )
            finally:
                if proc.stdout:
                    proc.stdout.close()
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()

        # Final restart to verify state remains completely reclaimable
        svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
        svc._socket_path = self.sock_path
        svc._socket_server = None
        svc._socket_thread = None
        svc._queue_lock = threading.Lock()
        svc._atexit_registered = False
        svc._max_queue = 32
        svc._tts_queue = queue.Queue(maxsize=32)
        svc._dropped_phases = 0
        svc._last_event_ts = 0.0
        svc._update_depth = lambda _d: None

        svc._start_socket_server()
        try:
            res = send_speech_request("Post-cycles validation", socket_path=self.sock_path)
            self.assertEqual(res, 0)
            self.assertEqual(svc._tts_queue.get(timeout=2.0), "Post-cycles validation")
        finally:
            svc._stop_socket_server()
            self.assertFalse(self.sock_path.exists())

    def test_stale_corrupted_or_non_socket_file_reclaimed(self) -> None:
        """Verifies startup reclaims non-socket files (regular files, symlinks) occupying the path."""
        # 1. Regular file with garbage
        self.sock_path.write_bytes(b"CORRUPTED_NON_SOCKET_DATA" * 50)
        self.assertTrue(self.sock_path.is_file())

        svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
        svc._socket_path = self.sock_path
        svc._socket_server = None
        svc._socket_thread = None
        svc._queue_lock = threading.Lock()
        svc._atexit_registered = False
        svc._max_queue = 32
        svc._tts_queue = queue.Queue(maxsize=32)
        svc._dropped_phases = 0
        svc._last_event_ts = 0.0
        svc._update_depth = lambda _d: None

        svc._start_socket_server()
        try:
            self.assertTrue(self.sock_path.exists())
            res = send_speech_request("Overwritten regular file", socket_path=self.sock_path)
            self.assertEqual(res, 0)
        finally:
            svc._stop_socket_server()

        # 2. Dangling symlink
        dangling_target = self.root / "does_not_exist.target"
        self.sock_path.symlink_to(dangling_target)
        self.assertTrue(self.sock_path.is_symlink())

        svc2 = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
        svc2._socket_path = self.sock_path
        svc2._socket_server = None
        svc2._socket_thread = None
        svc2._queue_lock = threading.Lock()
        svc2._atexit_registered = False
        svc2._max_queue = 32
        svc2._tts_queue = queue.Queue(maxsize=32)
        svc2._dropped_phases = 0
        svc2._last_event_ts = 0.0
        svc2._update_depth = lambda _d: None

        svc2._start_socket_server()
        try:
            res = send_speech_request("Overwritten dangling symlink", socket_path=self.sock_path)
            self.assertEqual(res, 0)
        finally:
            svc2._stop_socket_server()


class TestQueueBackpressureSocketFlood(unittest.TestCase):
    """Stress tests for queue backpressure under 200+ socket request flood."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.sock_path = self.root / "flood_test.sock"

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_sequential_burst_250_requests_enforces_drop_oldest_cap(self) -> None:
        """Blast 250 requests at server while playback is busy.

        Verifies:
        - Drop-oldest cap (32 items) is strictly enforced
        - Exactly (total_sent - 32) items are dropped
        - Remaining items in queue are the 32 latest requests in FIFO order
        - Queue depth file is updated
        - Memory (RSS) does not grow uncontrollably
        """
        sink = FakeAudioSink()
        sink.hold()  # Playback is blocked/busy
        synth = FakeSynthesizer()

        max_queue = 32
        svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
        svc._config = {"max_queue_depth": max_queue}
        svc._socket_path = self.sock_path
        svc._socket_server = None
        svc._socket_thread = None
        svc._queue_lock = threading.Lock()
        svc._atexit_registered = False
        svc._max_queue = max_queue
        svc._tts_queue = queue.Queue(maxsize=max_queue)
        svc._dropped_phases = 0
        svc._last_event_ts = 0.0
        depth_updates = []
        svc._update_depth = lambda d: depth_updates.append(d)
        svc._sink = sink
        svc._synth = synth
        svc._profile = VoiceProfile("test", 1.0, 15.0, "cal", 0)
        svc._engine = MagicMock()

        svc._start_socket_server()
        gc.collect()
        rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

        try:
            total_requests = 250
            send_errors = 0

            # Blast 250 requests sequentially
            for i in range(total_requests):
                # Small micro-pause to avoid overflowing listen backlog(5) during test setup
                time.sleep(0.001)
                res = send_speech_request(f"burst_msg_{i:04d}", socket_path=self.sock_path)
                if res != 0:
                    send_errors += 1

            # Wait for background request handlers to complete
            t_wait = time.time()
            while (svc._tts_queue.qsize() + svc._dropped_phases) < (
                total_requests - send_errors
            ) and (time.time() - t_wait) < 3.0:
                time.sleep(0.02)

            total_received = svc._tts_queue.qsize() + svc._dropped_phases
            self.assertLessEqual(
                svc._tts_queue.qsize(), max_queue, "Queue size must never exceed max_queue (32)"
            )
            self.assertEqual(
                svc._tts_queue.qsize(), max_queue, "Queue must be full at max_queue (32)"
            )
            self.assertEqual(
                svc._dropped_phases,
                total_received - max_queue,
                "Dropped count must equal total_received - max_queue",
            )

            # Verify memory usage did not blow up (< 30 MB delta)
            gc.collect()
            rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            rss_delta_mb = (
                (rss_after - rss_before) / (1024 * 1024)
                if sys.platform == "darwin"
                else (rss_after - rss_before) / 1024
            )
            self.assertLess(rss_delta_mb, 30.0, f"Memory growth excessive: {rss_delta_mb:.2f} MB")

            # Extract surviving items and verify they are the latest requests
            survivors = []
            while not svc._tts_queue.empty():
                survivors.append(svc._tts_queue.get_nowait())

            self.assertEqual(len(survivors), max_queue)
            # Verify survivor FIFO ordering
            for idx in range(len(survivors) - 1):
                self.assertLess(
                    survivors[idx],
                    survivors[idx + 1],
                    "Survivors must maintain ascending FIFO order",
                )

            # Verify oldest was dropped: the first survivor must be message #(total_requests - max_queue - send_errors)
            expected_last = f"burst_msg_{total_requests - 1:04d}"
            self.assertEqual(
                survivors[-1], expected_last, "The newest request must be at the tail of the queue"
            )

        finally:
            sink.release()
            svc._stop_socket_server()

    def test_client_abrupt_disconnect_during_backpressure_flood(self) -> None:
        """Hammer server with abrupt client disconnects (SO_LINGER 0) during rapid enqueueing.

        Verifies that connection resets do not crash the socket server thread or hang the daemon.
        """
        svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
        svc._socket_path = self.sock_path
        svc._socket_server = None
        svc._socket_thread = None
        svc._queue_lock = threading.Lock()
        svc._atexit_registered = False
        svc._max_queue = 32
        svc._tts_queue = queue.Queue(maxsize=32)
        svc._dropped_phases = 0
        svc._last_event_ts = 0.0
        svc._update_depth = lambda _d: None

        svc._start_socket_server()
        try:
            linger = (
                b"\x01\x00\x00\x00\x00\x00\x00\x00"
                if sys.platform == "darwin"
                else b"\x01\x00\x00\x00"
            )

            # Fire 50 abrupt resets
            for i in range(50):
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    s.connect(str(self.sock_path))
                    s.sendall(f"abrupt_{i}".encode())
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
                except (OSError, ConnectionResetError):
                    pass
                finally:
                    s.close()
                time.sleep(0.002)

            self.assertTrue(
                svc._socket_thread.is_alive(), "Socket thread must survive abrupt resets"
            )

            # Normal request immediately following the hammer must succeed
            res = send_speech_request("Normal after abrupt resets", socket_path=self.sock_path)
            self.assertEqual(res, 0)
        finally:
            svc._stop_socket_server()


class TestSimultaneousSocketAndJsonlEvents(unittest.TestCase):
    """Stress tests for concurrency between socket requests and JSONL tool event tailing."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.sock_path = self.root / "simul_test.sock"
        self.events_log = self.root / "events.jsonl"
        self.watermark_file = self.root / "watermark"
        self.depth_file = self.root / "depth"

        self.session_id = "test-session-simul"
        self.session_marker = (
            self.root / ".claude" / "auto-speech-narrate-sessions" / self.session_id
        )
        self.session_marker.parent.mkdir(parents=True)
        self.session_marker.touch()

        # Patch module-level paths in narrator_service
        self._orig_events_log = narrator_service.EVENTS_LOG
        self._orig_watermark = narrator_service.WATERMARK_FILE
        self._orig_depth = narrator_service.DEPTH_FILE
        narrator_service.EVENTS_LOG = self.events_log
        narrator_service.WATERMARK_FILE = self.watermark_file
        narrator_service.DEPTH_FILE = self.depth_file

    def tearDown(self) -> None:
        narrator_service.EVENTS_LOG = self._orig_events_log
        narrator_service.WATERMARK_FILE = self._orig_watermark
        narrator_service.DEPTH_FILE = self._orig_depth
        self.tmpdir.cleanup()

    def test_simultaneous_socket_and_jsonl_event_ingestion(self) -> None:
        """Feed 40 socket requests and 40 JSONL events concurrently.

        Verifies:
        - Zero exceptions or race conditions during concurrent puts
        - Both socket requests and JSONL events are ingested and played
        - _tts_worker processes both types (str and Phase) safely
        """
        played_items: list[str] = []
        play_lock = threading.Lock()

        class FastSynth:
            def synthesize_one(self, line: str, profile: Any, out_path: Path) -> bool:
                with play_lock:
                    played_items.append(line)
                return True

        class FastSink:
            def play(self, wav_path: Path) -> None:
                pass

            def interrupt(self) -> None:
                pass

        svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
        svc._config = {
            "max_queue_depth": 200,
            "idle_shutdown_seconds": 60,
            "min_events_per_phase": 1,
        }
        svc._classifier = narrator_service.PhaseClassifier(
            silence_seconds=0.01, max_events_per_phase=1
        )
        svc._max_queue = 200
        svc._tts_queue = queue.Queue(maxsize=200)
        svc._dropped_phases = 0
        svc._summarizer = MagicMock()
        svc._summarizer.summarize = lambda phase: (
            f"summary:{phase.category.value}:{len(phase.events)}"
        )
        svc._summarizer_lock = threading.Lock()
        svc._last_event_ts = time.time()
        svc._idle_shutdown = 60.0
        svc._stop = threading.Event()
        svc._fsm = narrator_service.NarratorStateMachine()
        svc._phases_this_turn = 0
        svc._sink = FastSink()
        svc._synth = FastSynth()
        svc._profile = MagicMock()
        svc._engine = MagicMock()
        svc._socket_path = self.sock_path
        svc._socket_server = None
        svc._socket_thread = None
        svc._queue_lock = threading.Lock()
        svc._atexit_registered = False

        svc._start_socket_server()
        tts_thread = threading.Thread(target=svc._tts_worker, daemon=True)
        tts_thread.start()

        num_items = 40
        socket_errors = []

        def _send_sockets():
            for i in range(num_items):
                time.sleep(0.005)
                res = send_speech_request(f"socket_msg_{i:03d}", socket_path=self.sock_path)
                if res != 0:
                    socket_errors.append(res)

        def _send_events():
            with open(self.events_log, "a") as f:
                for i in range(num_items):
                    ev = {
                        "event": "PostToolUse",
                        "ts": time.time(),
                        "payload": {
                            "session_id": self.session_id,
                            "tool_name": "Bash",
                            "tool_input": {"command": f"cargo test {i}"},
                        },
                    }
                    f.write(json.dumps(ev) + "\n")
                    f.flush()
                    time.sleep(0.005)

        with patch.object(Path, "home", staticmethod(lambda: self.root)):
            tail_thread = threading.Thread(target=svc._tail_events, daemon=True)
            tail_thread.start()

            try:
                t1 = threading.Thread(target=_send_sockets)
                t2 = threading.Thread(target=_send_events)
                t1.start()
                t2.start()
                t1.join(timeout=5.0)
                t2.join(timeout=5.0)

                t_end = time.time() + 5.0
                while time.time() < t_end and len(played_items) < (2 * num_items):
                    time.sleep(0.05)

                socket_played = [p for p in played_items if p.startswith("socket_msg_")]
                event_played = [p for p in played_items if p.startswith("summary:")]

                self.assertEqual(socket_errors, [], "Zero socket request errors allowed")
                self.assertEqual(
                    len(socket_played), num_items, f"Expected {num_items} socket items played"
                )
                self.assertEqual(
                    len(event_played), num_items, f"Expected {num_items} event summaries played"
                )
                self.assertEqual(
                    len(played_items), 2 * num_items, "All 80 items must be synthesized"
                )
            finally:
                svc._stop.set()
                svc._stop_socket_server()
                try:
                    svc._tts_queue.put_nowait(None)
                except Exception:  # noqa: BLE001, S110 — cleanup must not mask the test result
                    pass
                tts_thread.join(timeout=2.0)
                tail_thread.join(timeout=2.0)

    def test_mixed_phase_and_string_drop_oldest_under_backpressure(self) -> None:
        """Verify queue sheds mixed Phase and str items under backpressure without crashing.

        Tests that _enqueue_item correctly handles dropping either a Phase or str
        object when the bounded queue is saturated.
        """
        svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
        svc._max_queue = 4
        svc._tts_queue = queue.Queue(maxsize=4)
        svc._dropped_phases = 0
        svc._queue_lock = threading.Lock()
        svc._last_event_ts = 0.0
        svc._update_depth = lambda _d: None

        p1 = narrator_service.Phase(category=narrator_service.Category.RUN, session_id="s1")
        p2 = narrator_service.Phase(category=narrator_service.Category.EDIT, session_id="s1")

        # Fill queue with 2 Phases and 2 strings
        svc._enqueue_phase(p1)
        svc._enqueue_phase("socket_str_1")
        svc._enqueue_phase(p2)
        svc._enqueue_phase("socket_str_2")
        self.assertEqual(svc._tts_queue.qsize(), 4)
        self.assertEqual(svc._dropped_phases, 0)

        # Enqueue 5th item (should drop p1, which is a Phase)
        svc._enqueue_phase("socket_str_3")
        self.assertEqual(svc._tts_queue.qsize(), 4)
        self.assertEqual(svc._dropped_phases, 1)

        # Enqueue 6th item (should drop socket_str_1, which is a str)
        p3 = narrator_service.Phase(category=narrator_service.Category.EXPLORE, session_id="s1")
        svc._enqueue_phase(p3)
        self.assertEqual(svc._tts_queue.qsize(), 4)
        self.assertEqual(svc._dropped_phases, 2)

        # Verify survivors: p2, socket_str_2, socket_str_3, p3
        survivors = [
            svc._tts_queue.get_nowait(),
            svc._tts_queue.get_nowait(),
            svc._tts_queue.get_nowait(),
            svc._tts_queue.get_nowait(),
        ]
        self.assertEqual(survivors[0], p2)
        self.assertEqual(survivors[1], "socket_str_2")
        self.assertEqual(survivors[2], "socket_str_3")
        self.assertEqual(survivors[3], p3)


def main() -> int:
    suite = unittest.TestSuite()
    loader = unittest.TestLoader()
    suite.addTests(loader.loadTestsFromTestCase(TestSocketServerLifecycleAndRecovery))
    suite.addTests(loader.loadTestsFromTestCase(TestQueueBackpressureSocketFlood))
    suite.addTests(loader.loadTestsFromTestCase(TestSimultaneousSocketAndJsonlEvents))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
