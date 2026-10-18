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

import native_audio_sink  # noqa: E402

# Hermetic: these tests drive a real NativeAudioSink against a mocked mpv;
# no sound-yield gate, so they never read Core Audio or hold.
native_audio_sink.DEFAULT_SOUND_YIELD_FACTORY = lambda: None
from native_audio_sink import (  # noqa: E402
    MpvNotInstalledError,
    NativeAudioSink,
    PlaybackError,
)


def _make_silent_wav(duration_s: float = 0.05, framerate: int = 24000) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
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
                    if active_count > max_active:
                        max_active = active_count
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




class _ScriptedGate:
    """A sound-yield gate whose outcome is scripted; records abort probes."""

    def __init__(self, outcome, *, abort_after: int | None = None):
        self.outcome = outcome
        self.abort_after = abort_after
        self.calls = 0
        self.kwargs: list[dict] = []

    def wait_until_clear(self, should_abort=None, **kwargs):
        from sound_yield import ABORTED, HoldOutcome

        self.calls += 1
        self.kwargs.append(kwargs)
        on_hold = kwargs.get("on_hold")
        if on_hold is not None and self.outcome is not None and self.outcome.waited_seconds > 0:
            on_hold(self.outcome.blockers)
        if self.abort_after is not None and should_abort is not None:
            for _ in range(self.abort_after):
                if should_abort():
                    return HoldOutcome(ABORTED, 0.5, ("Music",))
            return HoldOutcome(ABORTED, 1.0, ("Music",))
        return self.outcome


