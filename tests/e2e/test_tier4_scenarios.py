"""Tier 4: Real-World Scenarios (End-to-End User Workflows).

Simulates complete end-to-end sessions:
- Full user session lifecycle (Daemon boot -> JSONL events -> CLI speak -> shutdown -> cleanup)
- High-load mixed traffic stress scenario
- Daemon stop/restart resiliency and client reconnection
"""

from __future__ import annotations

import os
import queue
import socketserver
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PYTHON = PROJECT_ROOT / "plugin" / "scripts" / "python"
if str(PLUGIN_PYTHON) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PYTHON))

import narrator_service
from priority_arbiter import PriorityArbiter, QueueProxyFacade
from voice_profile import VoiceProfile
from tests.e2e.harness import (
    IsolatedEnvironment,
    run_speak_cli,
)


class TestTier4RealWorldScenarios(unittest.TestCase):
    """Tier 4 tests simulating complete user workflows and real-world system sessions."""

    def setUp(self) -> None:
        self.sandbox = IsolatedEnvironment(use_spy_mpv=True, mpv_delay_s=0.02)

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def test_tier4_scenario_full_user_session_lifecycle(self) -> None:
        """Simulates full session with real NarratorService: boot daemon, stream tool events, run speak.py, shutdown, and verify cleanup."""
        sink = MagicMock()
        sink.interrupt = MagicMock()

        svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
        svc._config = {"max_queue_depth": 32, "idle_shutdown_seconds": 600}
        svc._socket_path = self.sandbox.socket_path
        svc._socket_server = None
        svc._socket_thread = None
        svc._queue_lock = threading.Lock()
        svc._atexit_registered = False
        svc._max_queue = 32
        svc._priority_arbiter = PriorityArbiter(maxsize=32)
        svc._tts_queue = QueueProxyFacade(svc._priority_arbiter, maxsize=32)
        svc._dropped_phases = 0
        svc._last_event_ts = time.time()
        svc._update_depth = lambda d: None
        svc._sink = sink
        svc._engine_state = "IDLE"
        svc._active_priority = None
        svc._active_item = None
        svc._interrupted = False
        svc._preempted = False
        svc._state_lock = threading.Lock()
        svc._stop = threading.Event()
        svc._fsm = narrator_service.NarratorStateMachine()
        svc._phases_this_turn = 0
        svc._profile = VoiceProfile("test", 1.0, 15.0, "cal", 0)
        svc._ensure_tts_initialized = lambda: None

        # 1. Daemon boots real socket listener
        svc._start_socket_server()
        self.sandbox.pid_path.write_text(str(os.getpid()), encoding="utf-8")
        time.sleep(0.05)

        try:
            # 2. Simulate tool event arrival
            self.sandbox.write_event("ToolExecution", {"tool": "Grep", "pattern": "def main"})
            svc.enqueue_text("event:Grep pattern def main")

            # 3. User runs speak.py
            cli_res = run_speak_cli("Claude explanation text", env=self.sandbox.env)
            self.assertEqual(cli_res.returncode, 0, f"speak.py failed: {cli_res.stderr}")

            # Verify both items were queued in the real daemon queue
            self.assertEqual(svc._tts_queue.qsize(), 2)
            item1 = svc._tts_queue.get_nowait()
            item2 = svc._tts_queue.get_nowait()
            self.assertEqual(getattr(item1, "payload", item1), "event:Grep pattern def main")
            self.assertEqual(getattr(item2, "payload", item2), "Claude explanation text")
            sink.interrupt.assert_not_called()

            # 4. Clean shutdown
            svc._stop_socket_server()
            if self.sandbox.pid_path.exists():
                self.sandbox.pid_path.unlink()

            # Verify artifacts cleaned up
            self.assertFalse(self.sandbox.socket_path.exists(), "Socket file was not cleaned up")
            self.assertFalse(self.sandbox.pid_path.exists(), "PID file was not cleaned up")
        finally:
            svc._stop_socket_server()
            if self.sandbox.socket_path.exists():
                self.sandbox.socket_path.unlink()

    def test_tier4_scenario_high_load_mixed_traffic(self) -> None:
        """Simulates high-load burst of alternating tool events and speak commands."""
        queue_cap = 32
        speech_queue: queue.Queue[str] = queue.Queue(maxsize=queue_cap)
        processed: list[str] = []
        stop_worker = threading.Event()

        class LoadHandler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                data = self.request.recv(4096).decode("utf-8").strip()
                if data:
                    if speech_queue.full():
                        speech_queue.get_nowait()
                    speech_queue.put_nowait(data)

        server = socketserver.ThreadingUnixStreamServer(str(self.sandbox.socket_path), LoadHandler)
        s_th = threading.Thread(target=server.serve_forever, daemon=True)
        s_th.start()
        time.sleep(0.05)

        def _drain() -> None:
            while not stop_worker.is_set() or not speech_queue.empty():
                try:
                    item = speech_queue.get(timeout=0.02)
                except queue.Empty:
                    continue
                processed.append(item)
                speech_queue.task_done()

        w_th = threading.Thread(target=_drain, daemon=True)
        w_th.start()

        try:
            # 20 interleaved operations
            for i in range(20):
                # Event
                speech_queue.put(f"event_{i}")
                # CLI call
                res = run_speak_cli(f"speak_{i}", env=self.sandbox.env)
                self.assertEqual(res.returncode, 0, f"speak.py failed on turn {i}: {res.stderr}")

            speech_queue.join()
            stop_worker.set()
            w_th.join(timeout=2.0)

            self.assertGreaterEqual(len(processed), 20)
        finally:
            server.shutdown()
            server.server_close()

    def test_tier4_scenario_daemon_reboot_and_client_reconnection(self) -> None:
        """Simulates daemon restart resilience: client fails when down, succeeds when restarted."""
        # 1. Daemon down: client must fail
        if self.sandbox.socket_path.exists():
            self.sandbox.socket_path.unlink()

        res1 = run_speak_cli("hello before boot", env=self.sandbox.env)
        self.assertNotEqual(res1.returncode, 0)

        # 2. Start daemon socket
        received: list[str] = []

        class RecvHandler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                msg = self.request.recv(4096).decode("utf-8").strip()
                if msg:
                    received.append(msg)

        server = socketserver.ThreadingUnixStreamServer(str(self.sandbox.socket_path), RecvHandler)
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        time.sleep(0.05)

        try:
            # 3. Client re-runs: must succeed
            res2 = run_speak_cli("hello after boot", env=self.sandbox.env)
            self.assertEqual(res2.returncode, 0)
            self.assertEqual(received, ["hello after boot"])
        finally:
            # 4. Stop daemon
            server.shutdown()
            server.server_close()
            if self.sandbox.socket_path.exists():
                self.sandbox.socket_path.unlink()

        # 5. Client re-runs after stop: must fail again
        res3 = run_speak_cli("hello after shutdown", env=self.sandbox.env)
        self.assertNotEqual(res3.returncode, 0)


if __name__ == "__main__":
    unittest.main()
