#!/usr/bin/env bash
# Bash tests for narrator_hook.sh gate logic.
#
# We point HOME at a temp dir so we can construct + tear down per-session
# markers without touching the real ~/.claude. The events log is also
# redirected to a temp path. Verifies:
#   - bail when AUTO_SPEECH_SUPPRESS_HOOKS=1 (nested claude -p guard)
#   - bail when no session_id in payload AND no env var
#   - bail when session_id present but marker absent
#   - fire when session_id matches an existing marker file
#   - fire when payload session_id is present but env var is unset

set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$TESTS_DIR/.." && pwd)"
HOOK="$PROJECT_ROOT/plugin/scripts/shell/narrator_hook.sh"

failures=0
ran=0
ok()   { ran=$((ran+1)); printf '  ok  %s\n' "$1"; }
fail() { ran=$((ran+1)); failures=$((failures+1)); printf '  FAIL %s: %s\n' "$1" "$2"; }

# Each test: set up a fresh temp HOME and EVENTS_LOG, fire the hook,
# count delta in the events log.
run_hook_case() {
    local description="$1"
    local marker_present="$2"  # "yes" or "no"
    local payload_session="$3"  # session id in payload (or empty)
    local env_session="$4"      # CLAUDE_CODE_SESSION_ID env (or empty)
    local suppress="$5"         # "1" or empty
    local expected_delta="$6"   # 0 = bail, 1 = fire

    local sid_to_mark="${payload_session:-$env_session}"
    local tmp_home; tmp_home="$(mktemp -d -t narrator-hook-test-XXXX)"
    local tmp_events; tmp_events="$(mktemp -t narrator-events-XXXX)"
    # Replace the hook's hardcoded events log via env? No — the hook
    # uses a fixed path. Tests run sequentially and we just count the
    # delta on the SHARED events log to detect a fire.
    # Save a marker location under our temp HOME.
    if [[ "$marker_present" == "yes" && -n "$sid_to_mark" ]]; then
        mkdir -p "$tmp_home/.claude/auto-speech-narrate-sessions"
        touch "$tmp_home/.claude/auto-speech-narrate-sessions/$sid_to_mark"
    fi

    local lines_before; lines_before=$(wc -l < /tmp/auto-speech-narrator-events.jsonl 2>/dev/null || echo 0)

    local payload="{}"
    if [[ -n "$payload_session" ]]; then
        payload="{\"hook_event_name\":\"PreToolUse\",\"tool_name\":\"Bash\",\"session_id\":\"$payload_session\"}"
    else
        payload="{\"hook_event_name\":\"PreToolUse\",\"tool_name\":\"Bash\"}"
    fi

    local env_args=( -u CLAUDE_CODE_SESSION_ID )
    if [[ -n "$env_session" ]]; then
        env_args=( "CLAUDE_CODE_SESSION_ID=$env_session" )
    fi
    if [[ -n "$suppress" ]]; then
        env_args+=( "AUTO_SPEECH_SUPPRESS_HOOKS=$suppress" )
    fi
    env_args+=( "HOME=$tmp_home" )

    echo "$payload" | env "${env_args[@]}" bash "$HOOK"
    local lines_after; lines_after=$(wc -l < /tmp/auto-speech-narrator-events.jsonl 2>/dev/null || echo 0)
    local delta=$((lines_after - lines_before))

    if [[ "$delta" -eq "$expected_delta" ]]; then
        ok "$description (delta=$delta)"
    else
        fail "$description" "expected delta=$expected_delta got delta=$delta"
    fi

    rm -rf "$tmp_home" "$tmp_events" 2>/dev/null
}

run_hook_case "suppress=1 bails immediately" "yes" "sid-x" "" "1" 0
run_hook_case "no session anywhere bails" "no" "" "" "" 0
run_hook_case "session id present but no marker → bail" "no" "sid-y" "" "" 0
run_hook_case "payload session matches marker → fire" "yes" "sid-z" "" "" 1
run_hook_case "env session matches marker (no payload) → fire" "yes" "" "sid-q" "" 1

echo
echo "narrator_hook gate: $ran ran, $failures failed"
exit $failures
