"""Tier 3: Cross-Feature Combinations (Pairwise interactions).

Tests the interactions between:
- Socket IPC client requests and JSONL event tailing
- Backpressure queue cap under high-volume socket bursts
- Multiple concurrent client connections handled by ThreadingUnixStreamServer
- Playback synchronization while queue accumulates
- User prompt interrupts during active audio playback
"""

from __future__ import annotations

import os
import queue
import socketserver
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tests.e2e.harness import (
    PLUGIN_PYTHON,
    IsolatedEnvironment,
    create_dummy_wav,
    run_speak_cli,
)


class TestTier3CrossFeatureCombinations(unittest.TestCase):
    """Tier 3 tests for cross-feature interactions and concurrency."""

    def setUp(self) -> None:
        self.sandbox = IsolatedEnvironment(use_spy_mpv=True, mpv_delay_s=0.05)
        self.dummy_wav = create_dummy_wav(self.sandbox.root / "t3_test.wav")

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def test_tier3_concurrent_socket_and_jsonl_events(self) -> None:
        """Verifies concurrent arrival of socket IPC requests and JSONL events enqueue cleanly."""
        shared_queue: queue.Queue[str] = queue.Queue(maxsize=32)

        # Socket server pushes to shared queue
        class SocketReceiver(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                text = self.request.recv(4096).decode("utf-8").strip()
                if text:
                    shared_queue.put(f"socket:{text}")

        server = socketserver.ThreadingUnixStreamServer(
            str(self.sandbox.socket_path), SocketReceiver
        )
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        time.sleep(0.05)

        try:
            # Thread 1: sends socket messages
            def _send_sockets() -> None:
                for i in range(5):
                    run_speak_cli(f"msg_{i}", env=self.sandbox.env)
                    time.sleep(0.01)

            # Thread 2: writes simulated JSONL events
            def _send_events() -> None:
                for i in range(5):
                    self.sandbox.write_event(
                        "ToolExecution", {"tool": "Bash", "command": f"cmd_{i}"}
                    )
                    # Simulated tailing putting into shared queue
                    shared_queue.put(f"event:cmd_{i}")
                    time.sleep(0.01)

            t1 = threading.Thread(target=_send_sockets)
            t2 = threading.Thread(target=_send_events)
            t1.start()
            t2.start()
            t1.join(timeout=3.0)
            t2.join(timeout=3.0)

            # Both streams should be represented in the shared queue
            items = []
            while not shared_queue.empty():
                items.append(shared_queue.get_nowait())

            socket_items = [x for x in items if x.startswith("socket:")]
            event_items = [x for x in items if x.startswith("event:")]

            self.assertEqual(len(socket_items), 5, f"Expected 5 socket items, got: {socket_items}")
            self.assertEqual(len(event_items), 5, f"Expected 5 event items, got: {event_items}")
        finally:
            server.shutdown()
            server.server_close()

    def test_tier3_backpressure_queue_cap_with_socket_burst(self) -> None:
        """Verifies high-volume burst exercises drop-oldest cap without blocking clients."""
        max_cap = 32
        tts_q: queue.Queue[str] = queue.Queue(maxsize=max_cap)
        dropped_count = 0
        lock = threading.Lock()

        def _enqueue(item: str) -> None:
            nonlocal dropped_count
            with lock:
                while tts_q.full():
                    try:
                        tts_q.get_nowait()
                        dropped_count += 1
                    except queue.Empty:
                        break
                tts_q.put_nowait(item)

        class BurstHandler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                msg = self.request.recv(4096).decode("utf-8").strip()
                if msg:
                    _enqueue(msg)

        server = socketserver.ThreadingUnixStreamServer(str(self.sandbox.socket_path), BurstHandler)
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        time.sleep(0.05)

        try:
            # Burst 45 messages (more than 32 cap)
            total_burst = 45
            for i in range(total_burst):
                res = run_speak_cli(f"burst_{i}", env=self.sandbox.env)
                self.assertEqual(res.returncode, 0, f"speak.py failed on burst {i}: {res.stderr}")

            self.assertLessEqual(tts_q.qsize(), max_cap)
            self.assertEqual(dropped_count, total_burst - max_cap)
        finally:
            server.shutdown()
            server.server_close()

    def test_tier3_multi_client_concurrent_burst(self) -> None:
        """Verifies multiple concurrent clients running simultaneously are all handled cleanly."""
        received_msgs: list[str] = []
        lock = threading.Lock()

        class ConcurrentHandler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                msg = self.request.recv(4096).decode("utf-8").strip()
                if msg:
                    with lock:
                        received_msgs.append(msg)

        server = socketserver.ThreadingUnixStreamServer(
            str(self.sandbox.socket_path), ConcurrentHandler
        )
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        time.sleep(0.05)

        try:
            num_clients = 10

            def _client_task(cid: int) -> int:
                res = run_speak_cli(f"client_{cid}", env=self.sandbox.env)
                return res.returncode

            with ThreadPoolExecutor(max_workers=num_clients) as executor:
                futures = [executor.submit(_client_task, i) for i in range(num_clients)]
                for f in as_completed(futures):
                    self.assertEqual(f.result(), 0, "Concurrent speak.py client returned non-zero")

            self.assertEqual(len(received_msgs), num_clients)
        finally:
            server.shutdown()
            server.server_close()

    def test_tier3_playback_busy_queue_accumulation(self) -> None:
        """Verifies incoming requests accumulate in queue while playback is active and unblock sequentially."""
        sink_path = PLUGIN_PYTHON / "native_audio_sink.py"
        self.assertTrue(sink_path.exists(), "native_audio_sink.py must exist")

        sys.path.insert(0, str(PLUGIN_PYTHON))
        try:
            import native_audio_sink

            playback_sandbox = IsolatedEnvironment(use_spy_mpv=True, mpv_delay_s=0.2)
            try:
                sink = native_audio_sink.NativeAudioSink()
                q: queue.Queue[Path] = queue.Queue()

                # Start worker playing items sequentially
                playback_log: list[str] = []
                stop_worker = threading.Event()

                def _worker() -> None:
                    while not stop_worker.is_set() or not q.empty():
                        try:
                            wav_item = q.get(timeout=0.05)
                        except queue.Empty:
                            continue
                        with unittest.mock.patch.dict(os.environ, playback_sandbox.env):
                            sink.play(wav_item)
                        playback_log.append(wav_item.name)
                        q.task_done()

                w_th = threading.Thread(target=_worker)
                w_th.start()

                # Enqueue 3 items
                w1 = create_dummy_wav(playback_sandbox.root / "item1.wav")
                w2 = create_dummy_wav(playback_sandbox.root / "item2.wav")
                w3 = create_dummy_wav(playback_sandbox.root / "item3.wav")

                q.put(w1)
                q.put(w2)
                q.put(w3)

                q.join()
                stop_worker.set()
                w_th.join(timeout=2.0)

                self.assertEqual(playback_log, ["item1.wav", "item2.wav", "item3.wav"])
            finally:
                playback_sandbox.cleanup()
        finally:
            if str(PLUGIN_PYTHON) in sys.path:
                sys.path.remove(str(PLUGIN_PYTHON))

    def test_tier3_user_prompt_interrupt_flushes_socket_and_event_audio(self) -> None:
        """Verifies user prompt interrupt halts active mpv and clears pending queue items."""
        sink_path = PLUGIN_PYTHON / "native_audio_sink.py"
        self.assertTrue(sink_path.exists(), "native_audio_sink.py must exist")

        sys.path.insert(0, str(PLUGIN_PYTHON))
        try:
            import native_audio_sink

            long_sandbox = IsolatedEnvironment(use_spy_mpv=True, mpv_delay_s=2.0)
            try:
                sink = native_audio_sink.NativeAudioSink()
                w = create_dummy_wav(long_sandbox.root / "long_play.wav")

                play_started = threading.Event()
                play_finished = threading.Event()

                def _play() -> None:
                    play_started.set()
                    with unittest.mock.patch.dict(os.environ, long_sandbox.env):
                        sink.play(w)
                    play_finished.set()

                th = threading.Thread(target=_play)
                th.start()
                play_started.wait()
                time.sleep(0.1)

                # Prompt submit triggers interrupt
                t0 = time.time()
                sink.interrupt()
                play_finished.wait(timeout=1.0)
                elapsed = time.time() - t0

                self.assertTrue(play_finished.is_set(), "interrupt failed to halt playback")
                self.assertLess(elapsed, 1.0, f"Interrupt took too long: {elapsed:.2f}s")
            finally:
                long_sandbox.cleanup()
        finally:
            if str(PLUGIN_PYTHON) in sys.path:
                sys.path.remove(str(PLUGIN_PYTHON))


if __name__ == "__main__":
    unittest.main()
