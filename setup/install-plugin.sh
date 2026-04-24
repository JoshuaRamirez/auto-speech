#!/usr/bin/env bash
# auto-speech — install the /speak slash command as a user-level command.
# Idempotent: safe to re-run. Creates a symlink so edits to the project
# propagate automatically.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CMD_SRC="$PROJECT_ROOT/plugin/commands/speak.md"
CMD_DST_DIR="$HOME/.claude/commands"
CMD_DST="$CMD_DST_DIR/speak.md"

if [[ ! -f "$CMD_SRC" ]]; then
    echo "error: expected command file at $CMD_SRC" >&2
    exit 1
fi

mkdir -p "$CMD_DST_DIR"

if [[ -L "$CMD_DST" ]]; then
    existing="$(readlink "$CMD_DST")"
    if [[ "$existing" == "$CMD_SRC" ]]; then
        echo "[install-plugin] symlink already in place: $CMD_DST -> $CMD_SRC"
        exit 0
    fi
    echo "[install-plugin] removing stale symlink $CMD_DST (-> $existing)"
    rm "$CMD_DST"
elif [[ -e "$CMD_DST" ]]; then
    echo "error: $CMD_DST exists and is not a symlink. Refusing to overwrite." >&2
    echo "       Back it up manually, then re-run." >&2
    exit 1
fi

ln -s "$CMD_SRC" "$CMD_DST"
echo "[install-plugin] linked $CMD_DST -> $CMD_SRC"
echo "[install-plugin] /speak is now available in any Claude Code session."
