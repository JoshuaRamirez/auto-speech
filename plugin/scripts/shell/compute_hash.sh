#!/usr/bin/env bash
# auto-speech — compute the cache key for a source message.
#
# stdin:  the source text (raw assistant message, pre-rewrite).
# stdout: a single line, 64-hex-char SHA-256 of:
#           source_text || 0x00 || voice_id || ":" || speed
# where voice_id and speed come from config/voice_calibration.json.
#
# Rationale:
#   Cache key is (source, voice, speed). A voice change or speed change
#   produces a different key and correctly forces regeneration.
#   The rewrite step is deliberately excluded from the hash — Claude's
#   rewrite-text variability shouldn't cause cache misses.

set -euo pipefail

PLUGIN_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_SCRIPTS_DIR/../.." && pwd)"
CONFIG_JSON="$PROJECT_ROOT/config/voice_calibration.json"
VENV="$PROJECT_ROOT/.venv"

# shellcheck disable=SC1091
source "$VENV/bin/activate"

# Buffer stdin to a temp file. Using a heredoc for the Python block would
# steal python's stdin, so we pass the source text via an argv-referenced
# file instead.
SRC_TMP=$(mktemp -t auto-speech-hash-XXXXXX)
trap 'rm -f "$SRC_TMP"' EXIT
cat > "$SRC_TMP"

python - "$CONFIG_JSON" "$SRC_TMP" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
src_path = Path(sys.argv[2])
try:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    voice_id = str(data["voice_id"])
    speed = float(data["speed"])
except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
    voice_id = "af_heart"
    speed = 1.0

source_bytes = src_path.read_bytes()
key_input = source_bytes + b"\x00" + f"{voice_id}:{speed}".encode("utf-8")
print(hashlib.sha256(key_input).hexdigest())
PY
