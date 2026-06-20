#!/usr/bin/env bash
# auto-speech bootstrap — keep the venv in sync with the committed lock,
# cheaply and without ever blocking the session.
#
# Decides via a stdlib hash check (self_update.py, system python — no venv
# needed) whether uv.lock changed since the last successful sync. Only then
# does it run `uv sync`, single-flight, and stamp the new hash. Intended to
# be wired into the SessionStart hook; also invoked by /auto-speech-update.
#
#   bootstrap.sh           background sync IF the lock changed (SessionStart)
#   bootstrap.sh --force   synchronous sync regardless (explicit update)
#
# It NEVER runs `git pull`: keeping pip deps current is safe and local;
# pulling source onto a repo you may be editing is not. Update source with
# an explicit `git pull` yourself; this only reconciles the venv to the lock.

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK="$PROJECT_ROOT/uv.lock"
STAMP="$PROJECT_ROOT/setup/.synced"
SELF_UPDATE="$PROJECT_ROOT/plugin/scripts/python/self_update.py"
LOCKDIR="/tmp/auto-speech-sync.lockdir"
LOG="/tmp/auto-speech-sync.log"

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

command -v uv >/dev/null 2>&1 || { echo "[bootstrap] uv not found; skipping" >&2; exit 0; }
[[ -f "$LOCK" ]] || exit 0

# Up to date and not forced → nothing to do (the common, ~instant path).
if [[ $FORCE -eq 0 ]] && ! python3 "$SELF_UPDATE" check "$LOCK" "$STAMP" >/dev/null 2>&1; then
    exit 0
fi

# Single-flight guard. Reclaim a stale lockdir from a crashed sync (>60 min).
if [[ -d "$LOCKDIR" ]] && [[ -n "$(find "$LOCKDIR" -prune -mmin +60 2>/dev/null)" ]]; then
    rmdir "$LOCKDIR" 2>/dev/null || true
fi
mkdir "$LOCKDIR" 2>/dev/null || exit 0   # another sync is already running

# Non-destructive: keep the narrate extra if mlx-lm is already installed, so
# a base sync doesn't prune a user's narration capability.
SYNC_ARGS=()
if [[ -x "$PROJECT_ROOT/.venv/bin/python" ]] && \
   "$PROJECT_ROOT/.venv/bin/python" -c "import mlx_lm" >/dev/null 2>&1; then
    SYNC_ARGS+=(--extra narrate)
fi

do_sync() {
    {
        echo "[$(date -u +%FT%TZ)] uv sync ${SYNC_ARGS[*]:-} (lock changed or forced)"
        if ( cd "$PROJECT_ROOT" && uv sync "${SYNC_ARGS[@]}" ); then
            python3 "$SELF_UPDATE" record "$LOCK" "$STAMP"
            echo "[$(date -u +%FT%TZ)] sync ok; stamped"
        else
            echo "[$(date -u +%FT%TZ)] sync FAILED (left unstamped; will retry next session)"
        fi
        rmdir "$LOCKDIR" 2>/dev/null || true
    } >>"$LOG" 2>&1
}

if [[ $FORCE -eq 1 ]]; then
    do_sync                       # synchronous for the explicit command
    echo "auto-speech: sync complete (see $LOG)"
else
    do_sync &                     # detached; the session is never blocked
    disown 2>/dev/null || true
fi
exit 0
