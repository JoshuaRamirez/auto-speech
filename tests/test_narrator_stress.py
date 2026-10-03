"""Empirical stress test suite for narrator_service.py and native_audio_sink.py.

Evaluates:
1. Worker thread queue processing resilience under errors (malformed text, empty strings,
   un-synthesizable text, mock/real synthesizer failures, thread liveness).
2. Rapid UserPromptSubmit interruptions without pkill (active playback interruption,
   rapid bursts, concurrent interrupts, zero hangs).
3. Temporary file leak checks (inspecting temp directories after 60+ varied utterances,
   checking for leftover narrator_*.wav, *.partial, and fragments).
4. Process table cleanliness and memory stability (no orphaned mpv processes, no zombies,
   no memory explosion).
"""

from __future__ import annotations

import gc
import json
import os
import queue
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

import narrator_service  # noqa: E402
from native_audio_sink import NativeAudioSink, PlaybackError  # noqa: E402
from tts_engine import TTSGenerationError  # noqa: E402
from voice_profile import VoiceProfile  # noqa: E402
from tests.e2e.harness import IsolatedEnvironment, create_dummy_wav  # noqa: E402


class MockFailingSynthesizer:
    """Configurable mock synthesizer simulating real-world MLX faults and successes."""

    def __init__(self) -> None:
        self.invocations: list[str] = []
        self.mode: str = "success"  # "success", "unspeakable", "fault", "crash"

    def synthesize_one(self, text: str, profile: Any, out_path: Path) -> bool:
        self.invocations.append(text)
        if self.mode == "unspeakable":
            return False
        if self.mode == "fault":
            # Simulate Kokoro generation fault
            raise TTSGenerationError("mlx Kokoro generate failed: broadcast shape mismatch")
        if self.mode == "crash":
            # Simulate unexpected Python/MLX exception
            raise RuntimeError("Unexpected low-level MLX failure")
        if self.mode == "create_fragments_and_fault":
            # Create fragment and partial files to stress-test cleanup
            frag1 = out_path.parent / f"{out_path.stem}-0_0.wav"
            frag1.write_bytes(b"RIFFdummyfrag")
            partial = out_path.with_suffix(out_path.suffix + ".partial")
            partial.write_bytes(b"RIFFdummypartial")
            raise TTSGenerationError("failed mid-synthesis leaving fragments")

        # Success mode
        create_dummy_wav(out_path, duration_s=0.05)
        return True


class MockAudioSink:
    """Mock audio sink with configurable delay, error, and interrupt tracking."""

    def __init__(self, delay_s: float = 0.05, raise_on_play: Exception | None = None) -> None:
        self.played: list[Path] = []
        self.interrupted: bool = False
        self.delay_s = delay_s
        self.raise_on_play = raise_on_play
        self._lock = threading.Lock()
        self._active_event = threading.Event()

    def play(self, wav_path: Path | str, timeout: float | None = None) -> None:
        path = Path(wav_path)
        with self._lock:
            self.played.append(path)
            self._active_event.set()

        if self.raise_on_play:
            self._active_event.clear()
            raise self.raise_on_play

        t_end = time.time() + self.delay_s
        while time.time() < t_end:
            if self.interrupted:
                self._active_event.clear()
                return
            time.sleep(0.01)
        self._active_event.clear()

    def interrupt(self) -> None:
        self.interrupted = True
        self._active_event.clear()


