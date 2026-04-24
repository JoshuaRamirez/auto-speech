"""ChunkDescriptor: one planned chunk in a ChunkPlan."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChunkDescriptor:
    """A single planned chunk: index, Fibonacci target, actual slice, offsets."""

    index: int
    fib_value: int
    target_char_count: int
    actual_char_count: int
    text: str
    estimated_duration_seconds: float
    boundary_offset_start: int
    boundary_offset_end: int
