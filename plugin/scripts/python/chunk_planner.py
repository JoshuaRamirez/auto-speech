"""ChunkPlanner: decompose an AudioTranscript into a ChunkPlan."""
from __future__ import annotations

from audio_transcript import AudioTranscript
from boundary_snapper import BoundarySnapper
from chunk_descriptor import ChunkDescriptor
from chunk_plan import ChunkPlan
from duration_estimator import DurationEstimator
from fibonacci import FibonacciSeq
from voice_profile import VoiceProfile


DEFAULT_BASE_DURATION_SECONDS = 4.0
DEFAULT_TOLERANCE = 0.25


class ChunkPlanner:
    """Build a Fibonacci-sized ChunkPlan for a transcript."""

    def plan(
        self,
        transcript: AudioTranscript,
        voice_profile: VoiceProfile,
        base_duration_seconds: float = DEFAULT_BASE_DURATION_SECONDS,
        tolerance: float = DEFAULT_TOLERANCE,
    ) -> ChunkPlan:
        text = transcript.text
        if not text:
            raise ValueError("transcript text is empty")
        cps = voice_profile.chars_per_second
        if cps <= 0:
            raise ValueError("voice_profile.chars_per_second must be > 0")

        fib = FibonacciSeq()
        descriptors: list[ChunkDescriptor] = []
        offset = 0
        total_len = len(text)

        while offset < total_len:
            fib_value = fib.next_target()
            target_chars = max(1, int(fib_value * base_duration_seconds * cps))

            end = BoundarySnapper.snap(text, offset, target_chars, tolerance)
            if end <= offset:
                # Last-resort safety: advance by one character to guarantee progress.
                end = min(offset + 1, total_len)
            slice_text = text[offset:end]
            actual = len(slice_text)
            descriptors.append(
                ChunkDescriptor(
                    index=len(descriptors) + 1,
                    fib_value=fib_value,
                    target_char_count=target_chars,
                    actual_char_count=actual,
                    text=slice_text,
                    estimated_duration_seconds=DurationEstimator.estimate_seconds(
                        actual, voice_profile
                    ),
                    boundary_offset_start=offset,
                    boundary_offset_end=end,
                )
            )
            offset = end

        total_seconds = DurationEstimator.estimate_seconds(total_len, voice_profile)
        return ChunkPlan(
            descriptors=tuple(descriptors),
            total_estimated_duration_seconds=total_seconds,
            total_char_count=total_len,
        )
