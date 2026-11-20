"""A clip is played with quiet before and after it, so the device does not
swallow its start or its end (his 2026-11-20 11:56 "autospeech stuff gets
cutoff at the end and maybe the start").

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

from edge_padding import (  # noqa: E402
    DEFAULT_LEAD_IN_MS,
    DEFAULT_TAIL_MS,
    DEFAULT_WAKE_MS,
    LEAD_IN_ENV,
    TAIL_ENV,
    WAKE_ENV,
    WARM_SECONDS,
    EdgePadding,
)
from native_audio_sink import MPV_FALLBACK_PATHS, NativeAudioSink  # noqa: E402

RATE = 24000
NO_ENV = {LEAD_IN_ENV: "", TAIL_ENV: "", WAKE_ENV: ""}


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


def _warm(sink: NativeAudioSink) -> NativeAudioSink:
    """A sink whose last line just ended: the device is awake, the short silent lead-in applies."""
    sink._play_cold = False
    return sink


def _samples_sent(padding: EdgePadding, clip: Path, out: Path, *, cold: bool = False) -> bytes:
    """Run the sink's own mpv command line into a file instead of a device and
    return the samples written (read by size: mpv's pcm header length is not trusted)."""
    sink = NativeAudioSink(sound_yield=False, audio_output="pcm", padding=padding)
    sink.stall_seconds = 0
    sink._play_cold = cold
    argv = sink.mpv_argv(_mpv(), clip)
    argv.insert(-1, "--audio-format=s16")   # the file in the clip's own sample format, whatever the filter works in
    argv.insert(-1, f"--ao-pcm-file={out}")
    subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=60)
    data = out.read_bytes()
    return data[data.find(b"data") + 8:]


def _ms(sample_bytes: bytes) -> float:
    return len(sample_bytes) / 2 / RATE * 1000


def _filter_of(argv: list[str]) -> list[str]:
    return [a for a in argv if a.startswith("--af")]


class TailArgvTest(unittest.TestCase):
    def test_default_sink_asks_mpv_for_the_tail(self) -> None:
        with mock.patch.dict(os.environ, {LEAD_IN_ENV: "0", TAIL_ENV: ""}):
            argv = _warm(NativeAudioSink(sound_yield=False)).mpv_argv("/x/mpv", "a.wav")
        self.assertIn(f"--af=lavfi=[apad=pad_dur={DEFAULT_TAIL_MS / 1000:g}]", argv)
        self.assertEqual(argv[-1], "a.wav")  # the file stays last

    def test_env_sets_the_tail_and_zero_turns_it_off(self) -> None:
        with mock.patch.dict(os.environ, {LEAD_IN_ENV: "0", TAIL_ENV: "250"}):
            self.assertIn("--af=lavfi=[apad=pad_dur=0.25]", _warm(NativeAudioSink(sound_yield=False)).mpv_argv("/x/mpv", "a.wav"))
        with mock.patch.dict(os.environ, {LEAD_IN_ENV: "0", TAIL_ENV: "0"}):
            argv = _warm(NativeAudioSink(sound_yield=False)).mpv_argv("/x/mpv", "a.wav")
        self.assertFalse(_filter_of(argv), argv)

    def test_unreadable_or_wild_env_is_cleaned_not_refused(self) -> None:
        for name, default, field in ((TAIL_ENV, DEFAULT_TAIL_MS, "tail_ms"), (LEAD_IN_ENV, DEFAULT_LEAD_IN_MS, "lead_ms")):
            for raw, want in (("soon", default), (" 120.0 ", 120), ("-5", 0), ("999999", 3000)):
                with mock.patch.dict(os.environ, {**NO_ENV, name: raw}):
                    self.assertEqual(getattr(EdgePadding.from_env(), field), want, f"{name}={raw!r}")

    def test_tail_keeps_its_length_to_the_ear_at_other_rates(self) -> None:
        sink = _warm(NativeAudioSink(sound_yield=False, padding=EdgePadding(lead_ms=0, tail_ms=500)))
        sink.set_speed(2.0)
        self.assertIn("--af=lavfi=[apad=pad_dur=1]", sink.mpv_argv("/x/mpv", "a.wav"))

    def test_position_stops_at_the_clips_end_through_the_tail(self) -> None:
        pad = EdgePadding(lead_ms=0, tail_ms=500)
        self.assertEqual(pad.clip_position(1.25, 2.0), 1.25)
        self.assertEqual(pad.clip_position(2.4, 2.0), 2.0)
        self.assertEqual(pad.clip_position(2.4, None), 2.4)  # length unknown: mpv's word stands