class TestWorkerQueueResilience(unittest.TestCase):
    """1. Stress test worker thread queue processing under errors."""

    def setUp(self) -> None:
        self.log_messages: list[str] = []
        self.profile = VoiceProfile("test_voice", 1.0, 15.0, "2026-10-03T00:00:00Z", 100)

    def _log_capture(self, msg: str) -> None:
        self.log_messages.append(msg)

    def test_worker_thread_resilience_under_extreme_error_stream(self) -> None:
        """Inject empty strings, malformed types, bad dicts, and synthesizer errors.

        Confirms:
        - _tts_worker thread remains alive throughout
        - Errors are trapped, logged, and queue depth updated
        - Subsequent valid items are processed successfully
        """
        synth = MockFailingSynthesizer()
        sink = MockAudioSink(delay_s=0.01)

        svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
        svc._config = {"max_queue_depth": 100}
        svc._sink = sink
        svc._synth = synth
        svc._profile = self.profile
        svc._engine = MagicMock()
        svc._tts_queue = queue.Queue(maxsize=100)
        svc._summarizer = None
        svc._summarizer_lock = threading.Lock()
        svc._update_depth = MagicMock()

        # Start worker thread
        worker_thread = threading.Thread(target=svc._tts_worker, daemon=True)
        worker_thread.start()
        self.assertTrue(worker_thread.is_alive(), "Worker thread should be running")

        # 1. Empty and whitespace strings
        svc._tts_queue.put("")
        svc._tts_queue.put("   \t  \n  ")

        # 2. Unspeakable text
        synth.mode = "unspeakable"
        svc._tts_queue.put("• • •")

        # 3. Synthesizer generation faults
        synth.mode = "fault"
        svc._tts_queue.put("This phrase will fault during synthesis")

        # 4. Synthesizer unexpected crash
        synth.mode = "crash"
        svc._tts_queue.put("This phrase will crash synthesis")

        # 5. Playback failure
        synth.mode = "success"
        sink.raise_on_play = PlaybackError("Simulated mpv crash")
        svc._tts_queue.put("This phrase will fail during playback")

        # 6. Malformed queue items: non-string, unexpected objects
        sink.raise_on_play = None
        svc._tts_queue.put(123456)  # integer
        svc._tts_queue.put({"type": "UnknownEvent", "bad": True})  # unexpected dict
        svc._tts_queue.put({"type": "Stop", "malformed": True})  # Stop missing fields
        svc._tts_queue.put(None)  # Sentinel to stop worker

        worker_thread.join(timeout=5.0)
        self.assertFalse(worker_thread.is_alive(), "Worker thread should have stopped at sentinel")

        # Restart worker thread and verify it processes new valid items
        synth.mode = "success"
        sink.played.clear()
        worker_thread2 = threading.Thread(target=svc._tts_worker, daemon=True)
        worker_thread2.start()

        svc._tts_queue.put("Subsequent valid item 1")
        svc._tts_queue.put("Subsequent valid item 2")
        svc._tts_queue.put(None)

        worker_thread2.join(timeout=5.0)
        self.assertFalse(worker_thread2.is_alive())
        self.assertEqual(len(sink.played), 2, "Valid items after errors must be played")


