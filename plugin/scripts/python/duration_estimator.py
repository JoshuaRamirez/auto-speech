"""DurationEstimator: estimate spoken duration from char count."""
from __future__ import annotations

from voice_profile import VoiceProfile


class DurationEstimator:
    """Convert character count to estimated seconds via VoiceProfile.chars_per_second."""

    @staticmethod
    def estimate_seconds(char_count: int, voice_profile: VoiceProfile) -> float:
        if voice_profile.chars_per_second <= 0:
            raise ValueError("VoiceProfile.chars_per_second must be > 0")
        return char_count / voice_profile.chars_per_second
