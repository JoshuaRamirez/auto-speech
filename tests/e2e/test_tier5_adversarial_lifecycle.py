"""Tier 5: Adversarial Coverage Hardening for Daemon Lifecycle, Backpressure & Fault Tolerance.

Covers:
1. Socket Server Lifecycle & Stale Binding:
   - Server startup when stale socket file, regular file, or broken symlink exists on disk
   - 8+ rapid server stop/restart cycles verifying zero leaked threads or dangling sockets
   - Clean unlinking upon normal shutdown and idempotent start/stop calls
   - Active server protection via _existing_pid() preventing duplicate daemons
2. Queue Overload & FIFO Eviction (300+ requests flood):
   - Flooding daemon with 350 requests when queue cap is 128 items
   - Strict FIFO drop-oldest verification: latest 128 requests preserved in order, 222 oldest dropped
   - Multi-threaded concurrent flood (350 requests across 12 threads) testing _queue_lock integrity
   - Mixed-type backpressure shedding (socket strings + Phase objects)
3. Signal Handling & Interruption:
   - Graceful termination on SIGTERM and SIGINT with exit code 0 and artifact cleanup
   - Playback interruption on SIGTERM unblocking worker thread
   - Hard crash (SIGKILL) simulation and subsequent seamless recovery over stale artifacts
   - Dead and corrupt PID file recovery
4. Worker & Request Handler Adversarial Robustness:
   - Worker resilience against synthesizer exceptions without thread death
   - Abrupt client disconnects (SO_LINGER 0) handled gracefully
   - Whitespace and empty payloads filtered without polluting _tts_queue
"""

from __future__ import annotations

import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PYTHON = PROJECT_ROOT / "plugin" / "scripts" / "python"
if str(PLUGIN_PYTHON) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PYTHON))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import narrator_service  # noqa: E402
from narrator_phase_classifier import Category, Phase  # noqa: E402
from narrator_service import (  # noqa: E402
    NarratorService,
    _existing_pid,
    load_config,
)
from speak import send_speech_request  # noqa: E402
from tests.e2e.harness import VENV_PYTHON  # noqa: E402
from voice_profile import VoiceProfile  # noqa: E402


class BlockingMockSink:
    """Mock audio sink that can block on play until released, and tracks plays/interrupts."""

    def __init__(self) -> None:
        self.played: list[Path] = []
        self._block_event = threading.Event()
        self._block_event.set()
        self.interrupted_count = 0
        self._lock = threading.Lock()

    def block(self) -> None:
        self._block_event.clear()

    def release(self) -> None:
        self._block_event.set()

    def play(self, wav_path: Path) -> None:
        self._block_event.wait(timeout=10.0)
        with self._lock:
            self.played.append(wav_path)

    def interrupt(self) -> None:
        with self._lock:
            self.interrupted_count += 1
        self._block_event.set()


class FastDummySynthesizer:
    """Fast synthesizer creating minimal valid WAV byte payloads without MLX model latency."""

    def __init__(self, fail_first_n: int = 0) -> None:
        self.synthesized: list[str] = []
        self._fail_remaining = fail_first_n
        self._lock = threading.Lock()

    def synthesize_one(self, text: str, profile: Any, out_path: Path) -> bool:
        with self._lock:
            if self._fail_remaining > 0:
                self._fail_remaining -= 1
                raise RuntimeError(f"Simulated synthesis crash on: {text}")
            self.synthesized.append(text)
        out_path.write_bytes(
            b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
        )
        return True


