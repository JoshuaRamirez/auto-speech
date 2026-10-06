"""Unit tests for AppleSayEngine (native macOS neural voice synthesis)."""

from __future__ import annotations

import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from apple_say_engine import (
    AppleSayEngine,
    get_available_say_voices,
    get_default_say_voice,
)
from tts_engine import TTSGenerationError
from voice_profile import VoiceProfile


class TestAppleSayEngine(unittest.TestCase):
    """Test suite for AppleSayEngine voice resolution, rate calibration, and synthesis."""

    def test_voice_discovery_prefers_ava_premium(self) -> None:
        mock_output = (
            "Alex                en_US    # Most people recognize me by my voice.\n"
            "Ava (Enhanced)      en_US    # Hello! My name is Ava.\n"
            "Ava (Premium)       en_US    # Hello! My name is Ava.\n"
            "Samantha            en_US    # Hello! My name is Samantha.\n"
        )
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout=mock_output)
            voices = get_available_say_voices(force_refresh=True)
            self.assertIn("Ava (Premium)", voices)
            self.assertEqual(voices["Ava (Premium)"], "en_US")
            self.assertIn("Ava (Enhanced)", voices)

            best = get_default_say_voice(force_refresh=True)
            self.assertEqual(best, "Ava (Premium)")

    def test_voice_discovery_fallback_when_premium_missing(self) -> None:
        mock_output = (
            "Alex                en_US    # Most people recognize me by my voice.\n"
            "Ava (Enhanced)      en_US    # Hello! My name is Ava.\n"
            "Samantha            en_US    # Hello! My name is Samantha.\n"
        )
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout=mock_output)
            best = get_default_say_voice(force_refresh=True)
            self.assertEqual(best, "Ava (Enhanced)")

    def test_rate_calibration(self) -> None:
        engine = AppleSayEngine(default_rate=212)
        self.assertEqual(engine.resolve_rate(1.0), 212)
        self.assertEqual(engine.resolve_rate(1.2), 254)
        self.assertEqual(engine.resolve_rate(0.8), 170)
        # Clamped bounds
        self.assertEqual(engine.resolve_rate(0.1), 50)
        self.assertEqual(engine.resolve_rate(5.0), 500)

    def test_empty_text_raises_generation_error(self) -> None:
        engine = AppleSayEngine()
        with tempfile.TemporaryDirectory() as tmpdir:
            out_wav = Path(tmpdir) / "out.wav"
            with self.assertRaises(TTSGenerationError):
                engine.synthesize("", out_path=out_wav)
            with self.assertRaises(TTSGenerationError):
                engine.synthesize("   \n\t  ", out_path=out_wav)

    def test_synthesize_command_construction(self) -> None:
        engine = AppleSayEngine(voice="Ava (Premium)", default_rate=212)
        profile = VoiceProfile(
            voice_id="Ava (Premium)",
            speed=1.0,
            chars_per_second=15.0,
            calibrated_at="2026-10-04T00:00:00Z",
            calibration_source_chars=100,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            out_wav = Path(tmpdir) / "out.wav"

            def fake_run(cmd, capture_output=True, text=True, check=False):
                # Simulate say writing the partial file
                partial = out_wav.with_suffix(".wav.partial")
                partial.write_bytes(b"RIFFdummydataWAVE")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch("subprocess.run", side_effect=fake_run) as mock_run:
                engine.synthesize("Hello world", voice_profile=profile, out_path=out_wav)
                self.assertTrue(out_wav.exists())
                self.assertEqual(out_wav.read_bytes(), b"RIFFdummydataWAVE")

                cmd = mock_run.call_args[0][0]
                self.assertEqual(cmd[0], "say")
                self.assertEqual(cmd[1:3], ["-v", "Ava (Premium)"])
                self.assertEqual(cmd[3:5], ["-r", "212"])
                self.assertEqual(cmd[5:7], ["--file-format=WAVE", "--data-format=LEI16@24000"])
                self.assertEqual(cmd[7:9], ["-o", str(out_wav.with_suffix(".wav.partial"))])
                self.assertEqual(cmd[9], "Hello world")

    def test_default_system_voice_omits_flags(self) -> None:
        engine = AppleSayEngine()
        profile = VoiceProfile(
            voice_id="system",
            speed=1.0,
            chars_per_second=15.0,
            calibrated_at="2026-10-04T00:00:00Z",
            calibration_source_chars=100,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            out_wav = Path(tmpdir) / "out.wav"

            def fake_run(cmd, capture_output=True, text=True, check=False):
                partial = out_wav.with_suffix(".wav.partial")
                partial.write_bytes(b"RIFFdummydataWAVE")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch("subprocess.run", side_effect=fake_run) as mock_run:
                engine.synthesize("Hello default world", voice_profile=profile, out_path=out_wav)
                cmd = mock_run.call_args[0][0]
                self.assertEqual(cmd[0], "say")
                self.assertNotIn("-v", cmd)
                self.assertNotIn("-r", cmd)
                self.assertIn("--file-format=WAVE", cmd)
                self.assertEqual(cmd[-1], "Hello default world")

    def test_speed_override_adds_rate_flag(self) -> None:
        engine = AppleSayEngine()
        profile = VoiceProfile(
            voice_id="system",
            speed=1.2,
            chars_per_second=15.0,
            calibrated_at="2026-10-04T00:00:00Z",
            calibration_source_chars=100,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            out_wav = Path(tmpdir) / "out.wav"

            def fake_run(cmd, capture_output=True, text=True, check=False):
                partial = out_wav.with_suffix(".wav.partial")
                partial.write_bytes(b"RIFFdummydataWAVE")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch("subprocess.run", side_effect=fake_run) as mock_run:
                engine.synthesize("Hello faster world", voice_profile=profile, out_path=out_wav)
                cmd = mock_run.call_args[0][0]
                self.assertNotIn("-v", cmd)
                self.assertIn("-r", cmd)
                rate_idx = cmd.index("-r")
                self.assertEqual(cmd[rate_idx + 1], "254")

    def test_large_text_uses_temp_file_flag(self) -> None:
        engine = AppleSayEngine(voice="Ava (Premium)")
        large_text = "word " * 2000  # 10,000 chars > 8192
        with tempfile.TemporaryDirectory() as tmpdir:
            out_wav = Path(tmpdir) / "out.wav"

            def fake_run(cmd, capture_output=True, text=True, check=False):
                partial = out_wav.with_suffix(".wav.partial")
                partial.write_bytes(b"RIFFdummydataWAVE")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch("subprocess.run", side_effect=fake_run) as mock_run:
                engine.synthesize(large_text, out_path=out_wav)
                cmd = mock_run.call_args[0][0]
                self.assertIn("-f", cmd)
                # Confirm text itself was not passed as raw argument
                self.assertNotIn(large_text, cmd)

    @unittest.skipUnless(sys.platform == "darwin", "Requires macOS say")
    def test_real_synthesis_generates_valid_24khz_wav(self) -> None:
        """Integration test on macOS: produces bit-exact 24kHz LEI16 mono RIFF WAV."""
        engine = AppleSayEngine()
        with tempfile.TemporaryDirectory() as tmpdir:
            out_wav = Path(tmpdir) / "real.wav"
            engine.synthesize("Unit test live speech verification.", out_path=out_wav)
            self.assertTrue(out_wav.is_file())
            self.assertGreater(out_wav.stat().st_size, 44)

            with wave.open(str(out_wav), "rb") as w:
                self.assertEqual(w.getnchannels(), 1, "Must be mono audio")
                self.assertEqual(w.getsampwidth(), 2, "Must be 16-bit (2 bytes)")
                self.assertEqual(w.getframerate(), 24000, "Must be 24000 Hz")
                self.assertGreater(w.getnframes(), 100)


if __name__ == "__main__":
    unittest.main()