class LeadInArgvTest(unittest.TestCase):
    def test_default_sink_asks_mpv_for_the_lead_in_before_the_tail(self) -> None:
        with mock.patch.dict(os.environ, NO_ENV):
            argv = _warm(NativeAudioSink(sound_yield=False)).mpv_argv("/x/mpv", "a.wav")
        self.assertEqual(
            _filter_of(argv),
            [f"--af=lavfi=[adelay=delays={DEFAULT_LEAD_IN_MS}:all=1,apad=pad_dur={DEFAULT_TAIL_MS / 1000:g}]"],
        )

    def test_env_sets_the_lead_in_and_zero_turns_it_off(self) -> None:
        with mock.patch.dict(os.environ, {LEAD_IN_ENV: "150", TAIL_ENV: "0"}):
            self.assertEqual(_filter_of(_warm(NativeAudioSink(sound_yield=False)).mpv_argv("/x/mpv", "a.wav")),
                             ["--af=lavfi=[adelay=delays=150:all=1]"])
        with mock.patch.dict(os.environ, {LEAD_IN_ENV: "0", TAIL_ENV: "0"}):
            self.assertFalse(_filter_of(_warm(NativeAudioSink(sound_yield=False)).mpv_argv("/x/mpv", "a.wav")))

    def test_lead_in_keeps_its_length_to_the_ear_at_other_rates(self) -> None:
        sink = _warm(NativeAudioSink(sound_yield=False, padding=EdgePadding(lead_ms=300, tail_ms=0)))
        sink.set_speed(1.5)
        self.assertIn("--af=lavfi=[adelay=delays=450:all=1]", sink.mpv_argv("/x/mpv", "a.wav"))

    def test_position_is_the_clips_own_not_mpvs(self) -> None:
        pad = EdgePadding(lead_ms=300, tail_ms=500)
        self.assertEqual(pad.clip_position(0.1, 2.0), 0.0)             # still in the quiet before the clip
        self.assertAlmostEqual(pad.clip_position(1.3, 2.0), 1.0)
        self.assertEqual(pad.clip_position(2.7, 2.0), 2.0)             # in the quiet after it
        self.assertAlmostEqual(pad.clip_position(1.6, 4.0, 2.0), 1.0)  # started at 2x: the lead-in is 0.6 s of mpv's clock


