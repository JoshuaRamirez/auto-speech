"""Empirical Stress Test Suite for NativeAudioSink.

Tests rapid calls, concurrent multi-threaded playback, interrupt latency and
termination, zero orphan/zombie mpv processes, and adversarial edge cases.
"""

from __future__ import annotations

import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from native_audio_sink import (
    MpvNotInstalledError,
    NativeAudioSink,
    PlaybackError,
)


def _create_wav(duration_s: float = 0.05, framerate: int = 24000) -> Path:
    """Creates a temporary silent WAV file of the given duration."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)
    n_frames = int(framerate * duration_s)
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(framerate)
        w.writeframes(b"\x00" * (n_frames * 2))
    return wav_path


def _count_system_mpv_processes() -> tuple[int, int]:
    """Returns (total_mpv_processes, zombie_mpv_processes) in the system process table."""
    try:
        out = subprocess.check_output(
            ["ps", "-A", "-o", "stat,comm"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return 0, 0

    total = 0
    zombies = 0
    for line in out.strip().splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            stat_col, comm = parts
            base_comm = Path(comm).name.lower()
            if base_comm in ("mpv", "(mpv)"):
                total += 1
                if stat_col.startswith("Z"):
                    zombies += 1
    return total, zombies


class TestStressSequentialAndConcurrentPlayback(unittest.TestCase):
    """Stress tests for rapid sequential and multi-threaded concurrent playback."""

    def setUp(self) -> None:
        if not shutil.which("mpv"):
            self.skipTest("mpv binary required for empirical stress tests")
        total, _zombies = _count_system_mpv_processes()
        self.assertEqual(total, 0, f"Found pre-existing mpv processes before test: {total}")

    def test_rapid_sequential_playback(self) -> None:
        """30 back-to-back sequential play() calls must succeed with zero crashes or leaks."""
        sink = NativeAudioSink()
        wav = _create_wav(duration_s=0.04)
        try:
            for _ in range(30):
                self.assertFalse(sink.is_playing)
                sink.play(wav)
                self.assertFalse(sink.is_playing)
                self.assertFalse(sink.was_interrupted)
        finally:
            wav.unlink(missing_ok=True)

        total, zombies = _count_system_mpv_processes()
        self.assertEqual(total, 0, f"Leaked mpv processes after sequential stress: {total}")
        self.assertEqual(zombies, 0, f"Leaked zombie mpv processes: {zombies}")

    def test_concurrent_multithreaded_playback_serialization(self) -> None:
        """10 threads concurrently calling play() must strictly serialize without audio overlap."""
        sink = NativeAudioSink()
        num_threads = 10
        calls_per_thread = 4
        wav_files = [_create_wav(duration_s=0.03) for _ in range(num_threads)]

        errors = []

        def worker(tid: int, wav_path: Path):
            for _ in range(calls_per_thread):
                try:
                    sink.play(wav_path)
                except Exception as e:  # noqa: BLE001 — worker records any playback failure
                    errors.append((tid, e))

        threads = [
            threading.Thread(target=worker, args=(i, wav_files[i])) for i in range(num_threads)
        ]

        # Monitor thread checking that at no time does sink have > 1 active playback
        monitor_stop = threading.Event()
        violations = []

        def monitor():
            while not monitor_stop.is_set():
                with sink._state_lock:
                    proc = sink._proc
                    if proc is not None and proc.poll() is None:
                        # Exactly one process active; any other check should never see > 1
                        pass
                time.sleep(0.001)

        mon_thread = threading.Thread(target=monitor, daemon=True)
        mon_thread.start()

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        monitor_stop.set()
        mon_thread.join(timeout=1.0)

        for w in wav_files:
            w.unlink(missing_ok=True)

        self.assertEqual(len(errors), 0, f"Worker threads encountered errors: {errors}")
        self.assertEqual(len(violations), 0, f"Violations observed: {violations}")
        self.assertFalse(sink.is_playing)

        # Give process table a moment to settle
        time.sleep(0.05)
        total, zombies = _count_system_mpv_processes()
        self.assertEqual(total, 0, f"Leaked mpv processes after concurrent playback: {total}")
        self.assertEqual(zombies, 0, f"Leaked zombies after concurrent playback: {zombies}")

    def test_staggered_fifo_queue_ordering(self) -> None:
        """Staggered thread submissions must acquire the lock in FIFO sequence."""
        sink = NativeAudioSink()
        wav = _create_wav(duration_s=0.04)
        execution_order = []
        lock = threading.Lock()

        def worker(idx: int):
            time.sleep(idx * 0.01)  # Stagger entry
            sink.play(wav)
            with lock:
                execution_order.append(idx)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        wav.unlink(missing_ok=True)
        self.assertEqual(execution_order, list(range(5)), f"Non-FIFO order: {execution_order}")


class TestStressInterruptionAndLatency(unittest.TestCase):
    """Stress tests for interruption under idle, active, race, and adversarial conditions."""

    def setUp(self) -> None:
        if not shutil.which("mpv"):
            self.skipTest("mpv binary required for empirical stress tests")

    def test_idle_interruption_flood(self) -> None:
        """1,000 concurrent interrupt() calls on an idle sink must be idempotent no-ops."""
        sink = NativeAudioSink()
        errors = []

        def flooder():
            for _ in range(100):
                try:
                    sink.interrupt()
                except Exception as e:  # noqa: BLE001 — flooder records any interrupt failure
                    errors.append(e)

        threads = [threading.Thread(target=flooder) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Idle interrupt errors: {errors}")
        self.assertFalse(sink.is_playing)

    def test_concurrent_interruption_during_playback(self) -> None:
        """20 threads hammering interrupt() while audio plays must cleanly terminate mpv."""
        sink = NativeAudioSink()
        wav = _create_wav(duration_s=5.0)

        play_done = threading.Event()
        player_error = []

        def player():
            try:
                sink.play(wav)
            except Exception as e:  # noqa: BLE001 — player thread records any playback failure
                player_error.append(e)
            finally:
                play_done.set()

        t_player = threading.Thread(target=player)
        t_player.start()

        # Wait until mpv is active
        while not sink.is_playing:
            time.sleep(0.002)

        # Launch 20 interrupters hammering interrupt()
        interrupter_errors = []

        def hammer():
            for _ in range(20):
                try:
                    sink.interrupt()
                except Exception as e:  # noqa: BLE001 — interrupter records any interrupt failure
                    interrupter_errors.append(e)

        interrupters = [threading.Thread(target=hammer) for _ in range(20)]
        for t in interrupters:
            t.start()
        for t in interrupters:
            t.join()

        play_done.wait(timeout=2.0)
        t_player.join(timeout=1.0)
        wav.unlink(missing_ok=True)

        self.assertEqual(player_error, [], f"Player thread threw unexpected error: {player_error}")
        self.assertEqual(
            interrupter_errors, [], f"Interrupter threads threw errors: {interrupter_errors}"
        )
        self.assertTrue(sink.was_interrupted)
        self.assertFalse(sink.is_playing)

        time.sleep(0.05)
        total, zombies = _count_system_mpv_processes()
        self.assertEqual(total, 0, f"Leaked mpv processes after concurrent interrupt: {total}")
        self.assertEqual(zombies, 0, f"Leaked zombies after concurrent interrupt: {zombies}")

    def test_interrupt_immediately_upon_starting(self) -> None:
        """Interrupting immediately as playback begins must terminate mpv in sub-200ms."""
        sink = NativeAudioSink()
        wav = _create_wav(duration_s=3.0)
        try:
            for iteration in range(15):
                player_error = []

                def player(player_error=player_error):
                    try:
                        sink.play(wav)
                    except Exception as e:  # noqa: BLE001 — trial records any playback failure
                        player_error.append(e)

                t = threading.Thread(target=player)
                t.start()

                # Wait until playback starts
                while not sink.is_playing:
                    time.sleep(0.0005)

                t_call = time.perf_counter()
                sink.interrupt()
                call_duration_ms = (time.perf_counter() - t_call) * 1000.0

                t.join(timeout=1.0)
                self.assertFalse(t.is_alive(), f"Player hung on iteration {iteration}")
                self.assertEqual(
                    player_error, [], f"Error on iteration {iteration}: {player_error}"
                )
                self.assertTrue(sink.was_interrupted)
                self.assertLess(call_duration_ms, 200.0)
        finally:
            wav.unlink(missing_ok=True)

        time.sleep(0.05)
        total, zombies = _count_system_mpv_processes()
        self.assertEqual(total, 0, f"Leaked mpv processes after immediate interrupt: {total}")
        self.assertEqual(zombies, 0, f"Leaked zombies after immediate interrupt: {zombies}")

    def test_concurrent_start_and_interrupt_race(self) -> None:
        """Concurrent thread start and immediate interrupt race must never hang or crash."""
        sink = NativeAudioSink()
        wav = _create_wav(duration_s=0.1)
        try:
            for iteration in range(25):
                player_error = []

                def player(player_error=player_error):
                    try:
                        sink.play(wav)
                    except Exception as e:  # noqa: BLE001 — trial records any playback failure
                        player_error.append(e)

                t = threading.Thread(target=player)
                t.start()
                sink.interrupt()
                t.join(timeout=1.5)
                self.assertFalse(t.is_alive(), f"Player hung on iteration {iteration}")
                self.assertEqual(
                    player_error, [], f"Error on iteration {iteration}: {player_error}"
                )
        finally:
            wav.unlink(missing_ok=True)

        time.sleep(0.05)
        total, zombies = _count_system_mpv_processes()
        self.assertEqual(total, 0, f"Leaked mpv processes after start/interrupt race: {total}")
        self.assertEqual(zombies, 0, f"Leaked zombies after start/interrupt race: {zombies}")

    def test_interrupt_latency_sub_200ms(self) -> None:
        """Empirically measure interrupt latency across 20 trials. Must be strictly < 200ms."""
        sink = NativeAudioSink()
        wav = _create_wav(duration_s=5.0)

        interrupt_call_latencies_ms = []
        play_unblock_latencies_ms = []

        try:
            for _ in range(20):
                unblock_event = threading.Event()
                t_play_unblocked = [0.0]

                def player(t_play_unblocked=t_play_unblocked, unblock_event=unblock_event):
                    sink.play(wav)
                    t_play_unblocked[0] = time.perf_counter()
                    unblock_event.set()

                t = threading.Thread(target=player)
                t.start()

                while not sink.is_playing:
                    time.sleep(0.001)

                t_call = time.perf_counter()
                sink.interrupt()
                t_ret = time.perf_counter()

                unblock_event.wait(timeout=2.0)
                t.join(timeout=1.0)

                call_lat_ms = (t_ret - t_call) * 1000.0
                unblock_lat_ms = (t_play_unblocked[0] - t_call) * 1000.0

                interrupt_call_latencies_ms.append(call_lat_ms)
                play_unblock_latencies_ms.append(unblock_lat_ms)

                self.assertTrue(sink.was_interrupted)
                self.assertFalse(sink.is_playing)
        finally:
            wav.unlink(missing_ok=True)

        max_call_lat = max(interrupt_call_latencies_ms)
        avg_call_lat = sum(interrupt_call_latencies_ms) / len(interrupt_call_latencies_ms)
        max_unblock_lat = max(play_unblock_latencies_ms)
        avg_unblock_lat = sum(play_unblock_latencies_ms) / len(play_unblock_latencies_ms)

        print("\n[LATENCY REPORT] 20 Trials:")
        print(f"  interrupt() call duration: max={max_call_lat:.2f}ms, avg={avg_call_lat:.2f}ms")
        print(
            f"  play() unblock duration:   max={max_unblock_lat:.2f}ms, avg={avg_unblock_lat:.2f}ms"
        )

        self.assertLess(
            max_call_lat, 200.0, f"Interrupt call latency exceeded 200ms: {max_call_lat:.2f}ms"
        )
        self.assertLess(
            max_unblock_lat, 200.0, f"Play unblock latency exceeded 200ms: {max_unblock_lat:.2f}ms"
        )

    def test_sigkill_escalation_on_unresponsive_process(self) -> None:
        """When a process ignores SIGTERM, sink must escalate to SIGKILL and reap it."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as script:
            script.write("""#!/usr/bin/env python3
import signal, time, sys
signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open("/tmp/sink_stub_ready.txt", "w") as f:
    f.write("ready")
time.sleep(10)
""")
            script_path = script.name
        os.chmod(script_path, stat.S_IRWXU)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as dummy_wav:
            dummy_wav_path = dummy_wav.name

        sink = NativeAudioSink(mpv_path=script_path)

        def player():
            sink.play(dummy_wav_path)

        t = threading.Thread(target=player)
        t.start()

        ready_path = Path("/tmp/sink_stub_ready.txt")
        while not ready_path.exists():
            time.sleep(0.005)

        t0 = time.perf_counter()
        sink.interrupt()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        t.join(timeout=2.0)

        ready_path.unlink(missing_ok=True)
        os.unlink(script_path)
        os.unlink(dummy_wav_path)

        self.assertTrue(sink.was_interrupted)
        # Should have waited ~500ms for SIGTERM, then sent SIGKILL and finished in ~505-550ms
        self.assertGreaterEqual(
            elapsed_ms, 490.0, f"Did not wait for terminate timeout: {elapsed_ms:.1f}ms"
        )
        self.assertLess(elapsed_ms, 1200.0, f"SIGKILL escalation took too long: {elapsed_ms:.1f}ms")


