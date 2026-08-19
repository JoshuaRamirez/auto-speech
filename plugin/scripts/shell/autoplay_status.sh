#!/usr/bin/env bash
# auto-speech — report autoplay status: per-session enrollment marker,
# enrollment dir contents, global disable marker, currently-playing mpv.
# Autoplay is OFF by default; markers enroll individual sessions.
# Enrollment is ONLY ~/.claude/auto-speech-autoplay-enabled/<session_id>.

set -uo pipefail

SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}"
ENROLL_DIR="$HOME/.claude/auto-speech-autoplay-enabled"
SESSION_MARKER="$ENROLL_DIR/${SESSION_ID:-NO_SESSION_ID}"
DISABLE_MARKER="$HOME/.claude/auto-speech.disabled"

echo "autoplay status (default OFF; markers are per-session opt-INs)"
echo "  session:        ${SESSION_ID:-<unset>}"
if [[ -n "$SESSION_ID" ]]; then
    if [[ -e "$SESSION_MARKER" ]]; then
        echo "  this session:   ON   (enrollment marker $SESSION_MARKER)"
    else
        echo "  this session:   OFF  (not enrolled; default)"
    fi
else
    echo "  this session:   OFF by default — CLAUDE_CODE_SESSION_ID not set, cannot enroll"
fi

if [[ -d "$ENROLL_DIR" ]] && [[ -n "$(ls -A "$ENROLL_DIR" 2>/dev/null)" ]]; then
    COUNT=$(ls "$ENROLL_DIR" 2>/dev/null | wc -l | tr -d ' ')
    OTHER=$((COUNT - $([[ -e "$SESSION_MARKER" ]] && echo 1 || echo 0)))
    echo "  enroll dir:     $COUNT session(s) enrolled ($OTHER other than this one)"
else
    echo "  enroll dir:     absent or empty → no session has enrolled"
fi

if [[ -e "$DISABLE_MARKER" ]]; then
    echo "  global disable: PRESENT — autoplay silenced for every session"
else
    echo "  global disable: absent"
fi

# Session SOLO scope (spotlight). Absent marker => every ENROLLED session reads.
SOLO_MARKER="$HOME/.claude/auto-speech-autoplay-solo"
if [[ -e "$SOLO_MARKER" ]]; then
    SOLO_ID="$(tr -d '[:space:]' < "$SOLO_MARKER" 2>/dev/null || true)"
    if [[ -n "$SESSION_ID" ]] && [[ "$SOLO_ID" == "$SESSION_ID" ]]; then
        echo "  scope:          SOLO — only THIS session reads (spotlight=$SOLO_ID)"
    else
        echo "  scope:          SOLO — only session ${SOLO_ID:-<empty>} reads; this session muted"
    fi
else
    echo "  scope:          ALL — every enrolled session reads (default)"
fi

# Currently-playing mpv (from the singleton session dir)
MPV_PID="$(cat /tmp/auto-speech/mpv.pid 2>/dev/null || true)"
if [[ -n "${MPV_PID:-}" ]] && kill -0 "$MPV_PID" 2>/dev/null; then
    WAV="$(cat /tmp/auto-speech/wav.path 2>/dev/null || echo unknown)"
    echo "  mpv:            playing pid=$MPV_PID wav=$WAV"
else
    echo "  mpv:            idle"
fi

# Recent autoplay log tail
LOG=/tmp/auto-speech-autoplay.log
if [[ -f "$LOG" ]]; then
    echo "  log tail:"
    tail -n 4 "$LOG" | sed 's/^/    /'
fi
