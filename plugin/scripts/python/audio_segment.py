"""AudioSegment: a generated WAV corresponding to one ChunkDescriptor."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from chunk_descriptor import ChunkDescriptor


@dataclass(frozen=True)
class AudioSegment:
    """A fully materialized chunk: WAV on disk + measured duration."""

    descriptor: ChunkDescriptor
    wav_path: Path
    actual_duration_seconds: float
    generation_elapsed_seconds: float
