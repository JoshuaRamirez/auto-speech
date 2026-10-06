#!/usr/bin/env bash
# Tests for compute_hash.sh cache key generation and fallback voice.

set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$TESTS_DIR/.." && pwd)"
COMPUTE_HASH="$PROJECT_ROOT/plugin/scripts/shell/compute_hash.sh"

failures=0
ran=0
ok()   { ran=$((ran+1)); printf '  ok  %s\n' "$1"; }
fail() { ran=$((ran+1)); failures=$((failures+1)); printf '  FAIL %s: %s\n' "$1" "$2"; }

# Anchor checks
grep -q 'voice_id = "system"' "$COMPUTE_HASH" || fail "anchor:fallback-voice-system" "compute_hash.sh does not fall back to 'system' voice"
grep -q 'voice_id = "af_heart"' "$COMPUTE_HASH" && fail "anchor:no-af-heart" "compute_hash.sh still references legacy 'af_heart' voice"
ok "anchor:fallback-voice-is-system"

# 1. Output format: 64 hex characters
out=$(echo -n "Basic test string" | bash "$COMPUTE_HASH")
if [[ "$out" =~ ^[0-9a-f]{64}$ ]]; then
    ok "output-is-64-char-hex"
else
    fail "output-is-64-char-hex" "got invalid hash output: $out"
fi

# 2. Correct SHA256 hash matching reference Python implementation
test_src="The quick brown fox jumps over the lazy dog."
actual=$(echo -n "$test_src" | bash "$COMPUTE_HASH")

# Resolve expected config from project root config/voice_calibration.json
CONFIG="$PROJECT_ROOT/config/voice_calibration.json"
expected=$(python3 - "$CONFIG" "$test_src" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
src = sys.argv[2].encode("utf-8")
try:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    voice_id = str(data["voice_id"])
    speed = float(data["speed"])
except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
    voice_id = "system"
    speed = 1.0

key = src + b"\x00" + f"{voice_id}:{speed}".encode("utf-8")
print(hashlib.sha256(key).hexdigest())
PY
)

if [[ "$actual" == "$expected" ]]; then
    ok "hash-matches-expected-calibration-key"
else
    fail "hash-matches-expected-calibration-key" "expected $expected, got $actual"
fi

# 3. Multiline text preservation
multi_src=$'First line of response\nSecond line with code: ls -la\nThird line.'
multi_actual=$(echo -n "$multi_src" | bash "$COMPUTE_HASH")
if [[ "$multi_actual" =~ ^[0-9a-f]{64}$ && "$multi_actual" != "$actual" ]]; then
    ok "multiline-input-hashed-distinctly"
else
    fail "multiline-input-hashed-distinctly" "multiline hashing failed or collided"
fi

echo
echo "compute_hash: $ran ran, $failures failed"
exit "$failures"
