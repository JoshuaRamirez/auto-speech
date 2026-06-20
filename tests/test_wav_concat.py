"""Unit test for WavConcatenator: frame-count sum + format parity."""
from __future__ import annotations

import sys
import tempfile
import wave
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from wav_concatenator import WavConcatError, WavConcatenator  # noqa: E402


def _write_sine_wav(path: Path, frames: int, rate: int = 24000) -> None:
    """Write a mono 16-bit WAV with `frames` constant-amplitude samples."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        # Just silence; the test is about frame counts and format, not audio.
        wf.writeframes(b"\x00\x00" * frames)


def _write_mismatch_wav(path: Path, frames: int) -> None:
    """Write a WAV with a DIFFERENT channel count to provoke a parity error."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)  # stereo - deliberately different
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(b"\x00\x00\x00\x00" * frames)


def test_frame_count_sum() -> None:
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        frames = [1200, 3400, 5600]
        sources = []
        for i, n in enumerate(frames):
            p = td_path / f"src-{i}.wav"
            _write_sine_wav(p, n)
            sources.append(p)
        dest = td_path / "concat.wav"
        WavConcatenator.concat(sources, dest)
        with wave.open(str(dest), "rb") as wf:
            total = wf.getnframes()
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 24000
        assert total == sum(frames), f"expected {sum(frames)}, got {total}"
        print(f"[test] frame-count sum OK: {total} == sum({frames})")


def test_format_mismatch_fails_loud() -> None:
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        good = td_path / "good.wav"
        bad = td_path / "bad.wav"
        _write_sine_wav(good, 1000)
        _write_mismatch_wav(bad, 1000)
        dest = td_path / "out.wav"
        raised = False
        try:
            WavConcatenator.concat([good, bad], dest)
        except WavConcatError as exc:
            raised = True
            print(f"[test] raised as expected: {exc}")
        assert raised, "expected WavConcatError on channel-count mismatch"
        assert not dest.exists(), "dest WAV should not exist after failed concat"
        partial = dest.with_suffix(dest.suffix + ".partial")
        assert not partial.exists(), "partial WAV should be cleaned up on failure"


def test_atomic_rename() -> None:
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        srcs = []
        for i in range(3):
            p = td_path / f"s-{i}.wav"
            _write_sine_wav(p, 500)
            srcs.append(p)
        dest = td_path / "atomic.wav"
        WavConcatenator.concat(srcs, dest)
        assert dest.exists()
        partial = dest.with_suffix(dest.suffix + ".partial")
        assert not partial.exists(), "no .partial should remain after success"
        print("[test] atomic rename OK")


def main() -> int:
    test_frame_count_sum()
    test_format_mismatch_fails_loud()
    test_atomic_rename()
    print("ok: wav_concatenator")
    return 0


if __name__ == "__main__":
    sys.exit(main())
