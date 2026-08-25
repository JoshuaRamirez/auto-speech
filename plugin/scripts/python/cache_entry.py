"""CacheEntry: one persisted cache entry's metadata."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CacheEntry:
    """Metadata for a single cached run, mirrored in meta.json on disk."""

    source_hash: str  # full 64-hex-char SHA-256
    voice_id: str
    speed: float
    char_count: int
    duration_seconds: float
    created_at: str  # ISO-8601 UTC, seconds precision, trailing "Z"
    chars_per_second_at_creation: float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> CacheEntry:
        return cls(
            source_hash=str(data["source_hash"]),
            voice_id=str(data["voice_id"]),
            speed=float(data["speed"]),
            char_count=int(data["char_count"]),
            duration_seconds=float(data["duration_seconds"]),
            created_at=str(data["created_at"]),
            chars_per_second_at_creation=float(data["chars_per_second_at_creation"]),
        )
