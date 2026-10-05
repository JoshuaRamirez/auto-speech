"""Tier 5: Adversarial Coverage Hardening for NativeAudioSink and Thin Client Socket IPC.

Adversarial stress-testing suite targeting:
1. NativeAudioSink:
   - Rapid sequential playback of 55 tiny audio chunks with process tracking and zero orphans.
   - Concurrent multi-threaded playback serialization.
   - Invalid audio file matrix (non-existent, directory, 0-byte, plain text, binary garbage) and immediate recovery.
   - mpv exit codes (1, 2, 127), signal terminations (SIGTERM, SIGKILL, 143, 137), timeout terminations, and MpvNotInstalledError.
   - Idempotent and thread-safe interrupt race conditions.

2. Thin Client speak.py and UNIX Socket IPC:
   - Giant payload transmission (500KB text) across chunked socket buffers.
   - High-concurrency client bursts (100 simultaneous clients) with connection backlog handling.
   - Parallel CLI subprocess burst executions.
   - Abrupt client disconnect matrix (immediate close, RST/SO_LINGER 0, partial send, SHUT_RDWR).
   - Malformed Unicode, surrogate pairs, null bytes, and raw invalid UTF-8 byte streams.
   - Empty vs whitespace-only inputs (including Unicode whitespaces).
   - Stale socket file, regular file, and broken symlink rebinding stress.

3. Integrated Workflows (NarratorService, MCP Speech Dispatch, AutoplayWorker):
   - High-concurrency drop-oldest backpressure under 100-request bursts.
   - MCP speech dispatch resilience against corrupt, empty, and offline conditions.
   - AutoplayWorker environment variable tampering and graceful fallbacks.
"""

from __future__ import annotations

import concurrent.futures
import os
import shutil
import signal
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

# Ensure project root and plugin scripts are in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PYTHON = PROJECT_ROOT / "plugin" / "scripts" / "python"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PLUGIN_PYTHON) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PYTHON))

from tests.e2e.harness import (  # noqa: E402
    IsolatedEnvironment,
    SpyMpv,
    create_dummy_wav,
    run_speak_cli,
)
from autoplay_worker import AutoplayWorker, _num_env  # noqa: E402
from narrator_service import (  # noqa: E402
    NarratorService,
    _DaemonSocketServer,
)
from native_audio_sink import (  # noqa: E402
    MpvNotInstalledError,
    NativeAudioSink,
    PlaybackError,
)
from speak import send_speech_request  # noqa: E402


