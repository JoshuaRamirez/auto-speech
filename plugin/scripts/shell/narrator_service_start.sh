#!/usr/bin/env bash
# auto-speech narrator — start the daemon if it's not already running.
# Detaches via python3 double-fork; survives the current shell.

set -uo pipefail

PLUGIN_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_SCRIPTS_DIR/../.." && pwd)"
VENV="$PROJECT_ROOT/.venv"
SERVICE="$PLUGIN_SCRIPTS_DIR/python/narrator_service.py"
PID_FILE="/tmp/auto-speech-narrator-daemon.pid"
# Structured log: owned and rotated in-process by auto_speech_log
# (RotatingFileHandler). The daemon's RAW stdio (tracebacks, MLX progress)
# goes to a SEPARATE .out file so it doesn't write to LOG_FILE and defeat
# that rotation. The .out is rotated here, pre-spawn.
LOG_FILE="/tmp/auto-speech-narrator-daemon.log"
OUT_FILE="/tmp/auto-speech-narrator-daemon.out"

# shellcheck source=log_rotate.sh
source "$PLUGIN_SCRIPTS_DIR/shell/log_rotate.sh"
as_rotate_log "$OUT_FILE"

if [[ ! -x "$VENV/bin/python" ]]; then
    echo "error: venv missing at $VENV. Run setup/install.sh first." >&2
    exit 1
fi

if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
        echo "narrator daemon already running (pid=$PID)"
        exit 0
    fi
fi

python3 - "$VENV/bin/python" "$SERVICE" "$OUT_FILE" <<'PY' &
import os, sys
py, service, log = sys.argv[1], sys.argv[2], sys.argv[3]
if os.fork() != 0:
    os._exit(0)
os.setsid()
if os.fork() != 0:
    os._exit(0)
fd_null = os.open(os.devnull, os.O_RDONLY)
fd_log = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(fd_null, 0)
os.dup2(fd_log, 1)
os.dup2(fd_log, 2)
os.close(fd_null)
os.close(fd_log)
os.execvp(py, [py, service])
PY
disown 2>/dev/null || true

# Wait briefly for PID file to appear so callers can verify
for _ in 1 2 3 4 5 6 7 8 9 10; do
    if [[ -f "$PID_FILE" ]]; then
        echo "narrator daemon started (pid=$(cat "$PID_FILE"))"
        exit 0
    fi
    sleep 0.2
done
echo "narrator daemon: pid file did not appear; check $OUT_FILE (raw stdio) and $LOG_FILE" >&2
exit 1
