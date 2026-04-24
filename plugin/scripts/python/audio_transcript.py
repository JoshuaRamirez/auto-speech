"""AudioTranscript: the audio-friendly rewritten text."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AudioTranscript:
    """Plain-text, TTS-ready transcript."""

    text: str

    @property
    def char_count(self) -> int:
        return len(self.text)
