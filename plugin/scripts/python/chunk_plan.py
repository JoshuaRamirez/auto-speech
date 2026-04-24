"""ChunkPlan: immutable ordered list of ChunkDescriptors."""
from __future__ import annotations

from dataclasses import dataclass

from chunk_descriptor import ChunkDescriptor


@dataclass(frozen=True)
class ChunkPlan:
    """Immutable scheduling aggregate."""

    descriptors: tuple[ChunkDescriptor, ...]
    total_estimated_duration_seconds: float
    total_char_count: int

    def __len__(self) -> int:
        return len(self.descriptors)

    def __iter__(self):
        return iter(self.descriptors)
