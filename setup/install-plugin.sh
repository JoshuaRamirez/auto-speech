#!/usr/bin/env bash
# auto-speech — install the auto-speech-* slash commands as user-level
# commands. Idempotent: safe to re-run. Creates symlinks so edits to the
# project propagate automatically.
#
#   bash setup/install-plugin.sh                the 8 curated commands
#   bash setup/install-plugin.sh --with-extras  + the 13 extra commands
#
# Command surface: plugin/commands/ holds the curated keep-set (also what
# the managed-plugin path auto-discovers); plugin/commands-extra/ holds
# the rest (playback transport, narrator controls, status/update), opt-in
# via --with-extras.
#
# Naming convention (see README): every command is prefixed with
# `auto-speech-` to avoid colliding with built-in Claude Code commands
# or other plugins. Generic names like /resume, /pause, /end belong to
# shared namespace and must not be claimed by a single plugin.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CMD_DST_DIR="$HOME/.claude/commands"
mkdir -p "$CMD_DST_DIR"

# Record the clone location for the installed commands: their bash blocks
# resolve PROJECT_ROOT from this file at runtime, so the same command files
# work for any user and any clone path. Re-running after moving the clone
# self-heals the recorded root.
mkdir -p "$HOME/.config/auto-speech"
printf '%s\n' "$PROJECT_ROOT" > "$HOME/.config/auto-speech/root"
echo "[install-plugin] recorded project root: $PROJECT_ROOT"

WITH_EXTRAS=0
if [[ "${1:-}" == "--with-extras" ]]; then
    WITH_EXTRAS=1
    shift
fi

install_one() {
    local subdir="$1" name="$2"
    local src="$PROJECT_ROOT/plugin/$subdir/$name"
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
# commands (autoplay-status/update) live in plugin/commands-extra/ —
# playback transport lives in the web app (/auto-speech-app) and health
# lives in /auto-speech-doctor. The prune loop below removes any
# previously-installed curated-dir command outside this set so re-running
# the installer converges on the curated surface; extras the user opted
# into are left alone (pruned only when their target file is gone).
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

EXTRAS=(
    "auto-speech-pause.md"
    "auto-speech-resume.md"
    "auto-speech-seek.md"
    "auto-speech-restart.md"
    "auto-speech-end.md"
    "auto-speech-autoplay-status.md"
    "auto-speech-update.md"
    "auto-speech-narrate-on.md"
    "auto-speech-narrate-off.md"
    "auto-speech-narrate-status.md"
    "auto-speech-narrate-stop.md"
    "auto-speech-narrate-install.md"
    "auto-speech-narrate-config.md"
)

# Prune stale auto-speech-* command symlinks. Two tiers, both scoped to
# links that point back into this project (never touches real files or
# unrelated links):
#   - targets under plugin/commands/       removed unless in KEEP (this
#     also catches dangling links to files that moved to commands-extra/)
#   - targets under plugin/commands-extra/ removed only when dangling,
#     so a user's opted-in extras survive re-runs
prune_removed() {
    local link target base
    for link in "$CMD_DST_DIR"/auto-speech-*.md; do
        [[ -L "$link" ]] || continue
        target="$(readlink "$link")"
        base="$(basename "$link")"
        if [[ "$target" == "$PROJECT_ROOT/plugin/commands/"* ]]; then
            for keep in "${KEEP[@]}"; do
                [[ "$base" == "$keep" ]] && continue 2
            done
            rm "$link"
            echo "[install-plugin] pruned trimmed command: $link"
        elif [[ "$target" == "$PROJECT_ROOT/plugin/commands-extra/"* ]]; then
            if [[ ! -e "$link" ]]; then
                rm "$link"
                echo "[install-plugin] pruned dangling extra: $link"
            fi
        fi
    done
}

prune_removed

for cmd in "${KEEP[@]}"; do
    install_one "commands" "$cmd"
done

if [[ $WITH_EXTRAS -eq 1 ]]; then
    for cmd in "${EXTRAS[@]}"; do
        install_one "commands-extra" "$cmd"
    done
fi

echo "[install-plugin] auto-speech commands installed (curated keep-set):"
echo "    /auto-speech-speak [n]    /auto-speech-replay [n]    /auto-speech-app"
echo "    /auto-speech-autoplay-on  /auto-speech-autoplay-off  /auto-speech-autoplay-mode"
echo "    /auto-speech-scope        /auto-speech-doctor"
if [[ $WITH_EXTRAS -eq 1 ]]; then
    echo "[install-plugin] extras installed:"
    echo "    /auto-speech-pause         /auto-speech-resume        /auto-speech-seek"
    echo "    /auto-speech-restart       /auto-speech-end           /auto-speech-autoplay-status"
    echo "    /auto-speech-update        /auto-speech-narrate-{on,off,status,stop,install,config}"
else
    echo "[install-plugin] 13 extra commands available: re-run with --with-extras"
fi