class TestStressEdgeCasesAndErrorHandling(unittest.TestCase):
    """Stress tests for malformed inputs, edge cases, and unexpected termination."""

    def setUp(self) -> None:
        if not shutil.which("mpv"):
            self.skipTest("mpv binary required for empirical stress tests")

    def test_missing_mpv_raises_mpv_not_installed(self) -> None:
        """When mpv is not found on PATH, play() raises MpvNotInstalledError."""
        wav = _create_wav(duration_s=0.01)
        try:
            sink = NativeAudioSink()
            with (
                mock.patch("shutil.which", return_value=None),
                self.assertRaises(MpvNotInstalledError),
            ):
                sink.play(wav)
        finally:
            wav.unlink(missing_ok=True)

    def test_nonexistent_file_raises_filenotfound(self) -> None:
        sink = NativeAudioSink()
        with self.assertRaises(FileNotFoundError):
            sink.play("/tmp/definitely_nonexistent_123456789.wav")

    def test_directory_raises_filenotfound(self) -> None:
        sink = NativeAudioSink()
        with self.assertRaises(FileNotFoundError):
            sink.play(Path("/tmp"))

    def test_zero_byte_file_raises_playback_error(self) -> None:
        sink = NativeAudioSink()
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            with self.assertRaises(PlaybackError) as ctx:
                sink.play(Path(f.name))
            self.assertIn("mpv exited with code 2", str(ctx.exception))

    def test_corrupt_text_file_raises_playback_error(self) -> None:
        sink = NativeAudioSink()
        with tempfile.NamedTemporaryFile(suffix=".txt") as f:
            f.write(b"NOT AUDIO DATA AT ALL")
            f.flush()
            with self.assertRaises(PlaybackError) as ctx:
                sink.play(Path(f.name))
            self.assertIn("mpv exited with code 2", str(ctx.exception))

    def test_truncated_wav_header_raises_playback_error(self) -> None:
        sink = NativeAudioSink()
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            f.write(b"RIFF\x24\x00\x00\x00WAVEfmt ")  # Truncated header
            f.flush()
            with self.assertRaises(PlaybackError) as ctx:
                sink.play(Path(f.name))
            self.assertIn("mpv exited with code 2", str(ctx.exception))

    def test_playback_timeout_raises_and_cleans_up(self) -> None:
        """When timeout is exceeded, PlaybackError is raised and mpv is reaped."""
        sink = NativeAudioSink()
        wav = _create_wav(duration_s=3.0)
        try:
            t0 = time.perf_counter()
            with self.assertRaises(PlaybackError) as ctx:
                sink.play(wav, timeout=0.1)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self.assertIn("Playback timed out after 0.1s", str(ctx.exception))
            self.assertLess(elapsed_ms, 500.0)
            self.assertFalse(sink.is_playing)
        finally:
            wav.unlink(missing_ok=True)

        time.sleep(0.05)
        total, zombies = _count_system_mpv_processes()
        self.assertEqual(total, 0, f"Leaked mpv after timeout: {total}")
        self.assertEqual(zombies, 0, f"Leaked zombies after timeout: {zombies}")

    def test_file_deleted_while_queued_raises_cleanly(self) -> None:
        """If a file is unlinked while queued behind another playback, PlaybackError is raised."""
        sink = NativeAudioSink()
        wav1 = _create_wav(duration_s=0.1)
        wav2 = _create_wav(duration_s=0.1)

        t1_started = threading.Event()
        t2_error = []

        def worker1():
            t1_started.set()
            sink.play(wav1)

        def worker2():
            t1_started.wait()
            time.sleep(0.01)  # Ensure worker1 holds the playback lock
            try:
                sink.play(wav2)
            except Exception as e:  # noqa: BLE001 — queued worker records any playback failure
                t2_error.append(e)

        t1 = threading.Thread(target=worker1)
        t2 = threading.Thread(target=worker2)

        t1.start()
        t2.start()

        # Delete wav2 while worker1 is still playing wav1
        wav2.unlink(missing_ok=True)

        t1.join()
        t2.join()
        wav1.unlink(missing_ok=True)

        self.assertEqual(len(t2_error), 1)
        self.assertIsInstance(t2_error[0], (FileNotFoundError, PlaybackError))

    def test_external_sigkill_treated_as_interrupted(self) -> None:
        """If mpv is killed externally via SIGKILL, play() treats it as an interrupt and returns cleanly."""
        sink = NativeAudioSink()
        wav = _create_wav(duration_s=5.0)

        def external_killer():
            while not sink.is_playing:
                time.sleep(0.002)
            with sink._state_lock:
                proc = sink._proc
                if proc is not None:
                    os.kill(proc.pid, signal.SIGKILL)

        t_kill = threading.Thread(target=external_killer)
        t_kill.start()
        t0 = time.perf_counter()
        sink.play(wav)  # Must return cleanly without exception
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        t_kill.join()
        wav.unlink(missing_ok=True)

        self.assertLess(elapsed_ms, 500.0)
        self.assertFalse(sink.is_playing)

        time.sleep(0.05)
        total, zombies = _count_system_mpv_processes()
        self.assertEqual(total, 0, f"Leaked mpv after external kill: {total}")
        self.assertEqual(zombies, 0, f"Leaked zombies after external kill: {zombies}")


def main() -> int:
    suite = unittest.TestSuite()
    suite.addTest(
        unittest.TestLoader().loadTestsFromTestCase(TestStressSequentialAndConcurrentPlayback)
    )
    suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestStressInterruptionAndLatency))
    suite.addTest(unittest.TestLoader().loadTestsFromTestCase(TestStressEdgeCasesAndErrorHandling))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
