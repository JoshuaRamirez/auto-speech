#!/usr/bin/env bash
# auto-speech — Stop hook entrypoint.
# Fast (~50ms) path; detaches a worker for the slow rewrite + TTS work.
#
# Wired into ~/.claude/settings.json under hooks.Stop by setup/install-hook.sh.
# Pause without uninstall: `touch ~/.claude/auto-speech.disabled`.

set -uo pipefail

PLUGIN_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKER="$PLUGIN_SCRIPTS_DIR/shell/autoplay_worker.sh"
DISABLED_MARKER="$HOME/.claude/auto-speech.disabled"
LOG="/tmp/auto-speech-autoplay.log"
# Beacon is per-session (set after we parse session_id from the payload).
# Used by is_stale() in the worker to detect a newer Stop event from
# the SAME session. Per-session-keyed so a Stop in session B doesn't
# false-stale session A's in-flight worker during the cross-session
# playback queue wait.
BEACON_DEFAULT="/tmp/auto-speech-last-stop"

# Capture the hook payload. Claude Code passes a JSON document with
# {session_id, transcript_path, cwd, hook_event_name, ...}; we want the
# transcript_path so the worker reads from the EXACT session that fired
# this Stop event, instead of relying on TranscriptLocator's "newest
# .jsonl in slug dir" heuristic — which is wrong any time the user
# runs two Claude sessions in the same project, or any time `claude -p`
# briefly leaves a newer-mtime jsonl in the slug dir.
PAYLOAD="$(cat)"
TRANSCRIPT_PATH=""
SESSION_ID=""
if command -v jq >/dev/null 2>&1; then
    TRANSCRIPT_PATH="$(printf '%s' "$PAYLOAD" | jq -r '.transcript_path // ""' 2>/dev/null || true)"
    SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r '.session_id // ""' 2>/dev/null || true)"
fi

# Nested-claude-p guard: the autoplay's own cli_rewrite spawns `claude -p`,
# which fires Stop hooks against this script. Without this, every rewrite
# triggers a NEW autoplay worker that reads from the rewrite's transcript
# and recursively re-rewrites its own output. cli_rewrite sets this var
# in its subprocess env so we can bail here.
if [[ "${AUTO_SPEECH_SUPPRESS_HOOKS:-}" == "1" ]]; then
    exit 0
fi

# Disable marker — exit 0 fast. Global mute, takes precedence over
# everything else.
if [[ -e "$DISABLED_MARKER" ]]; then
    exit 0
fi

# Per-session OPT-OUT scoping. Autoplay is ON by default for every
# session. A marker file in this dir (CLAUDE_CODE_SESSION_ID keyed,
# written by /auto-speech-autoplay-off) silences just that session;
# all other sessions keep playing. The global mute above still
# overrides everything.
SESSION_OPTOUT_DIR="$HOME/.claude/auto-speech-autoplay-sessions"
if [[ -n "$SESSION_ID" ]] && [[ -e "$SESSION_OPTOUT_DIR/$SESSION_ID" ]]; then
    exit 0
fi

# Per-session beacon path. Falls back to the legacy global beacon when
# we couldn't parse a session_id from the payload (jq missing or
# malformed JSON). The worker derives the same path from its $3 arg.
if [[ -n "$SESSION_ID" ]]; then
    BEACON="$BEACON_DEFAULT.$SESSION_ID"
else
    BEACON="$BEACON_DEFAULT"
fi

# Update the beacon so workers can detect they've been superseded BY
# A NEWER STOP IN THE SAME SESSION. Cross-session Stops touch their own
# beacon files and don't false-stale us.
: > "$BEACON" 2>/dev/null || true

# Capture the beacon mtime to hand to the worker.
BEACON_MTIME="$(stat -f %m "$BEACON" 2>/dev/null || echo 0)"

# Spawn the worker fully detached. macOS lacks GNU `setsid`, so we use
# python3 to do a double-fork + setsid into a new session. The hook
# itself returns in well under 100 ms.
python3 - "$WORKER" "$BEACON_MTIME" "$LOG" "$TRANSCRIPT_PATH" "$SESSION_ID" <<'PY' &
import os, sys
worker, beacon_mtime, log, transcript_path, session_id = (
    sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
)
# First fork: parent returns immediately; child continues.
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
# Worker accepts: beacon_mtime [transcript_path] [session_id]. We always
# pass three args; empty strings for missing pieces so positional parsing
# stays simple in the worker.
os.execvp("bash", ["bash", worker, beacon_mtime, transcript_path or "", session_id or ""])
PY
disown 2>/dev/null || true

exit 0
