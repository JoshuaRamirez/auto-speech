#!/usr/bin/env bash
# auto-speech narrator — report daemon status, queue depth, marker state.

set -uo pipefail

PID_FILE="/tmp/auto-speech-narrator-daemon.pid"
DEPTH_FILE="/tmp/auto-speech-narration-depth"
LOG_FILE="/tmp/auto-speech-narrator-daemon.log"
MARKER="$PWD/.claude/narrate.enabled"

echo "narrator status"
echo "  marker:    $([[ -e "$MARKER" ]] && echo "ON   ($MARKER)" || echo "OFF  ($MARKER)")"

if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
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
