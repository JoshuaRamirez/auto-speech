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
BEACON_DEFAULT="/tmp/auto-speech-last-stop"
DISABLED_MARKER="$HOME/.claude/auto-speech.disabled"
MIN_LEN="${AUTO_SPEECH_AUTOPLAY_MIN_LEN:-20}"
# Coalesce window: rapid-fire Stops within this many seconds collapse —
# only the newest spawned worker survives its early stale check, the rest
# bail before doing any rewrite/TTS work. Tuned to be short enough that
# autoplay still feels prompt, long enough to absorb a tool-call burst.
# Load coalesce + narration-wait from autoplay.toml (via autoplay_config.py).
# Env vars still take precedence so existing callers don't break.
# PLUGIN_SCRIPTS_DIR / VENV are set above.
COALESCE_FROM_CONFIG=""
NARRATION_WAIT_FROM_CONFIG=""
if [[ -x "$VENV/bin/python" ]]; then
    CFG_JSON="$("$VENV/bin/python" "$PLUGIN_SCRIPTS_DIR/python/autoplay_config.py" 2>/dev/null || true)"
    if [[ -n "$CFG_JSON" ]]; then
        COALESCE_FROM_CONFIG="$(printf '%s' "$CFG_JSON" | "$VENV/bin/python" -c 'import json,sys; d=json.load(sys.stdin); print(d.get("coalesce_seconds",""))' 2>/dev/null || true)"
        # int(float(...)) so bash's (( ... )) arithmetic doesn't choke
        # on a decimal value from the TOML (which is float by spec).
        NARRATION_WAIT_FROM_CONFIG="$(printf '%s' "$CFG_JSON" | "$VENV/bin/python" -c 'import json,sys; d=json.load(sys.stdin); v=d.get("narration_wait_max_seconds"); print(int(float(v)) if v is not None else "")' 2>/dev/null || true)"
    fi
fi
COALESCE_SECONDS="${AUTO_SPEECH_AUTOPLAY_COALESCE:-${COALESCE_FROM_CONFIG:-1}}"

START_BEACON_MTIME="${1:-0}"
# $2: transcript_path from the hook payload, threaded through
# autoplay_hook.sh so extract reads from the EXACT session that fired
# this Stop event rather than guessing by jsonl mtime.
TRANSCRIPT_PATH="${2:-}"
# $3: session_id from the hook payload. Used to derive a per-session
# beacon path so a Stop in session B doesn't false-stale session A's
# in-flight worker during the cross-session playback queue wait.
SESSION_ID="${3:-}"

# Derive the beacon path consistently with autoplay_hook.sh. If we have
# no session_id (jq missing or malformed payload), use the legacy
# global beacon — preserves single-session behaviour, only loses the
# cross-session false-stale protection.
if [[ -n "$SESSION_ID" ]]; then
    BEACON="$BEACON_DEFAULT.$SESSION_ID"
else
    BEACON="$BEACON_DEFAULT"
fi

log() { printf '[%s] [worker pid=%d sid=%.8s] %s\n' "$(date -u +%FT%TZ)" $$ "${SESSION_ID:-no-sid}" "$*"; }

is_stale() {
    local current
    current="$(stat -f %m "$BEACON" 2>/dev/null || echo 0)"
    [[ "$current" -gt "$START_BEACON_MTIME" ]]
}

# Unconditional mpv-busy wait. Always wait for any in-flight mpv (from
# our session OR from another session's autoplay OR from the narrator
# daemon) to finish before we'd start ours. Without this, two sessions
# running autoplay end up interrupting each other — user complaint:
# "the summary of the above interrupted what i was listening to from
# the cli". Capped same as the narrator-drain wait. Returns 0 if mpv
# went idle, 1 if we hit the cap or got staled out.
wait_for_mpv_idle() {
    local cap_seconds="$1"
    local waited=0
    while (( waited < cap_seconds )); do
        local mpv_pid mpv_running=0
        mpv_pid="$(cat /tmp/auto-speech/mpv.pid 2>/dev/null || true)"
        if [[ -n "${mpv_pid:-}" ]] && kill -0 "$mpv_pid" 2>/dev/null; then
            mpv_running=1
        fi
        # Also wait for the narrator FIFO to drain if its daemon is
        # alive (matches the original Phase 22 semantic).
        local depth=0
        if [[ -f "$NARRATION_DAEMON_PID_FILE" ]] && [[ -f "$NARRATION_DEPTH_FILE" ]]; then
            local narr_pid
            narr_pid="$(cat "$NARRATION_DAEMON_PID_FILE" 2>/dev/null || true)"
            if [[ -n "${narr_pid:-}" ]] && kill -0 "$narr_pid" 2>/dev/null; then
                depth="$(cat "$NARRATION_DEPTH_FILE" 2>/dev/null || echo 0)"
            fi
        fi
        if [[ "$mpv_running" -eq 0 ]] && [[ "${depth:-0}" -eq 0 ]]; then
            return 0
        fi
        sleep 0.5
        waited=$((waited + 1))
        if is_stale; then
            log "stale while waiting for playback queue (mpv=$mpv_running depth=$depth); bailing"
            return 1
        fi
    done
    log "playback wait hit cap (${cap_seconds}s, mpv=$mpv_running depth=$depth); proceeding anyway"
    return 0
}

