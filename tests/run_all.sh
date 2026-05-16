#!/usr/bin/env bash
# Run every test_*.py in this directory directly (no pytest dependency).
# Exits non-zero on the first failure. Order: cheap unit tests first,
# then the heavier producer/consumer/wav-concat tests last.

set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$TESTS_DIR/.." && pwd)"
VENV="$PROJECT_ROOT/.venv"

if [[ ! -x "$VENV/bin/python" ]]; then
    echo "error: venv missing at $VENV. Run setup/install.sh." >&2
    exit 1
fi

# Cheap unit tests first — failures here usually indicate a wider problem.
CHEAP=(
    test_narrator_phase_classifier.py
    test_narrator_summarizer.py
    test_narrator_config.py
    test_autoplay_config.py
)

# Heavier tests that touch the TTS pipeline or audio files.
HEAVY=(
    test_chunk_planner.py
    test_wav_concat.py
    test_segment_producer.py
    test_playback_consumer.py
)

ran=0
failed=0
fails=()

run_one() {
    local f="$1"
    local path="$TESTS_DIR/$f"
    if [[ ! -f "$path" ]]; then
        echo "skip $f (not found)"
        return
    fi
    echo
    echo "==== $f ===="
    if "$VENV/bin/python" "$path"; then
        ran=$((ran + 1))
    else
        ran=$((ran + 1))
        failed=$((failed + 1))
        fails+=("$f")
    fi
}

for f in "${CHEAP[@]}"; do run_one "$f"; done
for f in "${HEAVY[@]}"; do run_one "$f"; done

echo
echo "===================="
echo "ran:    $ran"
echo "failed: $failed"
if (( failed > 0 )); then
    printf '  - %s\n' "${fails[@]}"
    exit 1
fi
echo "all tests passed"