class TestTier5AdversarialSocketLifecycle(unittest.TestCase):
    """Adversarial stress tests for UNIX domain socket server lifecycle and recovery."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(dir="/tmp", prefix="as_t5_sock_")
        self.root = Path(self.temp_dir.name)
        self.sock_path = self.root / "auto-speech-daemon.sock"
        self.pid_path = self.root / "daemon.pid"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_stale_socket_file_rebinding_on_startup(self) -> None:
        """Verifies server startup automatically unlinks stale socket files and re-binds cleanly."""
        # 1. Create a dummy socket and close it to leave a dead socket node on disk
        dead_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        dead_sock.bind(str(self.sock_path))
        dead_sock.close()
        self.assertTrue(self.sock_path.exists(), "Stale socket file must exist on disk before start")

        # 2. Boot NarratorService on this path
        cfg = load_config()
        svc = NarratorService(
            socket_path=self.sock_path,
            sink=BlockingMockSink(),
            synth=FastDummySynthesizer(),
            config=cfg,
        )
        svc._start_socket_server()

        try:
            self.assertTrue(self.sock_path.exists(), "Socket server must create active socket")
            self.assertIsNotNone(svc._socket_thread)
            self.assertTrue(svc._socket_thread.is_alive())

            # 3. Transmit request to newly bound socket
            res = send_speech_request("Speech after stale socket reclamation", socket_path=self.sock_path)
            self.assertEqual(res, 0, "Client must successfully communicate with rebounded socket")

            # Verify received item in queue
            item = svc._tts_queue.get(timeout=2.0)
            self.assertEqual(item, "Speech after stale socket reclamation")
        finally:
            svc._stop_socket_server()
            self.assertFalse(self.sock_path.exists(), "Clean stop must unlink socket file from disk")

    def test_stale_regular_file_and_dangling_symlink_reclaimed(self) -> None:
        """Verifies server startup reclaims regular files and dangling symlinks occupying the path."""
        # Case A: Regular file containing non-socket garbage
        self.sock_path.write_bytes(b"STALE_GARBAGE_BYTES" * 20)
        self.assertTrue(self.sock_path.is_file())

        cfg = load_config()
        svcA = NarratorService(
            socket_path=self.sock_path,
            sink=BlockingMockSink(),
            synth=FastDummySynthesizer(),
            config=cfg,
        )
        svcA._start_socket_server()
        try:
            resA = send_speech_request("Overwritten regular file", socket_path=self.sock_path)
            self.assertEqual(resA, 0)
            self.assertEqual(svcA._tts_queue.get(timeout=2.0), "Overwritten regular file")
        finally:
            svcA._stop_socket_server()
            self.assertFalse(self.sock_path.exists())

        # Case B: Dangling symlink pointing to nonexistent path
        dangling_target = self.root / "nonexistent_target.sock"
        self.sock_path.symlink_to(dangling_target)
        self.assertTrue(self.sock_path.is_symlink())

        svcB = NarratorService(
            socket_path=self.sock_path,
            sink=BlockingMockSink(),
            synth=FastDummySynthesizer(),
            config=cfg,
        )
        svcB._start_socket_server()
        try:
            resB = send_speech_request("Overwritten dangling symlink", socket_path=self.sock_path)
            self.assertEqual(resB, 0)
            self.assertEqual(svcB._tts_queue.get(timeout=2.0), "Overwritten dangling symlink")
        finally:
            svcB._stop_socket_server()
            self.assertFalse(self.sock_path.exists())

    def test_rapid_restart_cycles_no_socket_leak_or_thread_leak(self) -> None:
        """Runs 8 rapid server stop/restart cycles ensuring zero leaked threads or dangling sockets."""
        cfg = load_config()
        threads_before = threading.active_count()

        for cycle in range(8):
            svc = NarratorService(
                socket_path=self.sock_path,
                sink=BlockingMockSink(),
                synth=FastDummySynthesizer(),
                config=cfg,
            )
            svc._start_socket_server()
            self.assertTrue(self.sock_path.exists(), f"Cycle {cycle}: socket should exist")

            # Verify functionality
            res = send_speech_request(f"Cycle {cycle} message", socket_path=self.sock_path)
            self.assertEqual(res, 0, f"Cycle {cycle}: speech request should succeed")

            # Shutdown server
            svc._stop_socket_server()
            self.assertFalse(self.sock_path.exists(), f"Cycle {cycle}: socket must be unlinked")
            if svc._socket_thread is not None:
                self.assertFalse(svc._socket_thread.is_alive(), f"Cycle {cycle}: thread must be dead")

        # Give runtime a tiny slice for thread cleanups
        time.sleep(0.05)
        threads_after = threading.active_count()
        self.assertLessEqual(
            threads_after,
            threads_before + 1,
            f"Thread leak detected: before={threads_before}, after={threads_after}",
        )

    def test_active_server_protection_via_existing_pid(self) -> None:
        """Verifies active daemon running guards against duplicate daemon startup via _existing_pid()."""
        child_code = f"""
import sys, os, time, signal
from pathlib import Path
sys.path.insert(0, "{PLUGIN_PYTHON}")
import narrator_service
from unittest.mock import MagicMock

