"""Unit tests for SpanSplitter + ResilientSynthesizer.

Regression context: mlx-audio's Kokoro `generate` raises on certain inputs
(content-dependent, not length-dependent). That fault used to abort the
whole CLI/autoplay run — /auto-speech-speak exited 5 with no audio at all,
because SegmentProducer and ShortPathStrategy called TTSEngine.synthesize()
directly and let the error propagate. Recovery existed only inside
web_server.py. These tests pin the shared behavior: a poisoned phrase is
isolated and dropped, everything else is still spoken.

A fake engine stands in for Kokoro: it "fails" on any span containing the
poison token and writes a real (tiny) WAV otherwise.
"""
from __future__ import annotations

import sys
import tempfile
import wave
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from resilient_synthesizer import ResilientSynthesizer
from span_splitter import SpanSplitter
from tts_engine import TTSGenerationError, TTSNoSpeakableContentError
from voice_profile import VoiceProfile

POISON = "zzpoisonzz"


def _profile() -> VoiceProfile:
    return VoiceProfile(
        voice_id="af_heart",
        speed=1.0,
        chars_per_second=15.0,
        calibrated_at="test",
        calibration_source_chars=0,
    )


class _FakeEngine:
    """Writes a real WAV, except for spans holding the poison token."""

    def __init__(self, unspeakable: str | None = None) -> None:
        self.calls: list[str] = []
        self._unspeakable = unspeakable

    def synthesize(self, text: str, profile, out_path: Path) -> None:
        self.calls.append(text)
        if self._unspeakable is not None and self._unspeakable in text:
            raise TTSNoSpeakableContentError("nothing pronounceable")
        if POISON in text:
            raise TTSGenerationError("kokoro generate failed: broadcast shapes")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(b"\x00\x00" * max(1, len(text)))


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="auto-speech-resilient-test-"))


def test_splitter_progression() -> None:
    s = SpanSplitter()
    assert s.split("One. Two. Three.") == ["One.", "Two.", "Three."]
    assert s.split("alpha, beta; gamma") == ["alpha", "beta", "gamma"]
    assert s.split("four three two one") == ["four three", "two one"]
    assert s.split("single") == ["single"]  # floor


def test_clean_span_is_one_call_one_file() -> None:
    eng = _FakeEngine()
    out = _tmp() / "span.wav"
    parts = ResilientSynthesizer(eng, log=lambda _m: None).synthesize_parts(
        "A clean sentence.", _profile(), out
    )
    assert parts == [out]
    assert len(eng.calls) == 1


def test_poisoned_phrase_is_dropped_rest_survives() -> None:
    """The whole point: one bad phrase must not silence the message."""
    eng = _FakeEngine()
    out = _tmp() / "span.wav"
    text = f"Good first sentence. Bad {POISON} sentence. Good last sentence."
    synth = ResilientSynthesizer(eng, log=lambda _m: None)
    parts = synth.synthesize_parts(text, _profile(), out)

    assert len(parts) >= 2, "surviving sentences must still be synthesized"
    assert all(p.is_file() for p in parts)
    joined = " ".join(
        c for c in eng.calls if POISON not in c and c in text
    )
    assert "Good first sentence." in eng.calls
    assert "Good last sentence." in eng.calls
    assert joined  # non-empty: real audio was produced around the fault


def test_synthesize_one_collapses_to_single_file() -> None:
    eng = _FakeEngine()
    d = _tmp()
    out = d / "chunk-001.wav"
    text = f"Alpha sentence. Beta {POISON} sentence. Gamma sentence."
    ok = ResilientSynthesizer(eng, log=lambda _m: None).synthesize_one(
        text, _profile(), out
    )
    assert ok is True
    assert out.is_file(), "caller's single-file contract must hold"
    with wave.open(str(out), "rb") as wf:
        assert wf.getnframes() > 0
    leftovers = [p for p in d.iterdir() if p != out]
    assert leftovers == [], f"recovery fragments not cleaned up: {leftovers}"


def test_wholly_unspeakable_returns_false_without_raising() -> None:
    eng = _FakeEngine(unspeakable="")  # every span is unspeakable
    out = _tmp() / "span.wav"
    ok = ResilientSynthesizer(eng, log=lambda _m: None).synthesize_one(
        "• • •", _profile(), out
    )
    assert ok is False
    assert not out.exists()


def test_single_word_fault_stops_at_the_floor() -> None:
    """No infinite recursion when the smallest unit still faults."""
    eng = _FakeEngine()
    out = _tmp() / "span.wav"
    parts = ResilientSynthesizer(eng, log=lambda _m: None).synthesize_parts(
        POISON, _profile(), out
    )
    assert parts == []
    assert len(eng.calls) == 1, "must not retry a span it cannot split"


def main() -> int:
    tests = [
        test_splitter_progression,
        test_clean_span_is_one_call_one_file,
        test_poisoned_phrase_is_dropped_rest_survives,
        test_synthesize_one_collapses_to_single_file,
        test_wholly_unspeakable_returns_false_without_raising,
        test_single_word_fault_stops_at_the_floor,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"resilient_synthesizer: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
