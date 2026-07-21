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

# Curated keep-set (2026-07-21): the plugin exposes only the highest-value
# commands. Transport controls (pause/resume/seek/restart/end), the
# narration subsystem (narrate-*), and the low-frequency maintenance/status
# commands (autoplay-status/update) were trimmed — playback transport lives
# in the web app (/auto-speech-app) and health lives in /auto-speech-doctor.
# The prune loop below removes any previously-installed command outside this
# set so re-running the installer converges on the curated surface.
KEEP=(
    "auto-speech-speak.md"
    "auto-speech-replay.md"
    "auto-speech-app.md"
    "auto-speech-autoplay-on.md"
    "auto-speech-autoplay-off.md"
    "auto-speech-autoplay-mode.md"
    "auto-speech-scope.md"
    "auto-speech-doctor.md"
)

# Prune stale auto-speech-* command symlinks not in the keep-set. Only
# removes symlinks that point back into this project's plugin/commands dir;
# never touches real files or unrelated links.
prune_removed() {
    local link target base
    for link in "$CMD_DST_DIR"/auto-speech-*.md; do
        [[ -L "$link" ]] || continue
        target="$(readlink "$link")"
        [[ "$target" == "$PROJECT_ROOT/plugin/commands/"* ]] || continue
        base="$(basename "$link")"
        for keep in "${KEEP[@]}"; do
            [[ "$base" == "$keep" ]] && continue 2
        done
        rm "$link"
        echo "[install-plugin] pruned trimmed command: $link"
    done
}

prune_removed

for cmd in "${KEEP[@]}"; do
    install_one "$cmd"
done

echo "[install-plugin] auto-speech commands installed (curated keep-set):"
echo "    /auto-speech-speak [n]    /auto-speech-replay [n]    /auto-speech-app"
echo "    /auto-speech-autoplay-on  /auto-speech-autoplay-off  /auto-speech-autoplay-mode"
echo "    /auto-speech-scope        /auto-speech-doctor"
