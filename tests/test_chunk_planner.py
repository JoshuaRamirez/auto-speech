"""Invariant tests for ChunkPlanner. Run directly; no pytest required."""
from __future__ import annotations

import random
import string
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from audio_transcript import AudioTranscript  # noqa: E402
from chunk_planner import ChunkPlanner  # noqa: E402
from voice_profile import VoiceProfile  # noqa: E402


def _profile() -> VoiceProfile:
    return VoiceProfile(
        voice_id="af_heart",
        speed=1.0,
        chars_per_second=15.84,
        calibrated_at="2026-04-23T00:00:00Z",
        calibration_source_chars=600,
    )


def _random_prose(rng: random.Random, min_chars: int, max_chars: int) -> str:
    n_sentences = rng.randint(3, 40)
    sentences = []
    for _ in range(n_sentences):
        words = []
        for _ in range(rng.randint(4, 18)):
            w_len = rng.randint(2, 9)
            words.append("".join(rng.choices(string.ascii_lowercase, k=w_len)))
        # Occasional clause break.
        if rng.random() < 0.4:
            idx = rng.randint(1, len(words) - 1)
            words[idx] = words[idx] + ","
        sentences.append(" ".join(words) + rng.choice([".", "?", "!"]))
    # Occasional paragraph break.
    para = []
    buf = []
    for s in sentences:
        buf.append(s)
        if rng.random() < 0.2:
            para.append(" ".join(buf))
            buf = []
    if buf:
        para.append(" ".join(buf))
    text = "\n\n".join(para)
    # Clamp to requested range.
    while len(text) < min_chars:
        text += " " + _random_prose(rng, min_chars // 2, max_chars)
    return text[:max_chars]


def _check_invariants(transcript_text: str, plan) -> None:
    reconstructed = "".join(d.text for d in plan.descriptors)
    assert reconstructed == transcript_text, (
        f"concat mismatch: {len(reconstructed)} vs {len(transcript_text)}"
    )
    for d in plan.descriptors:
        assert d.actual_char_count == len(d.text) > 0, f"empty chunk at index {d.index}"
    # Offsets increase monotonically and cover the whole text.
    prev_end = 0
    for d in plan.descriptors:
        assert d.boundary_offset_start == prev_end, (
            f"offset gap at index {d.index}: {d.boundary_offset_start} != {prev_end}"
        )
        assert d.boundary_offset_end > d.boundary_offset_start
        prev_end = d.boundary_offset_end
    assert prev_end == len(transcript_text), (
        f"final offset {prev_end} != length {len(transcript_text)}"
    )


def main() -> int:
    rng = random.Random(42)
    planner = ChunkPlanner()
    profile = _profile()
    trials = 30
    for i in range(trials):
        text = _random_prose(rng, 200, 4000)
        transcript = AudioTranscript(text=text)
        plan = planner.plan(transcript, profile)
        _check_invariants(text, plan)
        print(
            f"[trial {i + 1:2d}] chars={len(text):5d} "
            f"chunks={len(plan):2d} est={plan.total_estimated_duration_seconds:6.2f}s"
        )
    print(f"ok: {trials} trials, all invariants held")
    return 0


if __name__ == "__main__":
    sys.exit(main())
