"""WavInspector: read WAV duration via stdlib."""
from __future__ import annotations

import wave
from pathlib import Path


class WavInspector:
    """Report the exact duration of a PCM WAV file."""

    @staticmethod
    def duration_seconds(path: Path) -> float:
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
        if rate <= 0:
            raise ValueError(f"WAV at {path} has zero sample rate")
        return frames / float(rate)