# Dedup: if another worker is already playing the exact same source hash
# (which means we'd render the same audio and kill its mpv mid-play —
# user hears the same line cut off and start over), bail out instead.
# 120-second age cap so a stale marker from a long-finished play doesn't
# block a legitimate replay of identical content much later.
NOW_PLAYING_MARKER="/tmp/auto-speech-now-playing-hash"

already_playing_same_hash() {
    [[ -f "$NOW_PLAYING_MARKER" ]] || return 1
    local current_hash
    current_hash="$(cat "$NOW_PLAYING_MARKER" 2>/dev/null || true)"
    [[ "$current_hash" == "$SOURCE_HASH" ]] || return 1
    local marker_mtime now age
    marker_mtime="$(stat -f %m "$NOW_PLAYING_MARKER" 2>/dev/null || echo 0)"
    now="$(date +%s)"
    age=$(( now - marker_mtime ))
    (( age < 120 )) || return 1
    local mpv_pid
    mpv_pid="$(cat /tmp/auto-speech/mpv.pid 2>/dev/null || true)"
    [[ -n "${mpv_pid:-}" ]] && kill -0 "$mpv_pid" 2>/dev/null
}

# A second disable check in the worker: the user could have toggled off
# between hook fire and worker reaching here.
if [[ -e "$DISABLED_MARKER" ]]; then
    log "disable marker present; bailing"
    exit 0
fi

# Coalesce: sleep briefly, then bail if a newer Stop has fired. This
# collapses bursts of Stop events (tool-heavy turns) down to one survivor.
sleep "$COALESCE_SECONDS"
if is_stale; then
    log "stale during coalesce window; bailing (newer worker will handle)"
    exit 0
fi

# EARLY wait: don't bother rewriting if another mpv (any session's
# autoplay OR the narrator) is currently playing — wait first. Reduces
# wasted work (rewrite + TTS) when there's an obvious queue ahead of us.
NARRATION_DEPTH_FILE="/tmp/auto-speech-narration-depth"
NARRATION_DAEMON_PID_FILE="/tmp/auto-speech-narrator-daemon.pid"
NARRATION_WAIT_MAX="${AUTO_SPEECH_NARRATION_WAIT_MAX:-${NARRATION_WAIT_FROM_CONFIG:-90}}"
wait_for_mpv_idle "$NARRATION_WAIT_MAX" || exit 0

if [[ ! -x "$EXTRACT" ]]; then
    log "extract wrapper missing or not executable: $EXTRACT"
    exit 0
fi

# Extract last assistant message to a temp file.
SRC_FILE="$(mktemp -t auto-speech-autoplay-XXXXXX)"
trap 'rm -f "$SRC_FILE" "$SRC_FILE.rewrite"' EXIT

EXTRACT_ARGS=(--ordinal 1)
if [[ -n "$TRANSCRIPT_PATH" ]]; then
    EXTRACT_ARGS+=(--transcript-path "$TRANSCRIPT_PATH")
fi
if ! "$EXTRACT" "${EXTRACT_ARGS[@]}" > "$SRC_FILE" 2>/dev/null; then
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
    if already_playing_same_hash; then
        log "same hash already playing; skipping duplicate (cache-hit path)"
        exit 0
    fi
    # LATE wait: between the early wait and now, another session's
    # autoplay may have started mpv. Re-wait so we queue properly.
    wait_for_mpv_idle "$NARRATION_WAIT_MAX" || exit 0
    printf '%s' "$SOURCE_HASH" > "$NOW_PLAYING_MARKER" 2>/dev/null || true
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

# Intentionally no staleness check here. If a newer Stop fired during the
# rewrite, that newer worker is also running; when it reaches speak.py,
# MpvController._kill_prior_session() tears down whatever mpv this worker
# starts. Net effect: brief truncation of the older audio, newest content
# wins. Bailing here instead would mean the user hears NOTHING in active
# multi-turn conversations where rewrites can't outrun the next Stop.
#
# But: if the newer worker has the SAME source hash (identical content,
# happens when multiple Stop events fire for the same assistant turn),
# we'd just be killing one play of X to start an identical play of X —
# the user hears the line cut off and restart. Dedup here.
if already_playing_same_hash; then
    log "same hash already playing; skipping duplicate (post-rewrite path)"
    exit 0
fi
# LATE wait: rewrite took several seconds — another session may have
# started mpv during it. Wait so we queue cleanly behind them.
wait_for_mpv_idle "$NARRATION_WAIT_MAX" || exit 0
printf '%s' "$SOURCE_HASH" > "$NOW_PLAYING_MARKER" 2>/dev/null || true
"$SPEAK" --source-hash "$SOURCE_HASH" < "$REWRITE_FILE" >/dev/null 2>&1
RC=$?
if [[ "$RC" -ne 0 ]]; then
    log "speak.py exit $RC after rewrite"
fi

exit 0
