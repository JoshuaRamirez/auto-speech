"""The suite never plays sound (his 2026-10-20 10:40 "turn off the speech tests").

Hermetic: mpv is mocked. Covers the sink's --ao option (env, argument, default), the
quiet helper's opt-in, the runner's export, and that every test that can reach mpv
calls the helper at import.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[0] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))

from quiet_audio import AUDIO_OUTPUT_ENV, REAL_AUDIO_ENV, ensure_quiet, real_audio_allowed  # noqa: E402

ensure_quiet()

from native_audio_sink import NativeAudioSink  # noqa: E402

MPV_TOUCHING_TESTS = [
    "test_native_audio_sink.py",
    "test_native_audio_sink_stress.py",
    "test_playback_consumer.py",
    "test_autoplay_worker.py",
    "test_playback_cannot_hang.py",
    "test_speak_client.py",
    "test_queue_robustness.py",
    "test_synthesize_endpoint.py",
]


def _silent_wav() -> Path:
    fd, name = tempfile.mkstemp(prefix="auto-speech-quiet-", suffix=".wav")
    os.close(fd)
    with wave.open(name, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00" * 480)
    return Path(name)


def _argv_of(sink: NativeAudioSink, wav: Path) -> list[str]:
    proc = mock.MagicMock()
    proc.communicate.return_value = (b"", b"")
    proc.returncode = 0
    proc.poll.return_value = None
    with (
        mock.patch("shutil.which", return_value="/opt/homebrew/bin/mpv"),
        mock.patch("subprocess.Popen", return_value=proc) as popen,
    ):
        sink.play(wav)
    return list(popen.call_args.args[0])


class TestSinkAudioOutput(unittest.TestCase):
    def setUp(self) -> None:
        self.wav = _silent_wav()

    def tearDown(self) -> None:
        self.wav.unlink(missing_ok=True)

    def test_env_null_puts_ao_null_before_the_file(self) -> None:
        with mock.patch.dict(os.environ, {AUDIO_OUTPUT_ENV: "null"}):
            sink = NativeAudioSink(sound_yield=False)
            argv = _argv_of(sink, self.wav)
        self.assertEqual(sink.audio_output, "null")
        self.assertIn("--ao=null", argv)
        self.assertEqual(argv[-1], str(self.wav))
        self.assertLess(argv.index("--ao=null"), len(argv) - 1)

    def test_unset_or_blank_env_leaves_mpv_default(self) -> None:
        for value in ("", "   "):
            with mock.patch.dict(os.environ, {AUDIO_OUTPUT_ENV: value}):
                sink = NativeAudioSink(sound_yield=False)
                argv = _argv_of(sink, self.wav)
            self.assertIsNone(sink.audio_output)
            self.assertFalse([a for a in argv if a.startswith("--ao=")], argv)
        env = {k: v for k, v in os.environ.items() if k != AUDIO_OUTPUT_ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(NativeAudioSink(sound_yield=False).audio_output)

    def test_argument_wins_over_env(self) -> None:
        with mock.patch.dict(os.environ, {AUDIO_OUTPUT_ENV: "null"}):
            sink = NativeAudioSink(sound_yield=False, audio_output="coreaudio")
            argv = _argv_of(sink, self.wav)
        self.assertIn("--ao=coreaudio", argv)
        self.assertNotIn("--ao=null", argv)
        self.assertIsNone(NativeAudioSink(sound_yield=False, audio_output="").audio_output)

    def test_sink_argv_is_the_documented_shape(self) -> None:
        sink = NativeAudioSink(sound_yield=False, audio_output="null")
        argv = sink.mpv_argv("/x/mpv", self.wav)
        self.assertEqual(argv[0], "/x/mpv")
        self.assertEqual(argv[-2:], ["--ao=null", str(self.wav)])
        self.assertIn("--input-media-keys=no", argv)


class TestQuietHelper(unittest.TestCase):
    def test_without_the_flag_the_output_is_forced_to_null(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in (AUDIO_OUTPUT_ENV, REAL_AUDIO_ENV)}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(real_audio_allowed())
            self.assertEqual(ensure_quiet(), "null")
            self.assertEqual(os.environ[AUDIO_OUTPUT_ENV], "null")
        with mock.patch.dict(os.environ, {AUDIO_OUTPUT_ENV: "coreaudio", REAL_AUDIO_ENV: "0"}):
            self.assertEqual(ensure_quiet(), "null")  # a chosen output without the flag is still silenced

    def test_with_the_flag_nothing_is_forced(self) -> None:
        with mock.patch.dict(os.environ, {REAL_AUDIO_ENV: "1", AUDIO_OUTPUT_ENV: "coreaudio"}):
            self.assertTrue(real_audio_allowed())
            self.assertEqual(ensure_quiet(), "coreaudio")
            self.assertEqual(os.environ[AUDIO_OUTPUT_ENV], "coreaudio")
        env = {k: v for k, v in os.environ.items() if k != AUDIO_OUTPUT_ENV}
        env[REAL_AUDIO_ENV] = "1"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(ensure_quiet())
            self.assertNotIn(AUDIO_OUTPUT_ENV, os.environ)


class TestSuiteWiring(unittest.TestCase):
    def test_run_all_exports_the_null_output_unless_opted_in(self) -> None:
        text = (HERE / "run_all.sh").read_text()
        self.assertIn("export AUTO_SPEECH_MPV_AO=null", text)
        self.assertRegex(text, r'if \[\[ "\$\{AUTO_SPEECH_TEST_REAL_AUDIO:-\}" == "1" \]\]')
        self.assertIn("test_quiet_tests.py", text)

    def test_every_mpv_touching_test_calls_ensure_quiet_at_import(self) -> None:
        for name in MPV_TOUCHING_TESTS:
            text = (HERE / name).read_text()
            self.assertTrue(re.search(r"^from quiet_audio import ensure_quiet", text, re.M), name)
            self.assertTrue(re.search(r"^ensure_quiet\(\)", text, re.M), name)
            body = text.split("ensure_quiet()", 1)[0]
            self.assertFalse(re.search(r"^from native_audio_sink import|^import native_audio_sink", body, re.M), name)

    def test_conftest_makes_a_bare_pytest_silent(self) -> None:
        text = (HERE / "conftest.py").read_text()
        self.assertIn("ensure_quiet()", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