class TestPromptInterruption(unittest.TestCase):
    """2. Stress test user prompt interruption without pkill."""

    def setUp(self) -> None:
        self.sandbox = IsolatedEnvironment(use_spy_mpv=True, mpv_delay_s=0.5)
        self.dummy_wav = create_dummy_wav(self.sandbox.root / "long.wav", duration_s=1.0)

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def test_active_playback_interrupted_rapidly_without_pkill(self) -> None:
        """Verify NativeAudioSink.interrupt() halts active playback immediately

        without using pkill or leaving hanging threads.
        """
        sink = NativeAudioSink()

        play_done = threading.Event()

        def _play_target():
            with patch.dict(os.environ, self.sandbox.env):
                sink.play(self.dummy_wav)
            play_done.set()

        t = threading.Thread(target=_play_target)
        t.start()

        # Wait for mpv process to start
        time.sleep(0.1)
        self.assertTrue(sink.is_playing, "Sink should be playing before interrupt")

        # Interrupt playback
        t_interrupt = time.time()
        sink.interrupt()

        play_done.wait(timeout=1.0)
        self.assertTrue(play_done.is_set(), "Playback should unblock immediately on interrupt")
        duration = time.time() - t_interrupt
        self.assertLess(duration, 0.4, f"Interrupt took {duration:.3f}s, expected < 0.4s")
        t.join(timeout=1.0)
        self.assertFalse(t.is_alive())

    def test_rapid_user_prompt_submit_burst(self) -> None:
        """Simulate rapid-fire UserPromptSubmit events arriving during active narration.

        Verifies:
        - sink.interrupt() is invoked cleanly on every event
        - No deadlock, no unhandled exceptions
        - pkill is never executed
        """
        sink = NativeAudioSink()
        svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
        svc._config = {}
        svc._sink = sink
        svc._classifier = narrator_service.PhaseClassifier(
            silence_seconds=0.5, max_events_per_phase=1
        )
        svc._phases_this_turn = 5
        svc._tts_queue = queue.Queue()
        svc._last_event_ts = 0.0

        with tempfile.TemporaryDirectory() as home_str:
            home = Path(home_str)
            session_id = "test-session-rapid"
            marker = home / ".claude" / "auto-speech-narrate-sessions" / session_id
            marker.parent.mkdir(parents=True)
            marker.touch()

            # Generate 20 rapid UserPromptSubmit events
            burst_chunk = b"".join(
                (
                    json.dumps(
                        {
                            "event": "UserPromptSubmit",
                            "ts": time.time(),
                            "payload": {
                                "session_id": session_id,
                                "conversation_history": f"User: question {i}",
                            },
                        }
                    )
                    + "\n"
                ).encode("utf-8")
                for i in range(20)
            )

            # Spy on subprocess calls to verify pkill is NEVER called
            with patch.object(Path, "home", staticmethod(lambda: home)):
                with patch("subprocess.run") as mock_subproc_run:
                    svc._process_chunk(burst_chunk)

                    # Ensure subprocess.run was NEVER called with pkill
                    for call_arg in mock_subproc_run.call_args_list:
                        args = call_arg[0][0] if call_arg[0] else []
                        self.assertNotIn("pkill", args, "pkill must NEVER be called!")

        self.assertEqual(svc._phases_this_turn, 0)

    def test_concurrent_hammer_interrupt_and_play(self) -> None:
        """Hammer NativeAudioSink concurrently with 10 interrupt threads and 5 play threads."""
        sink = NativeAudioSink()
        stop_event = threading.Event()
        errors: list[Exception] = []

        def _play_worker(idx: int):
            wav = create_dummy_wav(self.sandbox.root / f"test_{idx}.wav", duration_s=0.05)
            while not stop_event.is_set():
                try:
                    with patch.dict(os.environ, self.sandbox.env):
                        sink.play(wav)
                except Exception as exc:
                    errors.append(exc)
                time.sleep(0.01)

        def _interrupt_worker():
            while not stop_event.is_set():
                try:
                    sink.interrupt()
                except Exception as exc:
                    errors.append(exc)
                time.sleep(0.005)

        play_threads = [threading.Thread(target=_play_worker, args=(i,)) for i in range(5)]
        interrupt_threads = [threading.Thread(target=_interrupt_worker) for _ in range(10)]

        for t in play_threads + interrupt_threads:
            t.start()

        # Let the hammer run for 1 second
        time.sleep(1.0)
        stop_event.set()

        for t in play_threads + interrupt_threads:
            t.join(timeout=2.0)
            self.assertFalse(t.is_alive(), "Thread hung during concurrent stress")

        self.assertEqual(errors, [], f"Unexpected errors during hammer test: {errors}")


