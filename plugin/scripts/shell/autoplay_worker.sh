#!/usr/bin/env bash
# auto-speech — autoplay worker shim.
#
# The worker logic now lives in autoplay_worker.py (explicit FSMs built on
# state_machine.py: WorkerLifecycleMachine + StalenessBeacon + PlaybackFifo
# + DedupGuard + AutoplayGate). This shim preserves the exact positional
# arg contract so autoplay_hook.sh needs no change:
#
#   autoplay_worker.sh BEACON_MTIME [TRANSCRIPT_PATH] [SESSION_ID]
#
# It execs the Python worker via the project venv. Always exits 0 (the
# Python worker also always returns 0); any failure is logged there.

set -u

PLUGIN_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_SCRIPTS_DIR/../.." && pwd)"
VENV="$PROJECT_ROOT/.venv"
WORKER_PY="$PLUGIN_SCRIPTS_DIR/python/autoplay_worker.py"

BEACON_MTIME="${1:-0}"
TRANSCRIPT_PATH="${2:-}"
SESSION_ID="${3:-}"

if [[ -x "$VENV/bin/python" ]]; then
    PY="$VENV/bin/python"
else
    PY="python3"
fi

exec "$PY" "$WORKER_PY" "$BEACON_MTIME" "$TRANSCRIPT_PATH" "$SESSION_ID"
