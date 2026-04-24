#!/usr/bin/env bash
# auto-speech — install the /speak slash command as a user-level command.
# Idempotent: safe to re-run. Creates a symlink so edits to the project
# propagate automatically.

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

install_one "speak.md"
install_one "replay.md"
install_one "pause.md"
install_one "resume.md"
install_one "seek.md"
install_one "restart.md"
install_one "end.md"

echo "[install-plugin] auto-speech commands installed: /speak /replay /pause /resume /seek /restart /end"