class TestTier5AdversarialAudioSink(unittest.TestCase):
    """Adversarial stress tests for NativeAudioSink."""

    def setUp(self) -> None:
        self.sandbox = IsolatedEnvironment(use_spy_mpv=True, mpv_delay_s=0.002)
        self.dummy_wav = create_dummy_wav(self.sandbox.root / "valid_test.wav", duration_s=0.05)

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def test_tier5_sink_rapid_sequential_playback_50_chunks(self) -> None:
        """Rapid sequential playback of 55 tiny audio chunks without process or descriptor leaks."""
        sink = NativeAudioSink(mpv_path=self.sandbox.spy_mpv.bin_path)
        chunk_count = 55

        t0 = time.time()
        for i in range(chunk_count):
            sink.play(self.dummy_wav)
            self.assertFalse(sink.is_playing, f"Sink should not be playing after chunk {i}")

        elapsed = time.time() - t0
        invocations = self.sandbox.spy_mpv.get_invocations()
        active_pids = self.sandbox.spy_mpv.get_active_pids()

        self.assertEqual(len(invocations), chunk_count, f"Expected {chunk_count} invocations, got {len(invocations)}")
        self.assertEqual(len(active_pids), 0, "No active or orphan mpv processes should remain")
        self.assertFalse(sink.is_playing)
        self.assertFalse(sink.was_interrupted)
        self.assertLess(elapsed, 15.0, f"55 chunks took too long: {elapsed:.2f}s")

    def test_tier5_sink_concurrent_multi_thread_serialization(self) -> None:
        """Multiple threads concurrently calling play() on the same sink must execute strictly serialized."""
        overlap_spy = SpyMpv(delay_s=0.03)
        sink = NativeAudioSink(mpv_path=overlap_spy.bin_path)

        thread_count = 10
        errors: list[Exception] = []

        def worker_task(idx: int) -> None:
            try:
                wav = create_dummy_wav(self.sandbox.root / f"worker_{idx}.wav", duration_s=0.02)
                sink.play(wav)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker_task, args=(i,)) for i in range(thread_count)]
        t0 = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        elapsed = time.time() - t0

        overlap_spy.cleanup()
        self.assertEqual(errors, [], f"Encountered errors in concurrent playback: {errors}")
        # With 10 threads and 0.03s delay serialized, must take >= 0.25s
        self.assertGreaterEqual(elapsed, 0.25, f"Playback should have been serialized (elapsed={elapsed:.2f}s)")
        self.assertFalse(sink.is_playing)

    def test_tier5_sink_invalid_audio_matrix_and_recovery(self) -> None:
        """Exhaustively verify invalid audio file types raise expected errors and sink recovers cleanly."""
        sink = NativeAudioSink(mpv_path=self.sandbox.spy_mpv.bin_path)

        # 1. Non-existent file
        missing_file = self.sandbox.root / "does_not_exist.wav"
        with self.assertRaises(FileNotFoundError):
            sink.play(missing_file)
        self.assertFalse(sink.is_playing)

        # 2. Directory instead of file
        sub_dir = self.sandbox.root / "a_directory"
        sub_dir.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(FileNotFoundError):
            sink.play(sub_dir)
        self.assertFalse(sink.is_playing)

        # Configure spy mpv to simulate failure for non-audio or corrupt inputs
        fail_spy = SpyMpv(delay_s=0.001, exit_code=2)
        failing_sink = NativeAudioSink(mpv_path=fail_spy.bin_path)

        # 3. 0-byte file
        empty_file = self.sandbox.root / "empty.wav"
        empty_file.touch()
        with self.assertRaises(PlaybackError) as cm_empty:
            failing_sink.play(empty_file)
        self.assertIn("mpv exited with code 2", str(cm_empty.exception))
        self.assertFalse(failing_sink.is_playing)

        # 4. Text file
        text_file = self.sandbox.root / "not_audio.txt"
        text_file.write_text("This is plain text pretending to be audio.", encoding="utf-8")
        with self.assertRaises(PlaybackError) as cm_txt:
            failing_sink.play(text_file)
        self.assertIn("mpv exited with code 2", str(cm_txt.exception))
        self.assertFalse(failing_sink.is_playing)

        # 5. Binary garbage
        garbage_file = self.sandbox.root / "garbage.bin"
        garbage_file.write_bytes(os.urandom(1024))
        with self.assertRaises(PlaybackError) as cm_bin:
            failing_sink.play(garbage_file)
        self.assertIn("mpv exited with code 2", str(cm_bin.exception))
        self.assertFalse(failing_sink.is_playing)

        fail_spy.cleanup()

        # 6. Immediate recovery test: play valid WAV cleanly after all failures
        sink.play(self.dummy_wav)
        self.assertFalse(sink.is_playing)
        self.assertFalse(sink.was_interrupted)

    def test_tier5_sink_mpv_exit_codes_and_interrupt_signals(self) -> None:
        """Verify mpv error exit codes raise PlaybackError, while interruption signals return cleanly."""
        # Non-zero error codes
        for code in (1, 2, 127):
            err_spy = SpyMpv(delay_s=0.001, exit_code=code)
            sink = NativeAudioSink(mpv_path=err_spy.bin_path)
            with self.assertRaises(PlaybackError) as cm:
                sink.play(self.dummy_wav)
            self.assertIn(f"mpv exited with code {code}", str(cm.exception))
            self.assertFalse(sink.is_playing)
            err_spy.cleanup()

        # Interrupted exit codes (143, 137)
        for sig_code in (143, 137):
            sig_spy = SpyMpv(delay_s=0.001, exit_code=sig_code)
            sink = NativeAudioSink(mpv_path=sig_spy.bin_path)
            sink.play(self.dummy_wav)
            self.assertFalse(sink.is_playing)
            sig_spy.cleanup()

        # Signal terminations (-SIGTERM = -15, -SIGKILL = -9)
        tmp_dir = Path(tempfile.mkdtemp(prefix="auto_speech_sig_"))
        try:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                sig_script = tmp_dir / f"mpv_sig_{sig.name}"
                sig_script.write_text(
                    f"#!/usr/bin/env python3\nimport os, signal\nos.kill(os.getpid(), signal.{sig.name})\n",
                    encoding="utf-8",
                )
                sig_script.chmod(0o755)
                sink = NativeAudioSink(mpv_path=sig_script)
                sink.play(self.dummy_wav)
                self.assertFalse(sink.is_playing)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_tier5_sink_playback_timeout_kills_process_and_recovers(self) -> None:
        """Playback exceeding timeout parameter terminates process, raises PlaybackError, and recovers."""
        long_spy = SpyMpv(delay_s=1.0)
        sink = NativeAudioSink(mpv_path=long_spy.bin_path)

        t0 = time.time()
        with self.assertRaises(PlaybackError) as cm:
            sink.play(self.dummy_wav, timeout=0.05)
        elapsed = time.time() - t0

        self.assertIn("timed out after 0.05s", str(cm.exception))
        self.assertLess(elapsed, 0.5, f"Timeout took too long to abort: {elapsed:.2f}s")
        self.assertFalse(sink.is_playing)

        # Sink must immediately recover for next playback using sandbox mpv
        recovery_sink = NativeAudioSink(mpv_path=self.sandbox.spy_mpv.bin_path)
        recovery_sink.play(self.dummy_wav)
        self.assertFalse(recovery_sink.is_playing)

        long_spy.cleanup()

    def test_tier5_sink_missing_mpv_binary_raises_proper_error(self) -> None:
        """Setting invalid mpv binary raises FileNotFoundError or MpvNotInstalledError."""
        bogus_sink = NativeAudioSink(mpv_path="/path/that/does/not/exist/mpv_bogus")
        with self.assertRaises(FileNotFoundError):
            bogus_sink.play(self.dummy_wav)

        # Resolving via PATH failure
        with mock.patch("shutil.which", return_value=None):
            default_sink = NativeAudioSink(mpv_path=None)
            with self.assertRaises(MpvNotInstalledError):
                default_sink.play(self.dummy_wav)

    def test_tier5_sink_interrupt_concurrency_and_idempotence(self) -> None:
        """Interrupting concurrently during active playback terminates immediately and is idempotent."""
        slow_spy = SpyMpv(delay_s=0.5)
        sink = NativeAudioSink(mpv_path=slow_spy.bin_path)

        playback_started = threading.Event()
        t0 = time.time()

        def background_play() -> None:
            playback_started.set()
            sink.play(self.dummy_wav)

        th = threading.Thread(target=background_play)
        th.start()
        playback_started.wait(timeout=1.0)
        time.sleep(0.02)

        # Rapid concurrent interrupt calls from multiple threads
        def interrupt_spam() -> None:
            for _ in range(10):
                sink.interrupt()
                time.sleep(0.001)

        spam_threads = [threading.Thread(target=interrupt_spam) for _ in range(5)]
        for st in spam_threads:
            st.start()
        for st in spam_threads:
            st.join()

        th.join(timeout=2.0)
        elapsed = time.time() - t0

        self.assertLess(elapsed, 1.0, f"Interrupt did not abort in timely manner: {elapsed:.2f}s")
        self.assertTrue(sink.was_interrupted)
        self.assertFalse(sink.is_playing)
        slow_spy.cleanup()

        # Safe to call when idle
        sink.interrupt()
        self.assertFalse(sink.is_playing)


