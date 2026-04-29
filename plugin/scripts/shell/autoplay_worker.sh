#!/usr/bin/env bash
# auto-speech — autoplay worker, run detached by autoplay_hook.sh.
# Performs: extract last assistant message → cache lookup → on miss
# rewrite via `claude -p` → speak.py --source-hash. Always exits 0;
# any failure is logged but never bubbles up.

set -u

PLUGIN_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_SCRIPTS_DIR/../.." && pwd)"
EXTRACT="$PLUGIN_SCRIPTS_DIR/shell/run_extract.sh"
COMPUTE_HASH="$PLUGIN_SCRIPTS_DIR/shell/compute_hash.sh"
SPEAK="$PLUGIN_SCRIPTS_DIR/shell/run_speak.sh"
VENV="$PROJECT_ROOT/.venv"
BEACON="/tmp/auto-speech-last-stop"
DISABLED_MARKER="$HOME/.claude/auto-speech.disabled"
MIN_LEN="${AUTO_SPEECH_AUTOPLAY_MIN_LEN:-20}"

START_BEACON_MTIME="${1:-0}"

log() { printf '[%s] [worker pid=%d] %s\n' "$(date -u +%FT%TZ)" $$ "$*"; }

is_stale() {
    local current
    current="$(stat -f %m "$BEACON" 2>/dev/null || echo 0)"
    [[ "$current" -gt "$START_BEACON_MTIME" ]]
}

# A second disable check in the worker: the user could have toggled off
# between hook fire and worker reaching here.
if [[ -e "$DISABLED_MARKER" ]]; then
    log "disable marker present; bailing"
    exit 0
fi

if [[ ! -x "$EXTRACT" ]]; then
    log "extract wrapper missing or not executable: $EXTRACT"
    exit 0
fi

# Extract last assistant message to a temp file.
SRC_FILE="$(mktemp -t auto-speech-autoplay-XXXXXX)"
trap 'rm -f "$SRC_FILE" "$SRC_FILE.rewrite"' EXIT

if ! "$EXTRACT" --ordinal 1 > "$SRC_FILE" 2>/dev/null; then
    log "extract failed (no qualifying message?); skipping"
    exit 0
fi

SRC_LEN="$(wc -c < "$SRC_FILE" | tr -d ' ')"
if [[ -z "$SRC_LEN" || "$SRC_LEN" -lt "$MIN_LEN" ]]; then
    log "source too short ($SRC_LEN < $MIN_LEN); skipping"
    exit 0
fi

# Compute cache key.
SOURCE_HASH="$("$COMPUTE_HASH" < "$SRC_FILE" 2>/dev/null || true)"
if [[ -z "$SOURCE_HASH" ]]; then
    log "compute_hash failed; skipping"
    exit 0
fi
HASH_PREFIX="${SOURCE_HASH:0:16}"
log "source chars=$SRC_LEN hash=$HASH_PREFIX"

if is_stale; then
    log "stale before cache check; bailing"
    exit 0
fi

# Cache hit path: speak.py with empty stdin short-circuits via cache lookup.
CACHE_WAV="$PROJECT_ROOT/config/cache/$HASH_PREFIX/full.wav"
if [[ -f "$CACHE_WAV" ]]; then
    log "cache hit; playing"
    if is_stale; then log "stale just before play; bailing"; exit 0; fi
    : | "$SPEAK" --source-hash "$SOURCE_HASH" >/dev/null 2>&1 || \
        log "speak.py exit non-zero on cache-hit path"
    exit 0
fi

# Cache miss: rewrite via claude -p (delegated to cli_rewrite.py which
# loads the prompt template, enforces a timeout, and surfaces errors
# cleanly).
if ! command -v claude >/dev/null 2>&1; then
    log "claude not on PATH; skipping rewrite"
    exit 0
fi


REWRITE_FILE="$SRC_FILE.rewrite"
log "invoking cli_rewrite.py (wraps claude -p with timeout)"
if [[ ! -d "$VENV" ]]; then
    log "venv missing at $VENV; skipping"
    exit 0
fi
"$VENV/bin/python" "$PROJECT_ROOT/plugin/scripts/python/cli_rewrite.py" \
    --timeout 90 \
    < "$SRC_FILE" > "$REWRITE_FILE" 2>>/tmp/auto-speech-claude-stderr.log
RC=$?
if [[ "$RC" -ne 0 ]]; then
    log "cli_rewrite exit $RC; skipping"
    exit 0
fi

REWRITE_LEN="$(wc -c < "$REWRITE_FILE" | tr -d ' ')"
if [[ -z "$REWRITE_LEN" || "$REWRITE_LEN" -lt 1 ]]; then
    log "cli_rewrite produced empty output; skipping"
    exit 0
fi
log "rewrite chars=$REWRITE_LEN"

if is_stale; then
    log "stale after rewrite; bailing (cache not promoted)"
    exit 0
fi

if is_stale; then log "stale before speak; bailing"; exit 0; fi
"$SPEAK" --source-hash "$SOURCE_HASH" < "$REWRITE_FILE" >/dev/null 2>&1
RC=$?
if [[ "$RC" -ne 0 ]]; then
    log "speak.py exit $RC after rewrite"
fi

exit 0