narrator_service.PID_FILE = Path("{self.pid_path}")
narrator_service.EVENTS_LOG = Path("{self.root}/events.jsonl")
narrator_service.DEPTH_FILE = Path("{self.root}/depth")
narrator_service.WATERMARK_FILE = Path("{self.root}/watermark")

cfg = narrator_service.load_config()
cfg["idle_shutdown_seconds"] = 60
svc = narrator_service.NarratorService(
    socket_path=Path("{self.sock_path}"),
    sink=MagicMock(),
    synth=MagicMock(),
    config=cfg,
)
ret = svc.run()
sys.exit(ret)
"""
        # Pass "/narrator_service.py" so ps cmdline matching succeeds
        proc = subprocess.Popen([str(VENV_PYTHON), "-c", child_code, "/narrator_service.py"])
        try:
            # Wait for child to initialize and write PID
            for _ in range(40):
                if self.pid_path.exists() and self.sock_path.exists():
                    break
                time.sleep(0.05)

            self.assertTrue(self.pid_path.exists(), "Primary daemon PID file must exist")
            self.assertTrue(self.sock_path.exists(), "Primary daemon socket must exist")

            # Attempt to launch second daemon with narrator_service.main()
            second_code = f"""
import sys
from pathlib import Path
sys.path.insert(0, "{PLUGIN_PYTHON}")
import narrator_service