class TestTier5AdversarialSocketIPC(unittest.TestCase):
    """Adversarial stress tests for thin client speak.py and UNIX Domain Socket IPC."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="auto_speech_adv_ipc_")
        self.socket_path = Path(self.temp_dir) / "test_ipc.sock"
        self.received_messages: list[str] = []
        self.received_lock = threading.Lock()

        def _on_text(msg: str) -> None:
            with self.received_lock:
                self.received_messages.append(msg)

        self.server = _DaemonSocketServer(self.socket_path, _on_text)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        time.sleep(0.05)

    def tearDown(self) -> None:
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_tier5_ipc_giant_500kb_payload_transmission(self) -> None:
        """Client sends a 500KB text payload; daemon receives, reconstructs, and matches fully."""
        # 500KB = 512,000 characters
        chunk_pattern = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ \n"
        repetitions = (512000 // len(chunk_pattern)) + 1
        giant_text = (chunk_pattern * repetitions)[:512000]

        rc = send_speech_request(giant_text, socket_path=self.socket_path, timeout=10.0)
        self.assertEqual(rc, 0, "send_speech_request should return 0 for giant payload")

        # Allow daemon thread to finish processing
        deadline = time.time() + 3.0
        while time.time() < deadline:
            with self.received_lock:
                if self.received_messages:
                    break
            time.sleep(0.05)

        with self.received_lock:
            self.assertEqual(len(self.received_messages), 1, "Should receive exactly one message")
            received = self.received_messages[0]

        self.assertEqual(len(received), len(giant_text.strip()), "Received length must match transmitted length")
        self.assertEqual(received, giant_text.strip(), "Received text content must match transmitted text")

    def test_tier5_ipc_100_concurrent_clients_burst(self) -> None:
        """100 simultaneous client threads burst to daemon socket; all connect and enqueue without drop."""
        client_count = 100

        def send_worker(idx: int) -> int:
            text = f"concurrent_client_burst_message_{idx:03d}"
            return send_speech_request(text, socket_path=self.socket_path, timeout=5.0)

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(send_worker, i) for i in range(client_count)]
            results = [f.result() for f in futures]

        self.assertEqual(results, [0] * client_count, "All 100 clients must return exit code 0")

        # Verify daemon received all 100 messages
        deadline = time.time() + 3.0
        while time.time() < deadline:
            with self.received_lock:
                if len(self.received_messages) == client_count:
                    break
            time.sleep(0.05)

        with self.received_lock:
            self.assertEqual(len(self.received_messages), client_count, f"Daemon received {len(self.received_messages)} of {client_count}")
            received_set = set(self.received_messages)
            for i in range(client_count):
                expected = f"concurrent_client_burst_message_{i:03d}"
                self.assertIn(expected, received_set, f"Missing message: {expected}")

    def test_tier5_ipc_parallel_cli_subprocesses_burst(self) -> None:
        """Multiple parallel speak.py subprocesses pipe stdin to daemon socket simultaneously."""
        subproc_count = 20
        env = os.environ.copy()
        env["AUTO_SPEECH_DAEMON_SOCK"] = str(self.socket_path)

        def cli_worker(idx: int) -> int:
            payload = f"cli_parallel_burst_{idx:02d}\n"
            proc = run_speak_cli(payload, env=env, timeout=5.0)
            return proc.returncode

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(cli_worker, i) for i in range(subproc_count)]
            results = [f.result() for f in futures]

        self.assertEqual(results, [0] * subproc_count, "All speak.py subprocesses must exit 0")

        deadline = time.time() + 3.0
        while time.time() < deadline:
            with self.received_lock:
                if len(self.received_messages) >= subproc_count:
                    break
            time.sleep(0.05)

        with self.received_lock:
            for i in range(subproc_count):
                self.assertIn(f"cli_parallel_burst_{i:02d}", self.received_messages)

    def test_tier5_ipc_abrupt_client_disconnect_matrix(self) -> None:
        """Server gracefully handles abrupt client resets, disconnects, and partial transmissions."""
        sock_str = str(self.socket_path)

        # 1. Connect and immediately close without sending data
        s1 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s1.connect(sock_str)
        s1.close()

        # 2. Connect, send partial bytes, set SO_LINGER(1, 0) and close immediately (abrupt RST)
        s2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s2.connect(sock_str)
        s2.sendall(b"partial-disconnect-test-bytes")
        s2.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        s2.close()

        # 3. Connect, send 1 byte, shutdown SHUT_RDWR, close
        s3 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s3.connect(sock_str)
        s3.sendall(b"1")
        try:
            s3.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        s3.close()

        # 4. Connect, send 32KB half-way without shutdown, close
        s4 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s4.connect(sock_str)
        s4.sendall(b"X" * 32768)
        s4.close()

        time.sleep(0.1)

        # 5. Legitimate connection right after abrupt disconnects must succeed normally
        rc = send_speech_request("Legitimate message after abrupt disconnects", socket_path=self.socket_path)
        self.assertEqual(rc, 0)

        time.sleep(0.1)
        with self.received_lock:
            self.assertIn("Legitimate message after abrupt disconnects", self.received_messages)

    def test_tier5_ipc_unicode_and_binary_edge_cases(self) -> None:
        """Handles complex Unicode, surrogate pairs, emojis, null bytes, and invalid UTF-8 bytes."""
        edge_cases = [
            "Null bytes: hello\x00world\x00test",
            "Emoji family: 👨‍👩‍👧‍👦 Rainbow flag: 🏳️‍🌈 Rocket: 🚀",
            "Multilingual: 東京 (Tokyo), Москва (Moscow), القاهرة (Cairo), ירושלים (Jerusalem)",
            "Zalgo combining text: H̸̡̪̯ͨ͊̽̅̾̎Ȩ̛̥͍̭̾͛ͪ̈́̀́͜",
            "Quotes & symbols: \" ' ` $ \t \r \n \\ / < > & %",
        ]

        for text in edge_cases:
            rc = send_speech_request(text, socket_path=self.socket_path)
            self.assertEqual(rc, 0, f"Failed sending Unicode edge case: {text!r}")

        # Raw invalid UTF-8 bytes sent directly to socket
        raw_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        raw_sock.connect(str(self.socket_path))
        raw_sock.sendall(b"prefix \xff\xfe\xfa\x80\x81 suffix\n")
        raw_sock.shutdown(socket.SHUT_WR)
        raw_sock.close()

        time.sleep(0.2)
        with self.received_lock:
            for text in edge_cases:
                self.assertIn(text.strip(), self.received_messages)
            # The invalid UTF-8 should be decoded with replacement character U+FFFD without crashing
            found_invalid = any("prefix" in m and "suffix" in m and "\ufffd" in m for m in self.received_messages)
            self.assertTrue(found_invalid, "Invalid UTF-8 should be decoded with replacement chars")

    def test_tier5_ipc_empty_and_whitespace_matrix(self) -> None:
        """Empty and all forms of whitespace inputs are ignored and do not enqueue to server."""
        whitespaces = [
            "",
            "   ",
            "\t\t\t",
            "\n\r\n  \t",
            "\u00a0\u2000\u2002\u2003\u3000\u2028\u2029",  # Non-breaking space, em/en quad/space, ideographic, separators
        ]

        for ws in whitespaces:
            rc = send_speech_request(ws, socket_path=self.socket_path)
            self.assertEqual(rc, 0, f"send_speech_request must return 0 for whitespace: {ws!r}")

        # Send raw whitespace directly over socket
        raw_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        raw_sock.connect(str(self.socket_path))
        raw_sock.sendall(b"   \r\n\t   \n")
        raw_sock.shutdown(socket.SHUT_WR)
        raw_sock.close()

        time.sleep(0.1)
        with self.received_lock:
            self.assertEqual(len(self.received_messages), 0, "No whitespace should ever be enqueued")

    def test_tier5_ipc_stale_socket_file_rebinding_stress(self) -> None:
        """_DaemonSocketServer unlinks stale socket files, regular files, and dangling symlinks upon binding."""
        test_sock = Path(self.temp_dir) / "rebind_test.sock"

        # 1. Stale regular file
        test_sock.write_text("not a socket", encoding="utf-8")
        s1 = _DaemonSocketServer(test_sock, lambda text: None)
        self.assertTrue(test_sock.is_socket())
        s1.server_close()
        test_sock.unlink()

        # 2. Dangling symlink
        target = Path(self.temp_dir) / "nonexistent_target"
        test_sock.symlink_to(target)
        self.assertTrue(test_sock.is_symlink())
        s2 = _DaemonSocketServer(test_sock, lambda text: None)
        self.assertTrue(test_sock.is_socket())
        s2.server_close()
        test_sock.unlink()

        # 3. Re-binding over already unlinked socket
        s3 = _DaemonSocketServer(test_sock, lambda text: None)
        self.assertTrue(test_sock.is_socket())
        s3.server_close()
        test_sock.unlink()


class TestTier5AdversarialIntegratedWorkflow(unittest.TestCase):
    """Adversarial stress tests for NarratorService backpressure, SayWorker, and AutoplayWorker."""

    def setUp(self) -> None:
        self.sandbox = IsolatedEnvironment(use_spy_mpv=True, mpv_delay_s=0.005)

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def test_tier5_narrator_backpressure_100_burst_drop_oldest(self) -> None:
        """NarratorService bounded queue sheds oldest items under 100 concurrent socket bursts without deadlock."""
        config = {
            "max_queue_depth": 16,
            "idle_shutdown_seconds": 60.0,
            "voice": "af_heart",
            "speed": 1.0,
        }
        service = NarratorService(
            config=config,
            socket_path=self.sandbox.socket_path,
        )

        service._start_socket_server()
        time.sleep(0.05)

        try:
            client_count = 100

            def burst_client(idx: int) -> int:
                return send_speech_request(f"burst_item_{idx:03d}", socket_path=self.sandbox.socket_path)

            with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
                results = list(executor.map(burst_client, range(client_count)))

            self.assertEqual(results, [0] * client_count)

            # Wait for all enqueueing to settle
            time.sleep(0.3)

            # Queue size must be capped at max_queue_depth (16)
            qsize = service._tts_queue.qsize()
            self.assertLessEqual(qsize, 16)
            self.assertGreater(service._dropped_phases, 0, "Drop-oldest backpressure should have shed phases")
            # Total processed items = queued + dropped = 100
            self.assertEqual(qsize + service._dropped_phases, client_count)
        finally:
            service._stop_socket_server()

    def test_tier5_mcp_speech_dispatch_adversarial_matrix(self) -> None:
        """Modernized speech dispatch: mcp_server.spawn_say_worker and speak.send_speech_request
        handle corrupt, empty, whitespace, and offline conditions safely without disk artifacts."""
        import mcp_server
        from speak import send_speech_request

        # 1. Empty and whitespace speech requests short-circuit cleanly
        self.assertEqual(send_speech_request(""), 0)
        self.assertEqual(send_speech_request("   \n\t  "), 0)

        # 2. Missing socket file returns 1 without raising uncaught exception
        missing_sock = self.sandbox.root / "missing.sock"
        self.assertEqual(send_speech_request("test payload", socket_path=missing_sock), 1)

        # 3. mcp_server.spawn_say_worker handles strings without crashing
        with mock.patch("subprocess.Popen") as mock_popen:
            captured = []
            mock_proc = mock.Mock()
            mock_proc.stdin = mock.Mock()
            mock_proc.stdin.write.side_effect = captured.append
            mock_popen.return_value = mock_proc

            mcp_server.spawn_say_worker("Test \ud83d\ude00 valid and unicode")
            self.assertTrue(mock_popen.called)

        # 4. mcp_server.spawn_say_worker handles Popen failures gracefully
        with mock.patch("subprocess.Popen", side_effect=OSError("Process spawn failed")):
            # Must not raise
            mcp_server.spawn_say_worker("Failed spawn attempt")

    def test_tier5_autoplay_worker_env_tampering(self) -> None:
        """AutoplayWorker handles tampered non-numeric environment variables gracefully."""
        # Test _num_env helper
        with mock.patch.dict(os.environ, {"AUTO_SPEECH_AUTOPLAY_COALESCE": "invalid_string"}):
            val = _num_env("AUTO_SPEECH_AUTOPLAY_COALESCE", float)
            self.assertIsNone(val, "_num_env should return None on invalid cast without crashing")

        with mock.patch.dict(os.environ, {"AUTO_SPEECH_AUTOPLAY_COALESCE": "0.25"}):
            val = _num_env("AUTO_SPEECH_AUTOPLAY_COALESCE", float)
            self.assertEqual(val, 0.25)

        # Worker initialization with fallback
        worker_default = AutoplayWorker(0.0, "/nonexistent/transcript.jsonl", "fake_session")
        self.assertEqual(worker_default._cfg["coalesce_seconds"], 1.0)

        # Worker initialization with custom config
        worker_custom = AutoplayWorker(
            0.0,
            "/nonexistent/transcript.jsonl",
            "fake_session",
            config={"coalesce_seconds": 0.05, "narration_wait_max": 10, "queue_wait_max": 100, "min_len": 5},
        )
        self.assertEqual(worker_custom._cfg["coalesce_seconds"], 0.05)


if __name__ == "__main__":
    unittest.main()
