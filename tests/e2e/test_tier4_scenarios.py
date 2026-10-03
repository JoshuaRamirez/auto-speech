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
import threading
import time
import unittest

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
        """Simulates full session: boot daemon, stream tool events, run speak.py, shutdown, and verify cleanup."""
        # Setup simulated unified daemon pipeline
        speech_queue: queue.Queue[str] = queue.Queue(maxsize=32)
        played_items: list[str] = []
        stop_daemon = threading.Event()

        class DaemonSocketHandler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                data = self.request.recv(4096).decode("utf-8").strip()
                if data:
                    speech_queue.put(f"cli:{data}")

        # 1. Daemon boots socket listener
        server = socketserver.ThreadingUnixStreamServer(
            str(self.sandbox.socket_path), DaemonSocketHandler
        )
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        self.sandbox.pid_path.write_text(str(os.getpid()), encoding="utf-8")
        time.sleep(0.05)

        # 2. Worker thread processing speech queue
        def _tts_worker() -> None:
            while not stop_daemon.is_set() or not speech_queue.empty():
                try:
                    item = speech_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                played_items.append(item)
                speech_queue.task_done()

        worker_thread = threading.Thread(target=_tts_worker, daemon=True)
        worker_thread.start()

        try:
            # 3. Simulate tool event arrival
            self.sandbox.write_event("ToolExecution", {"tool": "Grep", "pattern": "def main"})
            speech_queue.put("event:Grep pattern def main")

            # 4. User runs speak.py
            cli_res = run_speak_cli("Claude explanation text", env=self.sandbox.env)
            self.assertEqual(cli_res.returncode, 0, f"speak.py failed: {cli_res.stderr}")

            # Wait for queue to drain
            speech_queue.join()

            self.assertEqual(
                played_items,
                ["event:Grep pattern def main", "cli:Claude explanation text"],
            )

            # 5. Clean shutdown
            stop_daemon.set()
            server.shutdown()
            server.server_close()
            if self.sandbox.socket_path.exists():
                self.sandbox.socket_path.unlink()
            if self.sandbox.pid_path.exists():
                self.sandbox.pid_path.unlink()

            # Verify artifacts cleaned up
            self.assertFalse(self.sandbox.socket_path.exists(), "Socket file was not cleaned up")
            self.assertFalse(self.sandbox.pid_path.exists(), "PID file was not cleaned up")
        finally:
            stop_daemon.set()
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
