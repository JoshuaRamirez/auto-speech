#!/usr/bin/env bash
# auto-speech narrator — register PreToolUse, PostToolUse, Stop, and
# UserPromptSubmit hooks in ~/.claude/settings.json. The hooks are
# global; the actual narration is gated per-project by the marker
# <cwd>/.claude/narrate.enabled, which the hook itself checks.
#
# Idempotent: re-running yields exactly one block per event for our hook.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_CMD="$PROJECT_ROOT/plugin/scripts/shell/narrator_hook.sh"
SETTINGS="$HOME/.claude/settings.json"
EVENTS=("PreToolUse" "PostToolUse" "Stop" "UserPromptSubmit")

if ! command -v jq >/dev/null 2>&1; then
    echo "error: jq is required. Install via: brew install jq" >&2
    exit 1
fi

if [[ ! -x "$HOOK_CMD" ]]; then
    echo "error: hook script missing or not executable: $HOOK_CMD" >&2
    exit 1
fi

mkdir -p "$HOME/.claude"
if [[ ! -f "$SETTINGS" ]]; then
    echo "[install-narrator-hooks] creating $SETTINGS"
    echo '{}' > "$SETTINGS"
fi

if ! jq -e . "$SETTINGS" >/dev/null 2>&1; then
    echo "error: $SETTINGS is not valid JSON; refusing to edit." >&2
    exit 1
fi

TMP="$(mktemp -t auto-speech-narrator-install-XXXXXX)"
trap 'rm -f "$TMP"' EXIT

# For each event, append a block containing our hook if it's not already
# present anywhere under .hooks[$event]. jq edits don't compose well when
# done in one pass per event, so loop in jq's `reduce`.
jq \
    --arg cmd "$HOOK_CMD" \
    --argjson events "$(printf '%s\n' "${EVENTS[@]}" | jq -R . | jq -s .)" \
    '
    .hooks //= {} |
    reduce $events[] as $event (
        .;
        .hooks[$event] //= [] |
        (
            [.hooks[$event][]? | .hooks[]? | select(.type == "command" and .command == $cmd)]
            | length
        ) as $count |
        if $count >= 1 then .
        else .hooks[$event] += [ { "hooks": [ { "type": "command", "command": $cmd } ] } ]
        end
    )
    ' "$SETTINGS" > "$TMP"

mv "$TMP" "$SETTINGS"
trap - EXIT

echo "[install-narrator-hooks] registered ${EVENTS[*]} → $HOOK_CMD"
echo "[install-narrator-hooks] per-project enable: touch <project>/.claude/narrate.enabled"
echo "[install-narrator-hooks] events log:         /tmp/auto-speech-narrator-events.jsonl"
echo "[install-narrator-hooks] uninstall:          setup/uninstall-narrator-hooks.sh"
