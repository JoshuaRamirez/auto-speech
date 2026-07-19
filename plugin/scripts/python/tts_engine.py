"""TTSEngine: Kokoro-via-mlx-audio adapter.

Final interface locked in here during Phase 1 so Phase 5 only has to add
the SegmentProducer around it. Lazy-loads the model on first synthesis
and reuses it for the life of the process.

Atomicity: writes to <out_path>.partial and renames on success, so
PlaybackQueue consumers never see a half-written WAV.
"""
from __future__ import annotations

import os
import wave
from pathlib import Path

import numpy as np

from voice_profile import VoiceProfile


KOKORO_SAMPLE_RATE = 24000

# Kokoro language codes keyed by the voice-id prefix letter.
_KOKORO_LANG_CODES = frozenset("abefhijpz")


def _lang_code_for_voice(voice_id: str) -> str:
    """Return the Kokoro lang_code for a voice id (its first letter), or 'a'."""
    if voice_id and voice_id[0] in _KOKORO_LANG_CODES:
        return voice_id[0]
    return "a"


class TTSGenerationError(RuntimeError):
    """Raised when Kokoro synthesis fails. Non-recoverable; halt the pipeline."""


class TTSNoSpeakableContentError(TTSGenerationError):
    """Raised when the input has no pronounceable content.

    Kokoro/misaki reduce the text to zero phonemes — e.g. a selection made
    up entirely of symbols ("• • •", "★", "#", "x² + y² = z²"). This is a
    property of the *input*, not a synthesis fault, so callers may treat it
    as "nothing to say" rather than an error.
    """


class TTSEngine:
    """Synthesize a WAV for a text string using Kokoro-82M via mlx-audio."""

    def __init__(self, model_id: str = "mlx-community/Kokoro-82M-bf16") -> None:
        self._model_id = model_id
        self._model = None  # lazy

    @property
    def model_id(self) -> str:
        return self._model_id

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        # Local import so module import cost is paid only when used.
        from mlx_audio.tts.utils import load_model  # type: ignore
        print(f"[tts_engine] loading {self._model_id} ...")
        self._model = load_model(self._model_id)
        print("[tts_engine] model loaded")

    def synthesize(
        self,
        text: str,
        voice_profile: VoiceProfile,
        out_path: Path,
    ) -> None:
        """Write a WAV containing the spoken form of `text` to `out_path`."""
        if not text or not text.strip():
            raise TTSGenerationError("empty text passed to synthesize")

        self._ensure_loaded()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_path.with_suffix(out_path.suffix + ".partial")

        # Kokoro's lang_code is the voice-id's first letter (a=American,
        # b=British English, e/f/h/i/j/p/z = other languages). Passing the
        # wrong code loads the voice into a mismatched G2P pipeline, which
        # emits garbage or trips a broadcast-shape crash. Default to "a".
        lang_code = _lang_code_for_voice(voice_profile.voice_id)

        print(
            f"[tts_engine] synthesizing chars={len(text)} voice={voice_profile.voice_id} "
            f"speed={voice_profile.speed} lang={lang_code}"
        )
        try:
            chunks = []
            for result in self._model.generate(  # type: ignore[attr-defined]
                text=text,
                voice=voice_profile.voice_id,
                speed=voice_profile.speed,
                lang_code=lang_code,
            ):
                chunks.append(np.array(result.audio))
        except Exception as exc:
            raise TTSGenerationError(f"kokoro generate failed: {exc}") from exc

        if not chunks:
            raise TTSNoSpeakableContentError(
                "kokoro emitted no audio chunks (no pronounceable content)"
            )

        audio = np.concatenate(chunks, axis=0).astype(np.float32)
        audio = np.clip(audio, -1.0, 1.0)
        audio_i16 = (audio * 32767.0).astype(np.int16)

        with wave.open(str(tmp), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(KOKORO_SAMPLE_RATE)
            wf.writeframes(audio_i16.tobytes())

        os.replace(tmp, out_path)
        print(f"[tts_engine] wrote {out_path}")
