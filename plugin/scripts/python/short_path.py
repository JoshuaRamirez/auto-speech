"""ShortPathStrategy: decide and execute the one-shot generation path."""
from __future__ import annotations

import threading
from pathlib import Path

from audio_transcript import AudioTranscript
from duration_estimator import DurationEstimator
from mpv_controller import MpvController, MpvNotInstalledError, MpvStartupError
from resilient_synthesizer import ResilientSynthesizer
from tts_engine import TTSEngine, TTSNoSpeakableContentError
from voice_profile import VoiceProfile


class ShortPathStrategy:
    """Bypass chunking for transcripts whose estimated duration <= threshold."""

    def __init__(self, short_threshold_seconds: float) -> None:
        self._threshold = short_threshold_seconds

    def should_use(
        self, transcript: AudioTranscript, voice_profile: VoiceProfile
    ) -> bool:
        est = DurationEstimator.estimate_seconds(transcript.char_count, voice_profile)
        return est <= self._threshold

    def execute(
        self,
        transcript: AudioTranscript,
        voice_profile: VoiceProfile,
        tts_engine: TTSEngine,
        tmpdir: Path,
        stop_event: threading.Event,
    ) -> int:
        wav_path = tmpdir / "full.wav"
        # Recover from a content-dependent Kokoro generate fault by
        # retrying the offending span finer, rather than failing the run.
        produced = ResilientSynthesizer(tts_engine).synthesize_one(
            transcript.text, voice_profile, wav_path
        )
        if not produced:
            raise TTSNoSpeakableContentError(
                "no speakable audio recovered from transcript"
            )
        if stop_event.is_set():
            return 130
        try:
            MpvController().start(wav_path)
        except (MpvNotInstalledError, MpvStartupError) as exc:
            print(f"[short-path] mpv start failed: {exc}")
            return 6
        return 0
