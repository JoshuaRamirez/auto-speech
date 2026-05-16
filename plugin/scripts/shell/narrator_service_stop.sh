#!/usr/bin/env bash
# auto-speech narrator — stop the daemon if it's running.

set -uo pipefail

PID_FILE="/tmp/auto-speech-narrator-daemon.pid"

if [[ ! -f "$PID_FILE" ]]; then
    echo "narrator daemon: not running"
    exit 0
fi

PID="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ -z "${PID:-}" ]]; then
    rm -f "$PID_FILE"
    echo "narrator daemon: pid file empty; cleaned up"
    exit 0
fi

if ! kill -0 "$PID" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "narrator daemon: pid $PID not running; cleaned up"
    exit 0
fi

kill -TERM "$PID"
for _ in 1 2 3 4 5 6 7 8 9 10; do
    if ! kill -0 "$PID" 2>/dev/null; then
        rm -f "$PID_FILE"
        echo "narrator daemon: stopped (pid=$PID)"
        exit 0
    fi
    sleep 0.3
done

kill -KILL "$PID" 2>/dev/null || true
rm -f "$PID_FILE"
echo "narrator daemon: killed (pid=$PID)"
