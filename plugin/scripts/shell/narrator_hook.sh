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

# Per-project gate. Cheap test, runs before anything else.
MARKER="$PWD/.claude/narrate.enabled"
if [[ ! -e "$MARKER" ]]; then
    exit 0
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
