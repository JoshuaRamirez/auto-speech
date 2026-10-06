"""Unit tests for NativeAudioSink. Run directly; no external dependencies required."""

from __future__ import annotations

import shutil
import signal
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


def _make_silent_wav(duration_s: float = 0.05, framerate: int = 24000) -> Path:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)
    n_frames = int(framerate * duration_s)
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(framerate)
        w.writeframes(b"\x00" * (n_frames * 2))
    return wav_path


class TestNativeAudioSink(unittest.TestCase):
    def test_missing_wav_file_raises_filenotfound(self) -> None:
        sink = NativeAudioSink()
        non_existent = Path("/tmp/does_not_exist_auto_speech_123.wav")
        with self.assertRaises(FileNotFoundError):
            sink.play(non_existent)

    def test_missing_mpv_raises_mpv_not_installed(self) -> None:
        wav_path = _make_silent_wav(0.05)
        try:
            sink = NativeAudioSink()
            with mock.patch("shutil.which", return_value=None):
                with self.assertRaises(MpvNotInstalledError) as ctx:
                    sink.play(wav_path)
                self.assertIn("mpv not found on PATH", str(ctx.exception))
        finally:
            wav_path.unlink(missing_ok=True)

    def test_play_successful_invokes_mpv(self) -> None:
        wav_path = _make_silent_wav(0.05)
        mock_proc = mock.MagicMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        mock_proc.poll.return_value = None

        with (
            mock.patch("shutil.which", return_value="/opt/homebrew/bin/mpv"),
            mock.patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
        ):
            try:
                sink = NativeAudioSink()
                sink.play(wav_path)

                mock_popen.assert_called_once_with(
                    [
                        "/opt/homebrew/bin/mpv",
                        "--really-quiet",
                        "--no-video",
                        "--keep-open=no",
                        "--idle=no",
                        str(wav_path),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                mock_proc.communicate.assert_called_once()
                self.assertFalse(sink.was_interrupted)
                self.assertFalse(sink.is_playing)
            finally:
                wav_path.unlink(missing_ok=True)

    def test_play_non_zero_exit_raises_playback_error(self) -> None:
        wav_path = _make_silent_wav(0.05)
        mock_proc = mock.MagicMock()
        mock_proc.communicate.return_value = (b"", b"Audio device error")
        mock_proc.returncode = 2
        mock_proc.poll.return_value = None

        with (
            mock.patch("shutil.which", return_value="/usr/bin/mpv"),
            mock.patch("subprocess.Popen", return_value=mock_proc),
        ):
            try:
                sink = NativeAudioSink()
                with self.assertRaises(PlaybackError) as ctx:
                    sink.play(wav_path)
                self.assertIn("mpv exited with code 2", str(ctx.exception))
                self.assertIn("Audio device error", str(ctx.exception))
            finally:
                wav_path.unlink(missing_ok=True)

    def test_interrupt_active_playback(self) -> None:
        wav_path = _make_silent_wav(0.05)
        mock_proc = mock.MagicMock()
        mock_proc.returncode = -signal.SIGTERM
        mock_proc.poll.return_value = None

        interrupted_event = threading.Event()

        def fake_terminate() -> None:
            interrupted_event.set()

        mock_proc.terminate.side_effect = fake_terminate

        def fake_communicate(*args, **kwargs):
            interrupted_event.wait(timeout=1.0)
            mock_proc.poll.return_value = -15
            return (b"", b"")

        mock_proc.communicate.side_effect = fake_communicate

        with (
            mock.patch("shutil.which", return_value="/usr/bin/mpv"),
            mock.patch("subprocess.Popen", return_value=mock_proc),
        ):
            try:
                sink = NativeAudioSink()

                def do_interrupt():
                    time.sleep(0.01)
                    sink.interrupt()

                t = threading.Thread(target=do_interrupt)
                t.start()
                sink.play(wav_path)
                t.join()

                self.assertTrue(sink.was_interrupted)
                mock_proc.terminate.assert_called_once()
            finally:
                wav_path.unlink(missing_ok=True)

    def test_interrupt_escalates_to_kill(self) -> None:
        mock_proc = mock.MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="mpv", timeout=0.5), None]

        sink = NativeAudioSink()
        sink._proc = mock_proc

        sink.interrupt()

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()

    def test_interrupt_when_idle_is_noop(self) -> None:
        sink = NativeAudioSink()
        sink.interrupt()  # Must not raise
        self.assertFalse(sink.is_playing)

    def test_custom_mpv_path_override(self) -> None:
        wav_path = _make_silent_wav(0.05)
        mock_proc = mock.MagicMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0
        mock_proc.poll.return_value = None

        custom_bin = "/custom/bin/my_mpv"
        with mock.patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            try:
                sink = NativeAudioSink(mpv_path=custom_bin)
                sink.play(wav_path)
                self.assertEqual(mock_popen.call_args[0][0][0], custom_bin)
            finally:
                wav_path.unlink(missing_ok=True)

    def test_play_timeout_raises_playback_error(self) -> None:
        wav_path = _make_silent_wav(0.05)
        mock_proc = mock.MagicMock()
        mock_proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="mpv", timeout=1.0)
        mock_proc.poll.return_value = None

        with (
            mock.patch("shutil.which", return_value="/usr/bin/mpv"),
            mock.patch("subprocess.Popen", return_value=mock_proc),
        ):
            try:
                sink = NativeAudioSink()
                with self.assertRaises(PlaybackError) as ctx:
                    sink.play(wav_path, timeout=1.0)
                self.assertIn("Playback timed out after 1.0s", str(ctx.exception))
                mock_proc.terminate.assert_called_once()
            finally:
                wav_path.unlink(missing_ok=True)

    def test_concurrent_playback_serialized(self) -> None:
        w1 = _make_silent_wav(0.05)
        w2 = _make_silent_wav(0.05)

        active_count = 0
        max_active = 0
        count_lock = threading.Lock()

        def fake_popen(cmd, *args, **kwargs):
            nonlocal active_count, max_active
            proc = mock.MagicMock()
            proc.poll.return_value = None

            def fake_comm(*a, **kw):
                nonlocal active_count, max_active
                with count_lock:
                    active_count += 1
                    max_active = max(max_active, active_count)
                time.sleep(0.02)
                with count_lock:
                    active_count -= 1
                proc.returncode = 0
                return (b"", b"")

            proc.communicate.side_effect = fake_comm
            return proc

        with (
            mock.patch("shutil.which", return_value="/usr/bin/mpv"),
            mock.patch("subprocess.Popen", side_effect=fake_popen),
        ):
            try:
                sink = NativeAudioSink()
                t1 = threading.Thread(target=lambda: sink.play(w1))
                t2 = threading.Thread(target=lambda: sink.play(w2))
                t1.start()
                t2.start()
                t1.join()
                t2.join()

                self.assertEqual(max_active, 1)
            finally:
                w1.unlink(missing_ok=True)
                w2.unlink(missing_ok=True)

    def test_real_playback_smoke(self) -> None:
        if not shutil.which("mpv"):
            self.skipTest("mpv binary required for integration smoke test")
        sink = NativeAudioSink()
        wav_path = _make_silent_wav(0.1)
        try:
            t0 = time.time()
            sink.play(wav_path)
            elapsed = time.time() - t0
            self.assertGreaterEqual(elapsed, 0.05)
            self.assertFalse(sink.was_interrupted)
        finally:
            wav_path.unlink(missing_ok=True)

    def test_real_interrupt_smoke(self) -> None:
        if not shutil.which("mpv"):
            self.skipTest("mpv binary required for integration smoke test")
        sink = NativeAudioSink()
        wav_path = _make_silent_wav(2.0)
        try:

            def interrupter():
                time.sleep(0.05)
                sink.interrupt()

            t = threading.Thread(target=interrupter)
            t0 = time.time()
            t.start()
            sink.play(wav_path)
            t.join()
            elapsed = time.time() - t0

            self.assertTrue(sink.was_interrupted)
            self.assertLess(elapsed, 0.6, f"Interrupt was too slow: {elapsed:.2f}s")
        finally:
            wav_path.unlink(missing_ok=True)


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestNativeAudioSink)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
