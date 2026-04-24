#!/usr/bin/env bash
# auto-speech — verify installer succeeded and Kokoro can synthesize.
# Writes /tmp/auto-speech-verify.wav and plays it.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$PROJECT_ROOT/.venv"
OUT="/tmp/auto-speech-verify.wav"

if [[ ! -f "$PROJECT_ROOT/setup/.installed" ]]; then
    echo "error: installer has not run. Run setup/install.sh first." >&2
    exit 1
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

python - <<'PY'
import numpy as np
import wave
from mlx_audio.tts.utils import load_model

SAMPLE_RATE = 24000
OUT = "/tmp/auto-speech-verify.wav"

print("[verify] loading Kokoro-82M-bf16 ...")
model = load_model("mlx-community/Kokoro-82M-bf16")

print("[verify] synthesizing ...")
chunks = []
for result in model.generate(
    text="Verification successful.",
    voice="af_heart",
    speed=1.0,
    lang_code="a",
):
    # result.audio is an mx.array; move to numpy
    arr = np.array(result.audio)
    chunks.append(arr)

audio = np.concatenate(chunks, axis=0).astype(np.float32)
# clip to int16 range
audio_i16 = np.clip(audio, -1.0, 1.0)
audio_i16 = (audio_i16 * 32767.0).astype(np.int16)

with wave.open(OUT, "wb") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(SAMPLE_RATE)
    wf.writeframes(audio_i16.tobytes())

print(f"[verify] wrote {OUT}  duration={len(audio) / SAMPLE_RATE:.2f}s")
PY

echo "[verify] playing $OUT ..."
afplay "$OUT"
echo "[verify] done."
