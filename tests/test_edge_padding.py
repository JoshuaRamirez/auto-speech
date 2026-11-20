"""A clip is played with quiet after it, so the device does not swallow its end
(his 2026-11-20 11:56 "autospeech stuff gets cutoff at the end and maybe the start").

No sound: the argv tests mock nothing and start nothing; the behaviour tests
run the real mpv into a file (--ao=pcm) and read what would have reached the
device. Those are skipped where mpv is not installed (the bare CI interpreter).
"""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
import math
import os
import shutil
import struct
import subprocess
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

from quiet_audio import ensure_quiet  # noqa: E402

ensure_quiet()

from edge_padding import DEFAULT_TAIL_MS, TAIL_ENV, EdgePadding  # noqa: E402
from native_audio_sink import MPV_FALLBACK_PATHS, NativeAudioSink  # noqa: E402

RATE = 24000


def _mpv() -> str | None:
    found = shutil.which("mpv")
    if found:
        return found
    for path in MPV_FALLBACK_PATHS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _write_tone(path: Path, seconds: float) -> bytes:
    """A clip that is loud from its first sample to its last: the worst case
    for a device that drops an edge. Returns its sample bytes."""
    n = int(RATE * seconds)
    raw = b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / RATE)) or 1) for i in range(n))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(raw)
    return raw


def _samples_sent(sink: NativeAudioSink, mpv: str, clip: Path, out: Path) -> bytes:
    """Run the sink's own mpv command line into a file instead of a device and
    return the samples written (read by size: mpv's pcm header length is not trusted)."""
    argv = sink.mpv_argv(mpv, clip)
    argv.insert(-1, f"--ao-pcm-file={out}")
    subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=60)
    data = out.read_bytes()
    return data[data.find(b"data") + 8:]


class TailArgvTest(unittest.TestCase):
    def test_default_sink_asks_mpv_for_the_tail(self) -> None:
        with mock.patch.dict(os.environ, {TAIL_ENV: ""}):
            argv = NativeAudioSink(sound_yield=False).mpv_argv("/x/mpv", "a.wav")
        self.assertIn(f"--af=lavfi=[apad=pad_dur={DEFAULT_TAIL_MS / 1000:g}]", argv)
        self.assertEqual(argv[-1], "a.wav")  # the file stays last

    def test_env_sets_the_tail_and_zero_turns_it_off(self) -> None:
        with mock.patch.dict(os.environ, {TAIL_ENV: "250"}):
            self.assertIn("--af=lavfi=[apad=pad_dur=0.25]", NativeAudioSink(sound_yield=False).mpv_argv("/x/mpv", "a.wav"))
        with mock.patch.dict(os.environ, {TAIL_ENV: "0"}):
            argv = NativeAudioSink(sound_yield=False).mpv_argv("/x/mpv", "a.wav")
        self.assertFalse([a for a in argv if a.startswith("--af")], argv)

    def test_unreadable_or_wild_env_is_cleaned_not_refused(self) -> None:
        with mock.patch.dict(os.environ, {TAIL_ENV: "soon"}):
            self.assertEqual(EdgePadding.from_env().tail_ms, DEFAULT_TAIL_MS)
        with mock.patch.dict(os.environ, {TAIL_ENV: " 120.0 "}):
            self.assertEqual(EdgePadding.from_env().tail_ms, 120)
        with mock.patch.dict(os.environ, {TAIL_ENV: "-5"}):
            self.assertEqual(EdgePadding.from_env().tail_ms, 0)
        with mock.patch.dict(os.environ, {TAIL_ENV: "999999"}):
            self.assertEqual(EdgePadding.from_env().tail_ms, 3000)

    def test_tail_keeps_its_length_to_the_ear_at_other_rates(self) -> None:
        sink = NativeAudioSink(sound_yield=False, padding=EdgePadding(tail_ms=500))
        sink.set_speed(2.0)
        self.assertIn("--af=lavfi=[apad=pad_dur=1]", sink.mpv_argv("/x/mpv", "a.wav"))

    def test_position_stops_at_the_clips_end_through_the_tail(self) -> None:
        pad = EdgePadding(tail_ms=500)
        self.assertEqual(pad.clip_position(1.25, 2.0), 1.25)
        self.assertEqual(pad.clip_position(2.4, 2.0), 2.0)
        self.assertEqual(pad.clip_position(2.4, None), 2.4)  # length unknown: mpv's word stands


@unittest.skipUnless(_mpv(), "mpv is not installed")
class TailReachesTheDeviceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="auto-speech-edge-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.clip = self.dir / "clip.wav"
        self.raw = _write_tone(self.clip, 0.4)

    def test_without_padding_the_stream_ends_on_the_last_spoken_sample(self) -> None:
        """What was live until 2026-11-20: nothing after the clip's last sample."""
        sink = NativeAudioSink(sound_yield=False, audio_output="pcm", padding=EdgePadding.none())
        sink.stall_seconds = 0
        self.assertEqual(_samples_sent(sink, _mpv(), self.clip, self.dir / "out.wav"), self.raw)

    def test_the_whole_clip_then_half_a_second_of_quiet_is_sent(self) -> None:
        sink = NativeAudioSink(sound_yield=False, audio_output="pcm", padding=EdgePadding(tail_ms=500))
        sink.stall_seconds = 0
        sent = _samples_sent(sink, _mpv(), self.clip, self.dir / "out.wav")
        self.assertEqual(sent[: len(self.raw)], self.raw, "the clip must arrive whole and unchanged")
        after = sent[len(self.raw):]
        self.assertEqual(len(after) / 2 / RATE, 0.5, f"{len(after) / 2 / RATE * 1000:.0f} ms followed the clip, not 500")
        self.assertFalse(any(after), "what follows the clip must be silence")


if __name__ == "__main__":
    unittest.main()
