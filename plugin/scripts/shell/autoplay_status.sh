#!/usr/bin/env bash
# auto-speech — report autoplay status: per-session opt-out marker,
# opt-out dir contents, global disable marker, currently-playing mpv.
# Autoplay is ON by default; markers silence individual sessions.

set -uo pipefail

SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}"
OPTOUT_DIR="$HOME/.claude/auto-speech-autoplay-sessions"
SESSION_MARKER="$OPTOUT_DIR/${SESSION_ID:-NO_SESSION_ID}"
DISABLE_MARKER="$HOME/.claude/auto-speech.disabled"

echo "autoplay status (default ON; markers are per-session opt-OUTs)"
echo "  session:        ${SESSION_ID:-<unset>}"
if [[ -n "$SESSION_ID" ]]; then
    if [[ -e "$SESSION_MARKER" ]]; then
        echo "  this session:   OFF  (opt-out marker $SESSION_MARKER)"
    else
        echo "  this session:   ON   (no opt-out marker; default)"
    fi
else
    echo "  this session:   ON by default — CLAUDE_CODE_SESSION_ID not set, cannot be opted out"
fi

if [[ -d "$OPTOUT_DIR" ]] && [[ -n "$(ls -A "$OPTOUT_DIR" 2>/dev/null)" ]]; then
    COUNT=$(ls "$OPTOUT_DIR" 2>/dev/null | wc -l | tr -d ' ')
    OTHER=$((COUNT - $([[ -e "$SESSION_MARKER" ]] && echo 1 || echo 0)))
    echo "  opt-out dir:    $COUNT session(s) opted out ($OTHER other than this one)"
else
    echo "  opt-out dir:    absent or empty → no session has opted out"
fi

if [[ -e "$DISABLE_MARKER" ]]; then
    echo "  global disable: PRESENT — autoplay silenced for every session"
else
    echo "  global disable: absent"
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
