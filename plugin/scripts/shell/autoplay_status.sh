#!/usr/bin/env bash
# auto-speech — report autoplay status: per-session marker, opt-in
# dir presence, global disable marker, currently-playing mpv.

set -uo pipefail

SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}"
OPTIN_DIR="$HOME/.claude/auto-speech-autoplay-sessions"
SESSION_MARKER="$OPTIN_DIR/${SESSION_ID:-NO_SESSION_ID}"
DISABLE_MARKER="$HOME/.claude/auto-speech.disabled"

echo "autoplay status"
echo "  session:        ${SESSION_ID:-<unset>}"
if [[ -n "$SESSION_ID" ]]; then
    if [[ -e "$SESSION_MARKER" ]]; then
        echo "  this session:   ON   ($SESSION_MARKER)"
    else
        echo "  this session:   OFF  ($SESSION_MARKER not present)"
    fi
else
    echo "  this session:   cannot check — CLAUDE_CODE_SESSION_ID not set"
fi

if [[ -d "$OPTIN_DIR" ]] && [[ -n "$(ls -A "$OPTIN_DIR" 2>/dev/null)" ]]; then
    COUNT=$(ls "$OPTIN_DIR" 2>/dev/null | wc -l | tr -d ' ')
    OTHER=$((COUNT - $([[ -e "$SESSION_MARKER" ]] && echo 1 || echo 0)))
    echo "  opt-in dir:     present, $COUNT session(s) total ($OTHER other than this one)"
    echo "                  → autoplay is in STRICT mode: only opted-in sessions fire"
else
    echo "  opt-in dir:     absent or empty → autoplay fires for ALL sessions (legacy default)"
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