class TestNativeAudioSinkSoundYield(unittest.TestCase):
    """The sink consults the gate before mpv starts; the gate is injected,
    so the audio layer is fully mocked here."""

    def _mpv(self):
        proc = mock.MagicMock()
        proc.communicate.return_value = (b"", b"")
        proc.returncode = 0
        proc.pid = 4321
        proc.poll.return_value = 0
        return proc

    def test_clear_outcome_plays(self) -> None:
        from sound_yield import CLEAR, HoldOutcome

        wav = _make_silent_wav(0.05)
        gate = _ScriptedGate(HoldOutcome(CLEAR))
        try:
            with mock.patch("shutil.which", return_value="/usr/bin/mpv"), mock.patch(
                "subprocess.Popen", return_value=self._mpv()
            ) as popen:
                NativeAudioSink(sound_yield=gate).play(wav)
            self.assertEqual(gate.calls, 1)
            popen.assert_called_once()
        finally:
            wav.unlink(missing_ok=True)

    def test_resumed_outcome_plays_and_records_the_hold(self) -> None:
        from sound_yield import RESUMED, HoldOutcome

        wav = _make_silent_wav(0.05)
        gate = _ScriptedGate(HoldOutcome(RESUMED, 3.5, ("Music (pid 1)",)))
        try:
            with mock.patch("shutil.which", return_value="/usr/bin/mpv"), mock.patch(
                "subprocess.Popen", return_value=self._mpv()
            ) as popen:
                sink = NativeAudioSink(sound_yield=gate)
                sink.play(wav)
            popen.assert_called_once()
            self.assertEqual(sink.hold_status["last_hold"]["status"], RESUMED)
            self.assertEqual(sink.hold_status["last_hold"]["blockers"], ["Music (pid 1)"])
            self.assertFalse(sink.hold_status["holding"])
        finally:
            wav.unlink(missing_ok=True)

    def test_expired_raises_playback_yielded_and_never_starts_mpv(self) -> None:
        from native_audio_sink import AudioSinkError, PlaybackYielded
        from sound_yield import EXPIRED, HoldOutcome

        wav = _make_silent_wav(0.05)
        outcome = HoldOutcome(EXPIRED, 180.0, ("Spotify (pid 2)",))
        gate = _ScriptedGate(outcome)
        sink = NativeAudioSink(sound_yield=gate)
        try:
            with mock.patch("shutil.which", return_value="/usr/bin/mpv"), mock.patch(
                "subprocess.Popen", return_value=self._mpv()
            ) as popen:
                with self.assertRaises(PlaybackYielded) as ctx:
                    sink.play(wav)
            popen.assert_not_called()
            self.assertIn("set aside: held 180s while Spotify (pid 2) played sound", str(ctx.exception))
            self.assertIs(ctx.exception.outcome, outcome)
            self.assertIsInstance(ctx.exception, AudioSinkError)
            self.assertIs(sink.last_hold, outcome)
        finally:
            wav.unlink(missing_ok=True)

    def test_hold_false_skips_the_gate_and_plays_over_other_sound(self) -> None:
        from sound_yield import EXPIRED, HoldOutcome

        wav = _make_silent_wav(0.05)
        gate = _ScriptedGate(HoldOutcome(EXPIRED, 180.0, ("Spotify (pid 2)",)))
        sink = NativeAudioSink(sound_yield=gate)
        try:
            with mock.patch("shutil.which", return_value="/usr/bin/mpv"), mock.patch(
                "subprocess.Popen", return_value=self._mpv()
            ) as popen:
                sink.play(wav, hold=False)
            popen.assert_called_once()
            self.assertEqual(gate.calls, 0)
            self.assertIsNone(sink.last_hold)
        finally:
            wav.unlink(missing_ok=True)

    def test_queue_age_and_on_hold_reach_the_gate(self) -> None:
        from sound_yield import RESUMED, HoldOutcome

        wav = _make_silent_wav(0.05)
        outcome = HoldOutcome(RESUMED, 4.0, ("Music (pid 1)",))
        gate = _ScriptedGate(outcome)
        seen: list[tuple[str, ...]] = []
        try:
            with mock.patch("shutil.which", return_value="/usr/bin/mpv"), mock.patch(
                "subprocess.Popen", return_value=self._mpv()
            ) as popen:
                sink = NativeAudioSink(sound_yield=gate)
                sink.play(wav, already_waited=42.0, on_hold=seen.append)
            popen.assert_called_once()
            self.assertEqual(gate.kwargs[0]["already_waited"], 42.0)
            self.assertEqual(seen, [("Music (pid 1)",)])
            self.assertIs(sink.last_hold, outcome)
        finally:
            wav.unlink(missing_ok=True)

    def test_interrupt_releases_a_held_playback_quietly(self) -> None:
        wav = _make_silent_wav(0.05)
        gate = _ScriptedGate(None, abort_after=50)
        try:
            with mock.patch("shutil.which", return_value="/usr/bin/mpv"), mock.patch(
                "subprocess.Popen", return_value=self._mpv()
            ) as popen:
                sink = NativeAudioSink(sound_yield=gate)
                sink.interrupt()  # before the hold: cleared at hold start, so no effect
                t = threading.Thread(target=lambda: sink.play(wav))
                # Abort on the 10th probe by flipping the sink's own abort event.
                probes = {"n": 0}
                real_abort = sink._hold_abort.is_set

                def counting_abort():
                    probes["n"] += 1
                    if probes["n"] == 10:
                        sink.interrupt()
                    return real_abort()

                sink._hold_abort.is_set = counting_abort  # type: ignore[method-assign]
                t.start()
                t.join(timeout=5)
            self.assertFalse(t.is_alive())
            popen.assert_not_called()
            self.assertTrue(sink.was_interrupted)
            self.assertEqual(sink.hold_status["last_hold"]["status"], "aborted")
        finally:
            wav.unlink(missing_ok=True)

    def test_no_gate_never_consults_anything(self) -> None:
        wav = _make_silent_wav(0.05)
        try:
            with mock.patch("shutil.which", return_value="/usr/bin/mpv"), mock.patch(
                "subprocess.Popen", return_value=self._mpv()
            ) as popen:
                sink = NativeAudioSink(sound_yield=False)
                sink.play(wav)
            popen.assert_called_once()
            self.assertIsNone(sink.hold_status["last_hold"])
        finally:
            wav.unlink(missing_ok=True)

    def test_default_gate_comes_from_the_factory(self) -> None:
        sentinel = _ScriptedGate(None)
        with mock.patch.object(native_audio_sink, "DEFAULT_SOUND_YIELD_FACTORY", lambda: sentinel):
            self.assertIs(NativeAudioSink()._sound_yield, sentinel)
        self.assertIsNone(NativeAudioSink()._sound_yield)  # this file's hermetic rebind


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(
        [
            loader.loadTestsFromTestCase(TestNativeAudioSink),
            loader.loadTestsFromTestCase(TestNativeAudioSinkSoundYield),
        ]
    )
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