narrator_service.PID_FILE = Path("{self.pid_path}")
sys.exit(narrator_service.main())
"""
            second_proc = subprocess.run(
                [str(VENV_PYTHON), "-c", second_code, "/narrator_service.py"],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            # Second daemon must detect running instance and exit with returncode 1
            self.assertEqual(second_proc.returncode, 1, "Second daemon must exit with code 1")
            self.assertIn(
                "already running",
                second_proc.stderr,
                "Second daemon stderr must indicate daemon already running",
            )

            # Primary daemon must remain alive and healthy
            res = send_speech_request("Primary daemon health check", socket_path=self.sock_path)
            self.assertEqual(res, 0, "Primary daemon must still serve traffic")
        finally:
            proc.terminate()
            proc.wait(timeout=5.0)

    def test_idempotent_socket_server_start_stop(self) -> None:
        """Verifies multiple consecutive calls to _start_socket_server and _stop_socket_server are safe."""
        cfg = load_config()
        svc = NarratorService(
            socket_path=self.sock_path,
            sink=BlockingMockSink(),
            synth=FastDummySynthesizer(),
            config=cfg,
        )

        # Idempotent start
        svc._start_socket_server()
        initial_server = svc._socket_server
        initial_thread = svc._socket_thread
        svc._start_socket_server()  # Second call should no-op
        self.assertIs(svc._socket_server, initial_server)
        self.assertIs(svc._socket_thread, initial_thread)

        # Idempotent stop
        svc._stop_socket_server()
        self.assertIsNone(svc._socket_server)
        self.assertIsNone(svc._socket_thread)
        self.assertFalse(self.sock_path.exists())

        # Second call to stop should not raise any exceptions
        svc._stop_socket_server()
        self.assertIsNone(svc._socket_server)


class TestTier5AdversarialQueueBackpressureFlood(unittest.TestCase):
    """Adversarial stress tests for queue capacity, 300+ request flood, and FIFO drop-oldest eviction."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(dir="/tmp", prefix="as_t5_flood_")
        self.root = Path(self.temp_dir.name)
        self.sock_path = self.root / "auto-speech-daemon.sock"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_queue_flood_350_requests_fifo_drop_oldest_eviction(self) -> None:
        """Floods daemon with 350 requests when queue cap is 128 items while audio playback is busy.

        Verifies:
        - All 350 client requests succeed (code 0) without hangs or timeouts
        - Queue depth is strictly capped at 128 items
        - Exactly 222 items are dropped (350 - 128 = 222)
        - Surviving items are strictly the 128 newest requests (req_0222 to req_0349)
        - Queue preserves strict FIFO ordering (earliest surviving first, newest last)
        - Starting worker drains queue to 0 and synthesizes surviving items in order
        """
        sink = BlockingMockSink()
        synth = FastDummySynthesizer()

        cfg = load_config()
        cfg["max_queue_depth"] = 128

        svc = NarratorService(
            socket_path=self.sock_path,
            sink=sink,
            synth=synth,
            config=cfg,
        )
        svc._profile = VoiceProfile("test", 1.0, 15.0, "cal", 0)
        svc._engine = MagicMock()

        depth_records: list[int] = []
        svc._update_depth = lambda d: depth_records.append(d)

        svc._start_socket_server()

        total_requests = 350
        max_cap = 128

        try:
            # Sequential blast of 350 requests while worker is unstarted (holding queue).
            # Read server response ACK (b"OK\n") to preserve strict serial arrival order
            # and prevent multi-threaded socket server enqueue race.
            for i in range(total_requests):
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    sock.settimeout(5.0)
                    sock.connect(str(self.sock_path))
                    sock.sendall(f"req_{i:04d}".encode("utf-8"))
                    sock.shutdown(socket.SHUT_WR)
                    ack = sock.recv(1024)
                    self.assertEqual(ack, b"OK\n", f"Request {i} did not receive OK ACK")

            # Allow server handler threads to finish enqueuing
            t_wait = time.time()
            while (svc._tts_queue.qsize() + svc._dropped_phases < total_requests) and (
                time.time() - t_wait < 3.0
            ):
                time.sleep(0.01)

            # Invariants under backpressure
            current_qsize = svc._tts_queue.qsize()
            dropped_count = svc._dropped_phases
            self.assertEqual(current_qsize, max_cap, f"Queue size should equal max cap {max_cap}")
            self.assertEqual(
                dropped_count,
                total_requests - max_cap,
                f"Dropped phases should be exactly {total_requests - max_cap}",
            )
            self.assertEqual(current_qsize + dropped_count, total_requests)

            # Now start worker and let it process all surviving items
            worker_thread = threading.Thread(target=svc._tts_worker, daemon=True)
            worker_thread.start()

            svc._tts_queue.join()

            # Verify synthesizer received surviving items
            synthesized_items = synth.synthesized
            self.assertEqual(
                len(synthesized_items),
                max_cap,
                f"Worker should have synthesized all {max_cap} surviving items",
            )

            # Invariant: the surviving items must be strictly the newest 128 items (req_0222..req_0349)
            expected_first_survivor = f"req_{total_requests - max_cap:04d}"  # req_0222
            expected_last_survivor = f"req_{total_requests - 1:04d}"  # req_0349

            self.assertEqual(synthesized_items[0], expected_first_survivor)
            self.assertEqual(synthesized_items[-1], expected_last_survivor)

            # Invariant: strict ascending FIFO order
            for i in range(len(synthesized_items) - 1):
                self.assertLess(
                    synthesized_items[i],
                    synthesized_items[i + 1],
                    "Surviving items must preserve strict FIFO arrival order",
                )

            # Verify depth reached 0
            self.assertEqual(svc._tts_queue.qsize(), 0)
            self.assertIn(0, depth_records)
        finally:
            svc._stop_socket_server()
            try:
                svc._tts_queue.put_nowait(None)
            except Exception:
                pass

    def test_concurrent_queue_flood_350_requests_multi_threaded(self) -> None:
        """Sends 350 requests concurrently using 12 client worker threads against a 128-item queue cap."""
        sink = BlockingMockSink()
        synth = FastDummySynthesizer()

        cfg = load_config()
        cfg["max_queue_depth"] = 128

        svc = NarratorService(
            socket_path=self.sock_path,
            sink=sink,
            synth=synth,
            config=cfg,
        )
        svc._start_socket_server()

        total_requests = 350
        max_cap = 128

        def _client_send(idx: int) -> int:
            return send_speech_request(f"concurrent_{idx:04d}", socket_path=self.sock_path)

        try:
            with ThreadPoolExecutor(max_workers=12) as pool:
                futures = [pool.submit(_client_send, i) for i in range(total_requests)]
                results = [f.result() for f in futures]

            # All client threads must succeed with returncode 0
            self.assertTrue(all(r == 0 for r in results), "All concurrent requests must return 0")

            # Wait for server handlers to enqueue
            t_wait = time.time()
            while (svc._tts_queue.qsize() + svc._dropped_phases < total_requests) and (
                time.time() - t_wait < 3.0
            ):
                time.sleep(0.01)

            # Invariant verification: thread safety ensured exact sum
            current_qsize = svc._tts_queue.qsize()
            dropped_count = svc._dropped_phases
            self.assertEqual(current_qsize, max_cap)
            self.assertEqual(dropped_count, total_requests - max_cap)
            self.assertEqual(current_qsize + dropped_count, total_requests)

            # Drain queue and check contents
            items = []
            while not svc._tts_queue.empty():
                items.append(svc._tts_queue.get_nowait())
            self.assertEqual(len(items), max_cap)
        finally:
            sink.release()
            svc._stop_socket_server()

    def test_mixed_traffic_flood_under_backpressure(self) -> None:
        """Verifies backpressure drop-oldest shedding works with mixed item types (strings and Phase objects)."""
        cfg = load_config()
        cfg["max_queue_depth"] = 10

        svc = NarratorService(
            socket_path=self.sock_path,
            sink=BlockingMockSink(),
            synth=FastDummySynthesizer(),
            config=cfg,
        )

        # Enqueue 5 string items and 10 Phase items into a max-10 queue
        for i in range(5):
            svc.enqueue_text(f"text_msg_{i}")

        for i in range(10):
            phase = Phase(category=Category.EXPLORE, events=[MagicMock(ts=time.time())])
            svc._maybe_enqueue(phase)

        # Total attempted = 15, max cap = 10 -> exactly 5 items dropped
        self.assertEqual(svc._tts_queue.qsize(), 10)
        self.assertEqual(svc._dropped_phases, 5)

        # Oldest items were text_msg_0..4, so remaining items in queue must all be Phase instances
        items = []
        while not svc._tts_queue.empty():
            items.append(svc._tts_queue.get_nowait())

        self.assertEqual(len(items), 10)
        for item in items:
            self.assertIsInstance(item, Phase, "All remaining items should be the latest Phase objects")


