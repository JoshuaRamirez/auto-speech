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

# Isolation: every test process gets its own runtime state directory, so no
# test can reach the LIVE daemon's socket (/tmp/auto-speech-daemon.sock),
# its pid file, its log, the mpv pid file or the autoplay markers. Code
# resolves all of those through AUTO_SPEECH_TMP_ROOT (state_paths.py);
# a daemon a test auto-starts lands in here too and is stopped at exit.
STATE_DIR="$(mktemp -d /tmp/auto-speech-tests.XXXXXX)"
export AUTO_SPEECH_TMP_ROOT="$STATE_DIR"
export AUTO_SPEECH_DAEMON_SOCK="$STATE_DIR/auto-speech-daemon.sock"
export AUTO_SPEECH_MISSED_FILE="$STATE_DIR/missed.json"
export AUTO_SPEECH_MEETING_FILE="$STATE_DIR/meeting.json"
export AUTO_SPEECH_HISTORY_DIR="$STATE_DIR/history"
export AUTO_SPEECH_HISTORY_CONFIG="$STATE_DIR/history.toml"
# A daemon a test starts never goes through launchd, so it never takes the
# live narrator's label (2026-10-20 20:02: a niced run replaced his narrator).
export AUTO_SPEECH_START_VIA_LAUNCHD=never
echo "[isolation] AUTO_SPEECH_TMP_ROOT=$STATE_DIR"
echo "[isolation] AUTO_SPEECH_DAEMON_SOCK=$AUTO_SPEECH_DAEMON_SOCK"
echo "[isolation] AUTO_SPEECH_MISSED_FILE=$AUTO_SPEECH_MISSED_FILE"
echo "[isolation] AUTO_SPEECH_MEETING_FILE=$AUTO_SPEECH_MEETING_FILE"
echo "[isolation] AUTO_SPEECH_HISTORY_DIR=$AUTO_SPEECH_HISTORY_DIR"
echo "[isolation] AUTO_SPEECH_HISTORY_CONFIG=$AUTO_SPEECH_HISTORY_CONFIG"
echo "[isolation] AUTO_SPEECH_START_VIA_LAUNCHD=never"
# No test starts the real UI Utility: a silent copy a test started would stay
# as his copy (its contract: "A copy already running keeps its mode"). The
# player launcher talks to a stand-in on this private socket, and never runs
# a launch command (ADR-021).
export UI_UTILITY_SOCK="$STATE_DIR/ui-utility.sock"
export UI_UTILITY_SILENT=1
export AUTO_SPEECH_PLAYER_LAUNCH=never
echo "[isolation] UI_UTILITY_SOCK=$UI_UTILITY_SOCK"
echo "[isolation] UI_UTILITY_SILENT=1"
echo "[isolation] AUTO_SPEECH_PLAYER_LAUNCH=never"
# No test plays sound (his 2026-10-20 10:40 "turn off the speech tests"): mpv goes to
# its null output unless AUTO_SPEECH_TEST_REAL_AUDIO=1 is set on purpose. Each test
# that can reach mpv also sets this itself (tests/quiet_audio.py), so a bare run is silent too.
if [[ "${AUTO_SPEECH_TEST_REAL_AUDIO:-}" == "1" ]]; then
    echo "[audio] REAL audio on: AUTO_SPEECH_TEST_REAL_AUDIO=1 (tests may play sound)"
else
    export AUTO_SPEECH_MPV_AO=null
    echo "[audio] silent: AUTO_SPEECH_MPV_AO=null (set AUTO_SPEECH_TEST_REAL_AUDIO=1 to hear)"
fi
cleanup_state_dir() {
    local pid
    pid="$(cat "$STATE_DIR/auto-speech-narrator-daemon.pid" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && ps -o command= -p "$pid" 2>/dev/null | grep -q narrator_service.py; then
        kill "$pid" 2>/dev/null || true
    fi
    rm -rf "$STATE_DIR"
}
trap cleanup_state_dir EXIT

# The caller's Claude Code session must not leak into the tests: mcp_server
# falls back to CLAUDE_CODE_SESSION_ID / CLAUDE_SESSION_ID as the session
# when a call names none, which changes what session_configure does.
unset CLAUDE_CODE_SESSION_ID CLAUDE_SESSION_ID
echo "[isolation] CLAUDE_CODE_SESSION_ID and CLAUDE_SESSION_ID cleared"

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
    test_resilient_synthesizer.py
    test_native_audio_sink_stress.py
    test_state_paths.py  # imports http_routing, which needs flask: run with the web tests
)

# Cheap unit tests first — failures here usually indicate a wider problem.
CHEAP=(
    test_narrator_start_label.py
    test_state_machine.py
    test_auto_speech_log.py
    test_doctor.py
    test_playback_cannot_hang.py
    test_quiet_tests.py
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
    test_autoplay_enrollment.py
    test_leftover_optout_docs.py
    test_autoplay_worker.py
    test_say_worker.py
    test_replay_control.py
    test_mcp_server.py
    test_job_tracker.py
    test_narrator_phase_classifier.py
    test_narrator_summarizer.py
    test_narrator_config.py
    test_autoplay_config.py
    test_message_selector.py
    test_transcript_locator.py
    test_fibonacci.py
    test_cli_rewrite.py
    test_mlx_summarizer.py
    test_narrator_service.py
    test_queue_robustness.py
    test_daemon_client.py
    test_apple_say_engine.py
    test_native_audio_sink.py
    test_mpv_ipc.py
    test_sound_yield.py
    test_missed_speech.py
    test_speaker.py
    test_speech_history.py
    test_speech_control.py
    test_speech_settings.py
    test_stop_and_drain.py
    test_line_id.py
    test_play_from.py
    test_stop_is_playback.py
    test_control_round_1030.py
    test_ui_utility_client.py
    test_speak_client.py
    test_cli_dispatcher.py
    test_ipc_command_key.py
    test_say_hang.py
)

# Bash-script tests for shell helpers.
SHELL_TESTS=(
    test_autoplay_shim.sh
    test_narrator_hook.sh
    test_narrator_service_status.sh
    test_compute_hash.sh
    test_install_idempotent.sh
    test_install_plugin.sh
    test_bootstrap_hook_idempotent.sh
    test_deps_locked.sh
)

# Web-server tests: need Flask + numpy but stub MLX. Run in full mode and
# in the dedicated --web CI lane (a light venv with just flask + numpy).
WEB=(
    test_synthesize_endpoint.py
    test_state_paths.py
)

# Heavier tests that touch the TTS pipeline or audio files.
HEAVY=(
    test_chunk_planner.py
    test_wav_concat.py
    test_resilient_synthesizer.py
    test_segment_producer.py
    test_playback_consumer.py
    # ADR-019: the stall watch, the budgets and the retry, plus one short load test
    # (nice 19 burners, the real say and mpv at --ao=null: silent). Never in the full
    # list until 2026-10-21; it ran only under --hermetic.
    test_speech_under_load.py
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
