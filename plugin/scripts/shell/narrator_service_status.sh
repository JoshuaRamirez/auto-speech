#!/usr/bin/env bash
# auto-speech narrator — report daemon status, queue depth, marker state.

set -uo pipefail

# Runtime state directory; the test runner points this at a private dir.
STATE_DIR="${AUTO_SPEECH_TMP_ROOT:-/tmp}"
PID_FILE="$STATE_DIR/auto-speech-narrator-daemon.pid"
DEPTH_FILE="$STATE_DIR/auto-speech-narration-depth"
LOG_FILE="$STATE_DIR/auto-speech-narrator-daemon.log"
SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}"
MARKER_DIR="$HOME/.claude/auto-speech-narrate-sessions"
SESSION_MARKER="$MARKER_DIR/${SESSION_ID:-NO_SESSION_ID}"
LEGACY_MARKER="$PWD/.claude/narrate.enabled"

echo "narrator status"
echo "  session:   ${SESSION_ID:-<unset>}"
if [[ -n "$SESSION_ID" ]]; then
    echo "  marker:    $([[ -e "$SESSION_MARKER" ]] && echo "ON   ($SESSION_MARKER)" || echo "OFF  ($SESSION_MARKER)")"
else
    echo "  marker:    cannot check — CLAUDE_CODE_SESSION_ID not set"
fi
if [[ -e "$LEGACY_MARKER" ]]; then
    echo "  legacy:    DEPRECATED per-project marker still present at $LEGACY_MARKER (run /auto-speech-narrate-off to clean)"
fi
if [[ -d "$MARKER_DIR" ]]; then
    OTHER_SESSIONS=0
    for _m in "$MARKER_DIR"/*; do
        [[ -e "$_m" ]] || continue
        [[ "$(basename "$_m")" == "${SESSION_ID:-}" ]] && continue
        OTHER_SESSIONS=$((OTHER_SESSIONS + 1))
    done
    if [[ "$OTHER_SESSIONS" -gt 0 ]]; then
        echo "  others:    $OTHER_SESSIONS other session(s) also opted in"
    fi
fi

SHELL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SHELL_DIR/daemon_pid.sh" ]]; then
    # shellcheck source=daemon_pid.sh
    source "$SHELL_DIR/daemon_pid.sh"
fi

if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${PID:-}" ]] && narrator_pid_is_ours "$PID"; then
        echo "  daemon:    running (pid=$PID)"
    else
        echo "  daemon:    stale pid file (pid=$PID not alive)"
    fi
else
    echo "  daemon:    not running"
fi

if [[ -f "$DEPTH_FILE" ]]; then
    echo "  queue:     depth=$(cat "$DEPTH_FILE" 2>/dev/null)"
else
    echo "  queue:     unknown"
fi

if [[ -f "$LOG_FILE" ]]; then
    echo "  log tail:"
    tail -n 5 "$LOG_FILE" | sed 's/^/    /'
fi
