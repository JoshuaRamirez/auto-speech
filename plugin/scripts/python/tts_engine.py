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


class TTSGenerationError(RuntimeError):
    """Raised when Kokoro synthesis fails. Non-recoverable; halt the pipeline."""


class TTSEngine:
    """Synthesize a WAV for a text string using Kokoro-82M via mlx-audio."""

    def __init__(self, model_id: str = "mlx-community/Kokoro-82M-bf16") -> None:
        self._model_id = model_id
        self._model = None  # lazy

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

        print(
            f"[tts_engine] synthesizing chars={len(text)} voice={voice_profile.voice_id} "
            f"speed={voice_profile.speed}"
        )
        try:
            chunks = []
            for result in self._model.generate(  # type: ignore[attr-defined]
                text=text,
                voice=voice_profile.voice_id,
                speed=voice_profile.speed,
                lang_code="a",
            ):
                chunks.append(np.array(result.audio))
        except Exception as exc:
            raise TTSGenerationError(f"kokoro generate failed: {exc}") from exc

        if not chunks:
            raise TTSGenerationError("kokoro emitted no audio chunks")

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
