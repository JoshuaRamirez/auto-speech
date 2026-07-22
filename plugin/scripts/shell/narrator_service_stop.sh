#!/usr/bin/env bash
# auto-speech narrator — stop the daemon if it's running.

set -uo pipefail

SHELL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# AUTO_SPEECH_TMP_ROOT lets tests point this at a sandbox so a test-driven
# uninstall can never signal the real daemon (see tests/test_install_plugin.sh).
PID_FILE="${AUTO_SPEECH_TMP_ROOT:-/tmp}/auto-speech-narrator-daemon.pid"

# shellcheck source=daemon_pid.sh
source "$SHELL_DIR/daemon_pid.sh"

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

# Identity-checked, NOT a bare kill -0: never signal a process that isn't
# our daemon. Under PID reuse a recycled number would otherwise be killed.
if ! narrator_pid_is_ours "$PID"; then
    rm -f "$PID_FILE"
    echo "narrator daemon: pid $PID is not our daemon (dead or recycled); cleaned up"
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
