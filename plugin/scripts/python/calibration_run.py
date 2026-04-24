"""CalibrationRun: one historical measurement record."""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class CalibrationRun:
    """Immutable record of one calibration measurement.

    Kept for audit. Not read at runtime by other components — only
    VoiceProfile is. Append-only history.
    """

    timestamp: str
    voice_id: str
    speed: float
    reference_text_chars: int
    measured_duration_seconds: float
    computed_chars_per_second: float

    def to_dict(self) -> dict:
        return asdict(self)
