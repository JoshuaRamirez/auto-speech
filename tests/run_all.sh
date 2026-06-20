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
    test_state_machine.py
    test_auto_speech_log.py
    test_playback_state.py
    test_narrator_state.py
    test_worker_lifecycle.py
    test_staleness_beacon.py
    test_playback_fifo.py
    test_dedup_guard.py
    test_autoplay_gate.py
    test_autoplay_scope.py
    test_autoplay_worker.py
    test_job_tracker.py
    test_narrator_phase_classifier.py
    test_narrator_summarizer.py
    test_narrator_config.py
    test_autoplay_config.py
    test_message_selector.py
    test_transcript_locator.py
    test_fibonacci.py
    test_cli_rewrite.py
    test_mpv_wait.py
    test_mlx_summarizer.py
    test_narrator_service.py
)

# Bash-script tests for shell helpers.
SHELL_TESTS=(
    test_autoplay_shim.sh
    test_narrator_hook.sh
    test_install_idempotent.sh
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

run_one_py() {
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

run_one_sh() {
    local f="$1"
    local path="$TESTS_DIR/$f"
    if [[ ! -x "$path" ]]; then
        echo "skip $f (not found or not executable)"
        return
    fi
    echo
    echo "==== $f ===="
    if bash "$path"; then
        ran=$((ran + 1))
    else
        ran=$((ran + 1))
        failed=$((failed + 1))
        fails+=("$f")
    fi
}

for f in "${CHEAP[@]}"; do run_one_py "$f"; done
for f in "${SHELL_TESTS[@]}"; do run_one_sh "$f"; done
for f in "${HEAVY[@]}"; do run_one_py "$f"; done

echo
echo "===================="
echo "ran:    $ran"
echo "failed: $failed"
if (( failed > 0 )); then
    printf '  - %s\n' "${fails[@]}"
    exit 1
fi
echo "all tests passed"
