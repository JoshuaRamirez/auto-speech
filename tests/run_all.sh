#!/usr/bin/env bash
# Run the test suite directly (no pytest dependency).
#
# Modes:
#   bash tests/run_all.sh             full suite (python + shell + web + heavy)
#   bash tests/run_all.sh --hermetic  only tests that need NO runtime deps
#                                     — the CI subset (runs on a bare 3.12)
#   bash tests/run_all.sh --web       only the web-server tests — the CI web
#                                     lane (needs Flask + numpy, NOT MLX)
#
# Interpreter: defaults to .venv; override with AUTO_SPEECH_TEST_PYTHON so
# CI can run the hermetic subset on a bare interpreter (the darwin-scoped
# uv.lock can't `uv sync` on a Linux runner, and the hermetic tests import
# only the standard library + plugin modules anyway).

set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$TESTS_DIR/.." && pwd)"
VENV="$PROJECT_ROOT/.venv"

HERMETIC=0
WEB_ONLY=0
if [[ "${1:-}" == "--hermetic" ]]; then
    HERMETIC=1
    shift
elif [[ "${1:-}" == "--web" ]]; then
    WEB_ONLY=1
    shift
fi

PYTHON="${AUTO_SPEECH_TEST_PYTHON:-$VENV/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
    echo "error: python interpreter not found at $PYTHON" >&2
    echo "       set AUTO_SPEECH_TEST_PYTHON, or run setup/install.sh." >&2
    exit 1
fi

# Tests that import runtime deps (numpy / the TTS audio pipeline) and so
# CANNOT run on a bare interpreter. Classified empirically; every other
# test_*.py imports only the standard library + plugin modules.
NEEDS_DEPS=(
    test_segment_producer.py
    test_playback_consumer.py
    test_synthesize_endpoint.py
)

# Cheap unit tests first — failures here usually indicate a wider problem.
CHEAP=(
    test_state_machine.py
    test_auto_speech_log.py
    test_doctor.py
    test_config_validation.py
    test_self_update.py
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
    test_bootstrap_hook_idempotent.sh
    test_deps_locked.sh
)

# Web-server tests: need Flask + numpy but stub MLX. Run in full mode and
# in the dedicated --web CI lane (a light venv with just flask + numpy).
WEB=(
    test_synthesize_endpoint.py
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
    if "$PYTHON" "$path"; then
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

if [[ $HERMETIC -eq 1 ]]; then
    # Every python test except the dep-requiring ones. New tests are picked
    # up automatically; add a new dep-requiring test to NEEDS_DEPS above.
    echo "[hermetic] interpreter: $PYTHON"
    for path in "$TESTS_DIR"/test_*.py; do
        f="$(basename "$path")"
        skip=0
        for d in "${NEEDS_DEPS[@]}"; do [[ "$f" == "$d" ]] && skip=1; done
        if [[ $skip -eq 1 ]]; then
            echo "skip $f (needs runtime deps)"
            continue
        fi
        run_one_py "$f"
    done
elif [[ $WEB_ONLY -eq 1 ]]; then
    echo "[web] interpreter: $PYTHON"
    for f in "${WEB[@]}"; do run_one_py "$f"; done
else
    for f in "${CHEAP[@]}"; do run_one_py "$f"; done
    for f in "${SHELL_TESTS[@]}"; do run_one_sh "$f"; done
    for f in "${WEB[@]}"; do run_one_py "$f"; done
    for f in "${HEAVY[@]}"; do run_one_py "$f"; done
fi

echo
echo "===================="
echo "ran:    $ran"
echo "failed: $failed"
if (( failed > 0 )); then
    printf '  - %s\n' "${fails[@]}"
    exit 1
fi
echo "all tests passed"