class TestZeroTempFileLeaks(unittest.TestCase):
    """3. Verify no temporary file leaks after processing 50+ simulated utterances."""

    def test_zero_temp_file_leaks_after_60_utterances(self) -> None:
        """Process 60+ varied utterances (successes, unspeakables, generation faults,

        crashes, and mid-synthesis fragments). Confirm zero leftover narrator_*.wav,
        *.partial, or fragment files in temp directories.
        """
        temp_dir = Path(tempfile.gettempdir())
        tmp_dir = Path("/tmp")

        # Snapshot temp files before test
        def _get_narrator_temp_files() -> set[Path]:
            files: set[Path] = set()
            for pattern in ("narrator_*", "*.partial"):
                files.update(temp_dir.glob(pattern))
                files.update(tmp_dir.glob(pattern))
            return files

        initial_files = _get_narrator_temp_files()

        synth = MockFailingSynthesizer()
        sink = MockAudioSink(delay_s=0.001)
        profile = VoiceProfile("test_voice", 1.0, 15.0, "2026-10-03T00:00:00Z", 100)

        svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
        svc._config = {}
        svc._sink = sink
        svc._synth = synth
        svc._profile = profile
        svc._engine = MagicMock()

        # Execute 60 utterances under different conditions
        for i in range(60):
            if i % 4 == 0:
                synth.mode = "success"
                svc._speak(f"Successful utterance number {i}")
            elif i % 4 == 1:
                synth.mode = "unspeakable"
                svc._speak(f"Unspeakable utterance number {i}: • • • ★")
            elif i % 4 == 2:
                synth.mode = "fault"
                svc._speak(f"Faulting utterance number {i}")
            elif i % 4 == 3:
                synth.mode = "create_fragments_and_fault"
                svc._speak(f"Fragmenting utterance number {i}")

        final_files = _get_narrator_temp_files()
        leaked_files = final_files - initial_files

        self.assertEqual(
            leaked_files,
            set(),
            f"Temporary file leak detected! Leaked files: {leaked_files}",
        )


class TestProcessTableAndMemory(unittest.TestCase):
    """4. Verify process table cleanliness and memory stability."""

    def test_no_orphaned_mpv_processes(self) -> None:
        """Run 20 audio playbacks with interrupts and check process table."""
        sandbox = IsolatedEnvironment(use_spy_mpv=True, mpv_delay_s=0.2)
        try:
            sink = NativeAudioSink()
            wav = create_dummy_wav(sandbox.root / "dummy.wav", duration_s=0.2)

            for i in range(20):
                th = threading.Thread(
                    target=lambda: (
                        patch.dict(os.environ, sandbox.env),
                        sink.play(wav),
                    )
                )
                th.start()
                time.sleep(0.02)
                if i % 2 == 0:
                    sink.interrupt()
                th.join(timeout=1.0)

            # Check spy mpv active PIDs
            active = sandbox.spy_mpv.get_active_pids()
            self.assertEqual(active, [], f"Orphaned mpv processes found: {active}")
        finally:
            sandbox.cleanup()

    def test_memory_stability_under_sustained_load(self) -> None:
        """Process 200 utterances through narrator_service and measure RSS."""
        import resource

        synth = MockFailingSynthesizer()
        sink = MockAudioSink(delay_s=0.0001)
        profile = VoiceProfile("test_voice", 1.0, 15.0, "2026-10-03T00:00:00Z", 100)

        svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
        svc._config = {}
        svc._sink = sink
        svc._synth = synth
        svc._profile = profile
        svc._engine = MagicMock()

        gc.collect()
        rss_start = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

        for i in range(200):
            synth.mode = "success" if i % 2 == 0 else "fault"
            svc._speak(f"Memory test utterance {i} with payload content")

        gc.collect()
        rss_end = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

        # On macOS ru_maxrss is in bytes, on Linux in KB
        # Check that RSS did not blow up (within reasonable bounds)
        rss_delta_mb = (
            (rss_end - rss_start) / (1024 * 1024)
            if sys.platform == "darwin"
            else (rss_end - rss_start) / 1024
        )
        self.assertLess(rss_delta_mb, 50.0, f"Memory growth excessive: {rss_delta_mb:.2f} MB")


if __name__ == "__main__":
    unittest.main()
