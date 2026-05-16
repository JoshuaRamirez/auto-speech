#!/usr/bin/env bash
# auto-speech — full uninstall from the user's environment.
#
# Removes everything we ever wrote into ~/.claude or /tmp:
#   - slash command symlinks under ~/.claude/commands/
#   - Stop hook entry in ~/.claude/settings.json (via uninstall-hook.sh)
#   - the autoplay disable marker
#   - any running web_server.py and mpv processes we own
#   - /tmp/auto-speech* working files
#
# Does NOT touch:
#   - mpv / jq / uv (brew packages — useful elsewhere)
#   - the project source tree (you can `rm -rf` the auto-speech dir
#     yourself if you want)
#   - the venv (lives inside the project tree)
#
# Idempotent: safe to re-run.

set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CMD_DIR="$HOME/.claude/commands"
DISABLE_MARKER="$HOME/.claude/auto-speech.disabled"

echo "[uninstall] auto-speech project at $PROJECT_ROOT"

# 1. Remove slash command symlinks if (and only if) they point into our
# project. We check BOTH the current namespaced names (auto-speech-*) AND
# the legacy generic names (speak, pause, ...) so an upgrade from before
# the naming convention still cleans up fully.
NAMESPACED_CMDS=(
    auto-speech-app
    auto-speech-speak auto-speech-replay
    auto-speech-pause auto-speech-resume auto-speech-restart
    auto-speech-end auto-speech-seek
    auto-speech-autoplay-on auto-speech-autoplay-off auto-speech-autoplay-mode
    auto-speech-narrate-on auto-speech-narrate-off
    auto-speech-narrate-status auto-speech-narrate-stop
    auto-speech-narrate-install auto-speech-narrate-config
)
LEGACY_CMDS=(
    speak replay pause resume seek restart end autoplay-on autoplay-off
)
removed_cmds=0
for cmd in "${NAMESPACED_CMDS[@]}" "${LEGACY_CMDS[@]}"; do
    link="$CMD_DIR/$cmd.md"
    if [[ -L "$link" ]]; then
        target="$(readlink "$link")"
        case "$target" in
            "$PROJECT_ROOT/"*)
                rm -f "$link"
                echo "[uninstall] removed symlink /$cmd → $target"
                removed_cmds=$((removed_cmds + 1))
                ;;
            *)
                echo "[uninstall] skipped /$cmd (symlink points outside project: $target)"
                ;;
        esac
    fi
done
echo "[uninstall] removed $removed_cmds slash command symlinks"

# 2. Remove Stop hook entry. Use the existing idempotent uninstall script.
if [[ -x "$PROJECT_ROOT/setup/uninstall-hook.sh" ]]; then
    "$PROJECT_ROOT/setup/uninstall-hook.sh" || true
else
    echo "[uninstall] uninstall-hook.sh missing; skipping Stop hook removal"
fi

# 2b. Remove narrator hooks (Pre/PostToolUse/Stop/UserPromptSubmit).
if [[ -x "$PROJECT_ROOT/setup/uninstall-narrator-hooks.sh" ]]; then
    "$PROJECT_ROOT/setup/uninstall-narrator-hooks.sh" || true
else
    echo "[uninstall] uninstall-narrator-hooks.sh missing; skipping narrator hooks"
fi

# 2c. Stop the narrator daemon if running.
if [[ -x "$PROJECT_ROOT/plugin/scripts/shell/narrator_service_stop.sh" ]]; then
    "$PROJECT_ROOT/plugin/scripts/shell/narrator_service_stop.sh" || true
fi

# 3. Remove autoplay disable marker.
if [[ -e "$DISABLE_MARKER" ]]; then
    rm -f "$DISABLE_MARKER"
    echo "[uninstall] removed disable marker $DISABLE_MARKER"
fi

# 4. Stop any running web_server.py we own.
if pgrep -f "$PROJECT_ROOT/.venv/bin/python.*web_server.py" >/dev/null 2>&1; then
    pkill -f "$PROJECT_ROOT/.venv/bin/python.*web_server.py" || true
    echo "[uninstall] sent SIGTERM to web_server.py"
fi

# 5. Stop any active mpv playback session we own.
if [[ -S /tmp/auto-speech/control.sock ]]; then
    pkill -f 'mpv .* --input-ipc-server=/tmp/auto-speech/control.sock' || true
    echo "[uninstall] sent SIGTERM to mpv playback"
fi

# 6. Clean /tmp/ artifacts.
rm -rf /tmp/auto-speech 2>/dev/null && echo "[uninstall] removed /tmp/auto-speech/"
rm -f /tmp/auto-speech-webapp.pid 2>/dev/null && echo "[uninstall] removed /tmp/auto-speech-webapp.pid"
rm -f /tmp/auto-speech-webapp.log 2>/dev/null && echo "[uninstall] removed /tmp/auto-speech-webapp.log"
rm -f /tmp/auto-speech-autoplay.log 2>/dev/null && echo "[uninstall] removed /tmp/auto-speech-autoplay.log"
rm -f /tmp/auto-speech-claude-stderr.log 2>/dev/null && echo "[uninstall] removed /tmp/auto-speech-claude-stderr.log"
rm -f /tmp/auto-speech-last-stop 2>/dev/null && echo "[uninstall] removed /tmp/auto-speech-last-stop"
rm -f /tmp/auto-speech-narrator-events.jsonl 2>/dev/null && echo "[uninstall] removed /tmp/auto-speech-narrator-events.jsonl"
rm -f /tmp/auto-speech-narrator-hook.err 2>/dev/null && echo "[uninstall] removed /tmp/auto-speech-narrator-hook.err"
rm -f /tmp/auto-speech-narrator-daemon.log 2>/dev/null && echo "[uninstall] removed /tmp/auto-speech-narrator-daemon.log"
rm -f /tmp/auto-speech-narrator-daemon.pid 2>/dev/null && echo "[uninstall] removed /tmp/auto-speech-narrator-daemon.pid"
rm -f /tmp/auto-speech-narrator-daemon.watermark 2>/dev/null && echo "[uninstall] removed /tmp/auto-speech-narrator-daemon.watermark"
rm -f /tmp/auto-speech-narration-depth 2>/dev/null && echo "[uninstall] removed /tmp/auto-speech-narration-depth"
if [[ -d "$HOME/.claude/auto-speech-narrate-sessions" ]]; then
    rm -rf "$HOME/.claude/auto-speech-narrate-sessions"
    echo "[uninstall] removed per-session narrate markers ($HOME/.claude/auto-speech-narrate-sessions)"
fi
if [[ -d "$HOME/.claude/auto-speech-autoplay-sessions" ]]; then
    rm -rf "$HOME/.claude/auto-speech-autoplay-sessions"
    echo "[uninstall] removed per-session autoplay markers ($HOME/.claude/auto-speech-autoplay-sessions)"
fi

echo "[uninstall] done."
echo
echo "Not touched (intentionally):"
echo "  - $PROJECT_ROOT/        (source — \`rm -rf\` it yourself if desired)"
echo "  - $PROJECT_ROOT/.venv/  (the project's Python venv)"
echo "  - mpv, jq, uv (Homebrew)"
echo "  - $PROJECT_ROOT/config/cache/  (cached audio, machine-local)"