class TestTier5AdversarialSignalAndInterruption(unittest.TestCase):
    """Adversarial stress tests for SIGTERM, SIGINT, process crashes, and ungraceful crash recovery."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(dir="/tmp", prefix="as_t5_sig_")
        self.root = Path(self.temp_dir.name)
        self.sock_path = self.root / "daemon.sock"
        self.pid_path = self.root / "daemon.pid"
        self.events_path = self.root / "events.jsonl"
        self.depth_path = self.root / "depth"
        self.watermark_path = self.root / "watermark"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _spawn_daemon(self, hold_audio: bool = False) -> subprocess.Popen:
        child_code = f"""
import sys, os, time, signal
from pathlib import Path
sys.path.insert(0, "{PLUGIN_PYTHON}")
import narrator_service
from unittest.mock import MagicMock

narrator_service.PID_FILE = Path("{self.pid_path}")
narrator_service.EVENTS_LOG = Path("{self.events_path}")
narrator_service.DEPTH_FILE = Path("{self.depth_path}")
narrator_service.WATERMARK_FILE = Path("{self.watermark_path}")

class SinkMock:
    def __init__(self):
        self._interrupted = False
    def play(self, wav):
        if {hold_audio}:
            while not self._interrupted:
                time.sleep(0.05)
    def interrupt(self):
        self._interrupted = True

