#!/usr/bin/env bash
# auto-speech — install all auto-speech-* slash commands as user-level
# commands. Idempotent: safe to re-run. Creates symlinks so edits to the
# project propagate automatically.
#
# Naming convention (see README): every command is prefixed with
# `auto-speech-` to avoid colliding with built-in Claude Code commands
# or other plugins. Generic names like /resume, /pause, /end belong to
# shared namespace and must not be claimed by a single plugin.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CMD_DST_DIR="$HOME/.claude/commands"
mkdir -p "$CMD_DST_DIR"

install_one() {
    local name="$1"
    local src="$PROJECT_ROOT/plugin/commands/$name"
    local dst="$CMD_DST_DIR/$name"

    if [[ ! -f "$src" ]]; then
        echo "error: expected command file at $src" >&2
        exit 1
    fi

    if [[ -L "$dst" ]]; then
        local existing
        existing="$(readlink "$dst")"
        if [[ "$existing" == "$src" ]]; then
            echo "[install-plugin] symlink already in place: $dst -> $src"
            return 0
        fi
        echo "[install-plugin] removing stale symlink $dst (-> $existing)"
        rm "$dst"
    elif [[ -e "$dst" ]]; then
        echo "error: $dst exists and is not a symlink. Refusing to overwrite." >&2
        echo "       Back it up manually, then re-run." >&2
        exit 1
    fi

    ln -s "$src" "$dst"
    echo "[install-plugin] linked $dst -> $src"
}

install_one "auto-speech-speak.md"
install_one "auto-speech-replay.md"
install_one "auto-speech-pause.md"
install_one "auto-speech-resume.md"
install_one "auto-speech-seek.md"
install_one "auto-speech-restart.md"
install_one "auto-speech-end.md"
install_one "auto-speech-autoplay-on.md"
install_one "auto-speech-autoplay-off.md"
install_one "auto-speech-autoplay-mode.md"
install_one "auto-speech-autoplay-status.md"
install_one "auto-speech-app.md"
install_one "auto-speech-narrate-on.md"
install_one "auto-speech-narrate-off.md"
install_one "auto-speech-narrate-status.md"
install_one "auto-speech-narrate-stop.md"
install_one "auto-speech-narrate-install.md"
install_one "auto-speech-narrate-config.md"

echo "[install-plugin] auto-speech commands installed:"
echo "    /auto-speech-speak [n]    /auto-speech-replay [n]    /auto-speech-app"
echo "    /auto-speech-pause        /auto-speech-resume        /auto-speech-restart"
echo "    /auto-speech-end          /auto-speech-seek          /auto-speech-autoplay-on"
echo "    /auto-speech-autoplay-off /auto-speech-autoplay-mode /auto-speech-autoplay-status"
echo "    /auto-speech-narrate-on   /auto-speech-narrate-off   /auto-speech-narrate-status"
echo "    /auto-speech-narrate-stop /auto-speech-narrate-install /auto-speech-narrate-config"