@unittest.skipUnless(_mpv(), "mpv is not installed")
class PaddingReachesTheDeviceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="auto-speech-edge-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.clip = self.dir / "clip.wav"
        self.raw = _write_tone(self.clip, 0.4)
        self.out = self.dir / "out.wav"

    def test_without_padding_the_stream_is_the_clip_and_nothing_else(self) -> None:
        """What was live until 2026-11-20: speech on the first sample sent and on the last."""
        self.assertEqual(_samples_sent(EdgePadding.none(), self.clip, self.out), self.raw)

    def test_the_whole_clip_then_half_a_second_of_quiet_is_sent(self) -> None:
        sent = _samples_sent(EdgePadding(lead_ms=0, tail_ms=500), self.clip, self.out)
        self.assertEqual(sent[: len(self.raw)], self.raw, "the clip must arrive whole and unchanged")
        after = sent[len(self.raw):]
        self.assertEqual(_ms(after), 500, f"{_ms(after):.0f} ms followed the clip, not 500")
        self.assertFalse(any(after), "what follows the clip must be silence")

    def test_quiet_first_then_the_whole_clip(self) -> None:
        sent = _samples_sent(EdgePadding(lead_ms=300, tail_ms=0), self.clip, self.out)
        lead = RATE * 300 // 1000 * 2
        self.assertFalse(any(sent[:lead]), "the first 300 ms sent must be silence")
        self.assertEqual(sent[lead:], self.raw, "then the clip, whole and unchanged, and nothing after it")

    def test_the_defaults_wrap_the_clip_on_both_sides(self) -> None:
        with mock.patch.dict(os.environ, NO_ENV):
            padding = EdgePadding.from_env()
        sent = _samples_sent(padding, self.clip, self.out)
        lead = RATE * DEFAULT_LEAD_IN_MS // 1000 * 2
        self.assertFalse(any(sent[:lead]))
        self.assertEqual(sent[lead: lead + len(self.raw)], self.raw)
        after = sent[lead + len(self.raw):]
        self.assertEqual(_ms(after), DEFAULT_TAIL_MS)
        self.assertFalse(any(after))

    def test_a_cold_start_sends_a_tone_not_silence_then_the_whole_clip(self) -> None:
        """His 15:24: "i didnt hear the word two in two things": 300 ms of digital
        silence did not wake the adapter. After quiet, 1.2 s that is not silence."""
        with mock.patch.dict(os.environ, NO_ENV):
            padding = EdgePadding.from_env()
        sent = _samples_sent(padding, self.clip, self.out, cold=True)
        lead = RATE * DEFAULT_WAKE_MS // 1000 * 2
        head = struct.unpack(f"<{lead // 2}h", sent[:lead])
        peak = max(abs(v) for v in head)
        self.assertTrue(900 <= peak <= 1100, f"the tone peaks at {peak} of 32768, not about 1036 (-30 dBFS)")
        hundredths = [max(abs(v) for v in head[i: i + RATE // 100]) for i in range(0, len(head), RATE // 100)]
        self.assertTrue(all(hundredths[1:-1]), "no 10 ms of the lead-in is digital silence (but its very first and last)")
        self.assertLess(abs(head[-1]), 40, "the tone has faded out where the clip starts: no click")
        crossings = sum(1 for a, b in zip(head, head[1:]) if (a < 0) != (b < 0) and (a or b))
        self.assertTrue(55 <= crossings <= 65, f"{crossings} zero crossings in 1.2 s is not a 25 Hz tone")
        self.assertEqual(sent[lead: lead + len(self.raw)], self.raw, "then the clip, whole and unchanged")
        after = sent[lead + len(self.raw):]
        self.assertEqual(_ms(after), DEFAULT_TAIL_MS)
        self.assertFalse(any(after), "what follows the clip is silence")


class ColdStartTest(unittest.TestCase):
    def test_nothing_played_yet_or_quiet_since_is_cold_back_to_back_is_warm(self) -> None:
        pad = EdgePadding()
        self.assertTrue(pad.is_cold(None))
        self.assertTrue(pad.is_cold(200.0))            # his 15:24 line followed 3 min 20 s of quiet
        self.assertTrue(pad.is_cold(WARM_SECONDS + 0.1))
        self.assertFalse(pad.is_cold(0.4))             # the next queued line

    def test_a_fresh_sink_asks_mpv_for_the_wake_tone(self) -> None:
        with mock.patch.dict(os.environ, NO_ENV):
            argv = NativeAudioSink(sound_yield=False).mpv_argv("/x/mpv", "a.wav")
        self.assertEqual(_filter_of(argv), [
            "--af=lavfi=[adelay=delays=1200:all=1[d];"
            "sine=f=25:d=1.2,volume=-12dB,afade=t=in:d=0.05,afade=t=out:st=1.15:d=0.05[w];"
            "[d][w]amix=inputs=2:duration=first:normalize=0,apad=pad_dur=0.5]"
        ])
        self.assertEqual(argv[-1], "a.wav")

    def test_the_sink_goes_warm_after_a_play_and_cold_again_after_quiet(self) -> None:
        proc = mock.MagicMock()
        proc.communicate.return_value = (b"", b"")
        proc.returncode = 0
        proc.poll.return_value = None
        tmp = Path(tempfile.mkdtemp(prefix="auto-speech-edge-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        clip = tmp / "clip.wav"
        _write_tone(clip, 0.1)
        with (
            mock.patch("shutil.which", return_value="/x/mpv"),
            mock.patch("subprocess.Popen", return_value=proc) as popen,
            mock.patch.dict(os.environ, {**NO_ENV, "AUTO_SPEECH_QOS_BOOST": "0"}),
        ):
            sink = NativeAudioSink(sound_yield=False)
            sink.stall_seconds = 0
            sink.play(clip)                                  # first line: cold
            sink.play(clip)                                  # straight after: warm
            sink._last_play_end -= WARM_SECONDS + 1          # then quiet
            sink.play(clip)                                  # cold again
        filters = [_filter_of(call.args[0])[0] for call in popen.call_args_list]
        self.assertIn("sine=f=25", filters[0])
        self.assertEqual(filters[1], "--af=lavfi=[adelay=delays=300:all=1,apad=pad_dur=0.5]")
        self.assertIn("sine=f=25", filters[2])

    def test_env_sets_the_wake_lead_in_and_zero_falls_back_to_the_short_one(self) -> None:
        with mock.patch.dict(os.environ, {**NO_ENV, WAKE_ENV: "2000", TAIL_ENV: "0"}):
            self.assertIn("adelay=delays=2000:all=1[d];sine=f=25:d=2,", NativeAudioSink(sound_yield=False).mpv_argv("/x/mpv", "a.wav")[-2])
        with mock.patch.dict(os.environ, {**NO_ENV, WAKE_ENV: "0"}):
            self.assertEqual(_filter_of(NativeAudioSink(sound_yield=False).mpv_argv("/x/mpv", "a.wav")),
                             ["--af=lavfi=[adelay=delays=300:all=1,apad=pad_dur=0.5]"])
        for raw, want in (("soon", DEFAULT_WAKE_MS), (" 800.0 ", 800), ("-5", 0), ("999999", 3000)):
            with mock.patch.dict(os.environ, {**NO_ENV, WAKE_ENV: raw}):
                self.assertEqual(EdgePadding.from_env().wake_ms, want, f"{WAKE_ENV}={raw!r}")

    def test_position_on_a_cold_start_is_still_the_clips_own(self) -> None:
        pad = EdgePadding()
        self.assertEqual(pad.clip_position(0.9, 2.0, cold=True), 0.0)          # still in the tone
        self.assertAlmostEqual(pad.clip_position(2.2, 2.0, cold=True), 1.0)
        self.assertAlmostEqual(pad.clip_position(1.3, 2.0, cold=False), 1.0)   # warm: the short lead-in
        self.assertAlmostEqual(pad.clip_position(3.4, 4.0, 2.0, cold=True), 1.0)   # started at 2x


if __name__ == "__main__":
    unittest.main()