cfg = narrator_service.load_config()
cfg["idle_shutdown_seconds"] = 60
svc = narrator_service.NarratorService(
    socket_path=Path("{self.sock_path}"),
    sink=SinkMock(),
    synth=MagicMock(),
    config=cfg,
)
ret = svc.run()
sys.exit(ret)
"""
        proc = subprocess.Popen([str(VENV_PYTHON), "-c", child_code, "/narrator_service.py"])
        # Wait for daemon to become ready (PID file matches proc.pid and socket is bound and accepting)
        for _ in range(50):
            if self.pid_path.exists():
                try:
                    pid_val = int(self.pid_path.read_text().strip())
                    if pid_val == proc.pid and self.sock_path.exists():
                        # Verify socket accepts connections
                        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        try:
                            s.connect(str(self.sock_path))
                            s.close()
                            break
                        except OSError:
                            pass
                        finally:
                            s.close()
                except (ValueError, OSError):
                    pass
            time.sleep(0.05)
        return proc

    def test_sigterm_graceful_shutdown(self) -> None:
        """Sends SIGTERM to running daemon and verifies clean exit code 0 and artifact deletion."""
        proc = self._spawn_daemon()
        self.assertTrue(self.sock_path.exists())
        self.assertTrue(self.pid_path.exists())

        # Send SIGTERM
        proc.send_signal(signal.SIGTERM)
        ret = proc.wait(timeout=5.0)

        self.assertEqual(ret, 0, f"Daemon should exit 0 on SIGTERM, got {ret}")
        self.assertFalse(self.sock_path.exists(), "Socket file must be cleaned up on SIGTERM")
        self.assertFalse(self.pid_path.exists(), "PID file must be cleaned up on SIGTERM")

    def test_sigint_graceful_shutdown(self) -> None:
        """Sends SIGINT (Ctrl+C) to running daemon and verifies clean exit code 0 and artifact deletion."""
        proc = self._spawn_daemon()
        self.assertTrue(self.sock_path.exists())
        self.assertTrue(self.pid_path.exists())

        # Send SIGINT
        proc.send_signal(signal.SIGINT)
        ret = proc.wait(timeout=5.0)

        self.assertEqual(ret, 0, f"Daemon should exit 0 on SIGINT, got {ret}")
        self.assertFalse(self.sock_path.exists(), "Socket file must be cleaned up on SIGINT")
        self.assertFalse(self.pid_path.exists(), "PID file must be cleaned up on SIGINT")

    def test_sigterm_interrupts_active_playback_and_unblocks_worker(self) -> None:
        """Verifies SIGTERM unblocks worker thread blocked in sink.play() via sink.interrupt()."""
        proc = self._spawn_daemon(hold_audio=True)
        try:
            # Enqueue item to make worker enter blocking playback
            res = send_speech_request("Item causing playback", socket_path=self.sock_path)
            self.assertEqual(res, 0)
            time.sleep(0.1)

            # Send SIGTERM - must interrupt playback and terminate within 5s
            t0 = time.time()
            proc.send_signal(signal.SIGTERM)
            ret = proc.wait(timeout=5.0)
            elapsed = time.time() - t0

            self.assertEqual(ret, 0)
            self.assertLess(elapsed, 4.0, f"Shutdown took too long ({elapsed}s), interrupt failed")
            self.assertFalse(self.sock_path.exists())
            self.assertFalse(self.pid_path.exists())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_sigkill_crash_and_subsequent_recovery(self) -> None:
        """Simulates hard process crash (SIGKILL) leaving stale artifacts, and verifies recovery on next boot."""
        # 1. Start daemon 1
        p1 = self._spawn_daemon()
        self.assertEqual(send_speech_request("Pre-crash message", socket_path=self.sock_path), 0)

        # 2. Hard crash via SIGKILL
        p1.kill()
        p1.wait()

        # Both stale files remain on disk
        self.assertTrue(self.sock_path.exists(), "Socket file remains after SIGKILL")
        self.assertTrue(self.pid_path.exists(), "PID file remains after SIGKILL")

        # 3. Client trying to connect to dead socket fails gracefully with 1
        ret_stale = send_speech_request("Message during outage", socket_path=self.sock_path)
        self.assertEqual(ret_stale, 1, "Client should gracefully return 1 when daemon is dead")

        # 4. Boot daemon 2 on the exact same directory and paths
        p2 = self._spawn_daemon()
        try:
            self.assertTrue(self.sock_path.exists())
            self.assertTrue(self.pid_path.exists())
            new_pid = int(self.pid_path.read_text().strip())
            self.assertEqual(new_pid, p2.pid)
            self.assertNotEqual(new_pid, p1.pid)

            # Verify client can communicate with recovered daemon
            ret_recovered = send_speech_request("Message to recovered daemon", socket_path=self.sock_path)
            self.assertEqual(ret_recovered, 0, "Recovered daemon must accept client connections")
        finally:
            p2.terminate()
            p2.wait(timeout=5.0)

    def test_corrupt_or_dead_pid_file_clean_recovery(self) -> None:
        """Verifies _existing_pid() returns None when PID file contains dead PID or corrupt text."""
        # Case A: Dead PID (e.g. 9999999)
        self.pid_path.write_text("9999999\n")
        original_pid_file = narrator_service.PID_FILE
        narrator_service.PID_FILE = self.pid_path
        try:
            self.assertIsNone(_existing_pid(), "Dead PID must return None")

            # Case B: Corrupt text
            self.pid_path.write_text("NOT_A_VALID_INTEGER_PID\n")
            self.assertIsNone(_existing_pid(), "Corrupt PID file content must return None")

            # Case C: Empty file
            self.pid_path.write_text("")
            self.assertIsNone(_existing_pid(), "Empty PID file must return None")
        finally:
            narrator_service.PID_FILE = original_pid_file


class TestTier5AdversarialWorkerAndHandlerRobustness(unittest.TestCase):
    """Adversarial stress tests for _tts_worker and _DaemonRequestHandler edge cases."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(dir="/tmp", prefix="as_t5_robust_")
        self.root = Path(self.temp_dir.name)
        self.sock_path = self.root / "auto-speech-daemon.sock"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_worker_resilience_to_synthesis_exceptions(self) -> None:
        """Verifies worker thread survives unhandled synthesizer exceptions and processes subsequent items."""
        sink = BlockingMockSink()
        # Synthesizer configured to raise RuntimeError on the first 2 items
        synth = FastDummySynthesizer(fail_first_n=2)

        cfg = load_config()
        cfg["max_queue_depth"] = 10
        svc = NarratorService(
            socket_path=self.sock_path,
            sink=sink,
            synth=synth,
            config=cfg,
        )
        svc._profile = VoiceProfile("test", 1.0, 15.0, "cal", 0)
        svc._engine = MagicMock()

        # Enqueue 3 items: items 0 and 1 will crash synthesizer, item 2 will succeed
        svc.enqueue_text("crash_item_1")
        svc.enqueue_text("crash_item_2")
        svc.enqueue_text("success_item_3")

        worker = threading.Thread(target=svc._tts_worker, daemon=True)
        worker.start()

        # Wait for queue to drain
        svc._tts_queue.join()

        # Worker thread must still be alive!
        self.assertTrue(worker.is_alive(), "_tts_worker must not terminate on synthesis errors")

        # Item 3 must have succeeded and reached the audio sink
        self.assertEqual(synth.synthesized, ["success_item_3"])
        self.assertEqual(len(sink.played), 1)

        # Stop worker cleanly
        svc._tts_queue.put(None)
        worker.join(timeout=2.0)

    def test_daemon_request_handler_abrupt_disconnects(self) -> None:
        """Hammers _DaemonSocketServer with 30 abrupt RST disconnects (SO_LINGER 0) without server crash."""
        cfg = load_config()
        svc = NarratorService(
            socket_path=self.sock_path,
            sink=BlockingMockSink(),
            synth=FastDummySynthesizer(),
            config=cfg,
        )
        svc._start_socket_server()

        linger = (
            b"\x01\x00\x00\x00\x00\x00\x00\x00"
            if sys.platform == "darwin"
            else b"\x01\x00\x00\x00"
        )

        try:
            for i in range(30):
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    s.connect(str(self.sock_path))
                    s.sendall(f"partial_chunk_{i}".encode("utf-8"))
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
                except (OSError, ConnectionResetError):
                    pass
                finally:
                    s.close()
                time.sleep(0.002)

            self.assertTrue(
                svc._socket_thread.is_alive(), "Socket server thread must survive abrupt client resets"
            )

            # Normal request immediately following resets must succeed
            res = send_speech_request("Normal request post-resets", socket_path=self.sock_path)
            self.assertEqual(res, 0)
        finally:
            svc._stop_socket_server()

    def test_daemon_request_handler_empty_and_whitespace_only(self) -> None:
        """Verifies empty or whitespace-only client socket payloads do not enqueue to _tts_queue."""
        cfg = load_config()
        svc = NarratorService(
            socket_path=self.sock_path,
            sink=BlockingMockSink(),
            synth=FastDummySynthesizer(),
            config=cfg,
        )
        svc._start_socket_server()

        try:
            # 1. Whitespace via send_speech_request
            self.assertEqual(send_speech_request("   \n\t   ", socket_path=self.sock_path), 0)

            # 2. Raw socket sending whitespace
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(str(self.sock_path))
            sock.sendall(b"  \n   \n\t  ")
            sock.shutdown(socket.SHUT_WR)
            sock.close()

            time.sleep(0.05)
            self.assertEqual(svc._tts_queue.qsize(), 0, "Empty/whitespace payloads must not be enqueued")
        finally:
            svc._stop_socket_server()


def load_tests(loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None) -> unittest.TestSuite:
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestTier5AdversarialSocketLifecycle))
    suite.addTests(loader.loadTestsFromTestCase(TestTier5AdversarialQueueBackpressureFlood))
    suite.addTests(loader.loadTestsFromTestCase(TestTier5AdversarialSignalAndInterruption))
    suite.addTests(loader.loadTestsFromTestCase(TestTier5AdversarialWorkerAndHandlerRobustness))
    return suite


if __name__ == "__main__":
    unittest.main()
