#!/usr/bin/env bash
# Tests for narrator_service_status.sh daemon PID validation and status output.

set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$TESTS_DIR/.." && pwd)"
STATUS_SCRIPT="$PROJECT_ROOT/plugin/scripts/shell/narrator_service_status.sh"

failures=0
ran=0
ok()   { ran=$((ran+1)); printf '  ok  %s\n' "$1"; }
fail() { ran=$((ran+1)); failures=$((failures+1)); printf '  FAIL %s: %s\n' "$1" "$2"; }

# Anchor checks
grep -q 'narrator_pid_is_ours "\$PID"' "$STATUS_SCRIPT" || fail "anchor:uses-narrator-pid-is-ours" "narrator_service_status.sh does not use narrator_pid_is_ours on PID"
grep -q 'kill -0 "\$PID"' "$STATUS_SCRIPT" && fail "anchor:bare-kill-zero" "narrator_service_status.sh still uses bare kill -0 on PID"
ok "anchor:narrator-service-status-pid-guard"

# Preserve any active PID file
PID_FILE="${AUTO_SPEECH_TMP_ROOT:-/tmp}/auto-speech-narrator-daemon.pid"
ORIG_PID=""
if [[ -f "$PID_FILE" ]]; then
    ORIG_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
fi

cleanup() {
    rm -f "$PID_FILE" 2>/dev/null || true
    if [[ -n "$ORIG_PID" ]]; then
        echo "$ORIG_PID" > "$PID_FILE" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# 1. Daemon not running when PID_FILE is missing
rm -f "$PID_FILE"
output="$(bash "$STATUS_SCRIPT")"
if echo "$output" | grep -q "daemon:    not running"; then
    ok "status-reports-not-running-when-pid-file-absent"
else
    fail "status-reports-not-running-when-pid-file-absent" "output did not contain 'daemon:    not running'"
fi

# 2. Stale pid file when PID is dead
echo "999999" > "$PID_FILE"
output="$(bash "$STATUS_SCRIPT")"
if echo "$output" | grep -q "daemon:    stale pid file (pid=999999 not alive)"; then
    ok "status-reports-stale-for-dead-pid"
else
    fail "status-reports-stale-for-dead-pid" "output did not report stale pid for 999999"
fi

# 3. Stale pid file when PID is alive but not ours (anti-PID-reuse)
echo "$$" > "$PID_FILE"
output="$(bash "$STATUS_SCRIPT")"
if echo "$output" | grep -q "daemon:    stale pid file (pid=$$ not alive)"; then
    ok "status-reports-stale-for-foreign-alive-pid"
else
    fail "status-reports-stale-for-foreign-alive-pid" "output did not reject foreign alive pid $$"
fi

# 4. Marker status check (ON vs OFF)
TMP_HOME="$(mktemp -d -t status-test-home-XXXXXX)"
export HOME="$TMP_HOME"
export CLAUDE_CODE_SESSION_ID="session-status-123"

# Marker absent
output="$(bash "$STATUS_SCRIPT")"
if echo "$output" | grep -q "marker:    OFF"; then
    ok "status-reports-marker-off-when-absent"
else
    fail "status-reports-marker-off-when-absent" "output did not report marker: OFF"
fi

# Marker present
mkdir -p "$TMP_HOME/.claude/auto-speech-narrate-sessions"
touch "$TMP_HOME/.claude/auto-speech-narrate-sessions/session-status-123"
output="$(bash "$STATUS_SCRIPT")"
if echo "$output" | grep -q "marker:    ON"; then
    ok "status-reports-marker-on-when-present"
else
    fail "status-reports-marker-on-when-present" "output did not report marker: ON"
fi

rm -rf "$TMP_HOME" 2>/dev/null || true

echo
echo "narrator_service_status: $ran ran, $failures failed"
exit "$failures"
