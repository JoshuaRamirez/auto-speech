#!/usr/bin/env bash
# auto-speech — idempotent launcher for the localhost web server.
#
# Behavior:
#   - If a server is already running and healthy → print "already-running"
#     followed by the URL; exit 0.
#   - Else spawn the server fully detached (python double-fork + setsid),
#     write its PID to /tmp/auto-speech-webapp.pid, poll the URL for up
#     to 10 s, then print "started" and the URL on success.
#   - On post-spawn timeout → print "started-but-not-responsive"
#     followed by the URL; exit 1. The server may still be loading.
#
# Output contract: exactly two lines on stdout — a status word and the URL.
# Diagnostic noise (stderr) is fine; the slash command parses stdout only.

set -uo pipefail

PLUGIN_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNSERVER="$PLUGIN_SCRIPTS_DIR/shell/run_server.sh"
PIDFILE="/tmp/auto-speech-webapp.pid"
LOG="/tmp/auto-speech-webapp.log"
URL="http://127.0.0.1:7860/"
STARTUP_DEADLINE_TICKS=20      # 20 × 0.5 s = 10 s
STARTUP_TICK_SECONDS=0.5

if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 not on PATH; cannot spawn detached server" >&2
    exit 1
fi
if [[ ! -x "$RUNSERVER" ]]; then
    echo "error: $RUNSERVER missing or not executable" >&2
    exit 1
fi

is_pidfile_alive() {
    local pid
    [[ -f "$PIDFILE" ]] || return 1
    pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    [[ -n "$pid" && "$pid" =~ ^[0-9]+$ ]] || return 1
    kill -0 "$pid" 2>/dev/null
}

is_responsive() {
    curl -fs -o /dev/null --max-time 2 "$URL" 2>/dev/null
}

# Fast path: the URL is the source of truth for "is the server up?"
# A responsive port means someone owns it — pidfile-owned or not — and
# we must not spawn a duplicate that will fight for it.
if is_responsive; then
    echo "already-running"
    echo "$URL"
    exit 0
fi

# Port idle → no one is serving. Clean any stale pidfile before respawn.
if [[ -f "$PIDFILE" ]] && ! is_pidfile_alive; then
    rm -f "$PIDFILE" 2>/dev/null || true
fi

# Detached spawn via python double-fork + setsid. Same pattern as
# autoplay_hook.sh; portable on macOS where GNU `setsid` is absent.
python3 - "$RUNSERVER" "$LOG" "$PIDFILE" <<'PY' &
import os, sys
runserver, log, pidfile = sys.argv[1], sys.argv[2], sys.argv[3]
# First fork: parent returns to the bash launcher; child continues.
if os.fork() != 0:
    os._exit(0)
os.setsid()
# Second fork: orphan from the new session leader so init reaps it.
if os.fork() != 0:
    os._exit(0)
# Re-open stdio onto /dev/null and the log file.
fd_null = os.open(os.devnull, os.O_RDONLY)
fd_log = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(fd_null, 0)
os.dup2(fd_log, 1)
os.dup2(fd_log, 2)
os.close(fd_null)
os.close(fd_log)
# Write the pidfile with our PID — this PID survives the execvp below.
with open(pidfile, "w") as f:
    f.write(f"{os.getpid()}\n")
os.execvp("bash", ["bash", runserver])
PY
disown 2>/dev/null || true

# Poll for responsiveness.
for _ in $(seq 1 "$STARTUP_DEADLINE_TICKS"); do
    if is_responsive; then
        echo "started"
        echo "$URL"
        exit 0
    fi
    sleep "$STARTUP_TICK_SECONDS"
done

# Server may have spawned but not yet bound the port (model load, Flask import).
# Returning non-zero so the slash command can flag it; the URL line still goes
# to stdout for the user.
echo "started-but-not-responsive"
echo "$URL"
exit 1
