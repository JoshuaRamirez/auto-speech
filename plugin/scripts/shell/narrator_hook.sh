#!/usr/bin/env bash
# auto-speech narrator — PreToolUse / PostToolUse / Stop / UserPromptSubmit
# hook entrypoint. Reads the hook payload from stdin, decorates with
# cwd + timestamp + the hook_event_name field, appends one JSONL record
# to /tmp/auto-speech-narrator-events.jsonl.
#
# Gated by <cwd>/.claude/narrate.enabled — when the marker is absent the
# hook returns immediately (no payload parse, no jq launch).
#
# Pure bash + jq. No python in this path: PreToolUse fires many times
# per turn, and a 200 ms python startup per fire would be visible.
#
# Always exits 0; any failure goes to /tmp/auto-speech-narrator-hook.err.

set -uo pipefail

EVENTS_LOG="/tmp/auto-speech-narrator-events.jsonl"
ERR_LOG="/tmp/auto-speech-narrator-hook.err"

# Always consume stdin so Claude Code's hook payload doesn't break the pipe.
PAYLOAD="$(cat)"

# Nested-claude-p guard: cli_rewrite spawns `claude -p` which fires its
# own hooks. Those events are noise, not narratable activity. Bail.
if [[ "${AUTO_SPEECH_SUPPRESS_HOOKS:-}" == "1" ]]; then
    exit 0
fi

# Per-session gate. Each session opts in individually by touching
# ~/.claude/auto-speech-narrate-sessions/<session_id>. The session id
# comes from the Claude Code hook payload (which always includes it),
# falling back to the CLAUDE_CODE_SESSION_ID env var if jq is missing
# or the payload doesn't parse. The env-var path is unreliable across
# Claude Code versions — some propagate it to hook subprocesses, some
# don't — so payload is preferred.
SESSION_ID=""
if command -v jq >/dev/null 2>&1; then
    SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r '.session_id // ""' 2>/dev/null || true)"
fi
if [[ -z "$SESSION_ID" ]]; then
    SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}"
fi
if [[ -z "$SESSION_ID" ]]; then
    exit 0
fi
MARKER="$HOME/.claude/auto-speech-narrate-sessions/$SESSION_ID"
if [[ ! -e "$MARKER" ]]; then
    exit 0
fi

# Auto-spawn the daemon if the user opted this session in but the daemon
# has since exited (idle-shutdown after 10 min, or crashed, or never
# started after a fresh boot). Without this, the user runs
# /auto-speech-narrate-on once, comes back hours later, and hears
# silence with no obvious diagnostic.
PID_FILE="/tmp/auto-speech-narrator-daemon.pid"
DAEMON_ALIVE=0
if [[ -f "$PID_FILE" ]]; then
    DAEMON_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${DAEMON_PID:-}" ]] && kill -0 "$DAEMON_PID" 2>/dev/null; then
        DAEMON_ALIVE=1
    fi
fi
if [[ "$DAEMON_ALIVE" -eq 0 ]]; then
    PLUGIN_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    START_SCRIPT="$PLUGIN_SCRIPTS_DIR/narrator_service_start.sh"
    if [[ -x "$START_SCRIPT" ]]; then
        # Spawn detached so we don't block the hook. Stderr to err log.
        ( "$START_SCRIPT" >/dev/null 2>>"$ERR_LOG" & ) 2>/dev/null
    fi
fi

if ! command -v jq >/dev/null 2>&1; then
    printf '[%s] jq missing on PATH\n' "$(date -u +%FT%TZ)" >> "$ERR_LOG"
    exit 0
fi

TS="$(date +%s)"

# Build the event record. Tolerate non-JSON payloads so a malformed
# Claude Code hook payload (or a manual smoke test) still produces a line.
if printf '%s' "$PAYLOAD" | jq -e . >/dev/null 2>&1; then
    EVENT="$(printf '%s' "$PAYLOAD" | jq -c \
        --arg cwd "$PWD" \
        --arg ts "$TS" \
        --arg pid "$$" \
        '{
            ts: ($ts | tonumber),
            cwd: $cwd,
            pid: ($pid | tonumber),
            event: (.hook_event_name // "unknown"),
            payload: .
        }' 2>>"$ERR_LOG")" || EVENT=""
else
    EVENT="$(jq -cn \
        --arg raw "$PAYLOAD" \
        --arg cwd "$PWD" \
        --arg ts "$TS" \
        --arg pid "$$" \
        '{
            ts: ($ts | tonumber),
            cwd: $cwd,
            pid: ($pid | tonumber),
            event: "unknown",
            payload: { raw: $raw }
        }' 2>>"$ERR_LOG")" || EVENT=""
fi

if [[ -z "$EVENT" ]]; then
    printf '[%s] event-build failed\n' "$(date -u +%FT%TZ)" >> "$ERR_LOG"
    exit 0
fi

# Append. Single-line write of a few hundred bytes is well under PIPE_BUF
# (Darwin 512) for the typical hook payload, so concurrent writes from
# overlapping hook fires interleave at line granularity.
printf '%s\n' "$EVENT" >> "$EVENTS_LOG"
exit 0
