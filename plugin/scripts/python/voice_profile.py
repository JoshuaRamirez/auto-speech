"""VoiceProfile: calibrated voice record."""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class VoiceProfile:
    """A calibrated voice at a specific speed.

    chars_per_second is the measured spoken throughput derived from a
    Calibrator run. Consumers use it to estimate duration and to size
    chunks in the Fibonacci planner.
    """

    voice_id: str
    speed: float
    chars_per_second: float
    calibrated_at: str  # ISO-8601 UTC, e.g. "2026-04-23T17:10:00Z"
    calibration_source_chars: int

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "VoiceProfile":
        return cls(
            voice_id=data["voice_id"],
            speed=float(data["speed"]),
            chars_per_second=float(data["chars_per_second"]),
            calibrated_at=data["calibrated_at"],
            calibration_source_chars=int(data["calibration_source_chars"]),
        )
