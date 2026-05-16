#!/usr/bin/env bash
# auto-speech narrator — remove the narrator hook from all event slots
# in ~/.claude/settings.json. Idempotent: safe to re-run after the hook
# is already gone.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_CMD="$PROJECT_ROOT/plugin/scripts/shell/narrator_hook.sh"
SETTINGS="$HOME/.claude/settings.json"
EVENTS=("PreToolUse" "PostToolUse" "Stop" "UserPromptSubmit")

if ! command -v jq >/dev/null 2>&1; then
    echo "error: jq is required. Install via: brew install jq" >&2
    exit 1
fi

if [[ ! -f "$SETTINGS" ]]; then
    echo "[uninstall-narrator-hooks] $SETTINGS does not exist; nothing to do"
    exit 0
fi

if ! jq -e . "$SETTINGS" >/dev/null 2>&1; then
    echo "error: $SETTINGS is not valid JSON; refusing to edit." >&2
    exit 1
fi

TMP="$(mktemp -t auto-speech-narrator-uninstall-XXXXXX)"
trap 'rm -f "$TMP"' EXIT

# For each event, strip our hook from any block that contains it, then
# drop blocks left empty, then drop the event key if its block list is
# empty, then drop .hooks entirely if no events remain.
jq \
    --arg cmd "$HOOK_CMD" \
    --argjson events "$(printf '%s\n' "${EVENTS[@]}" | jq -R . | jq -s .)" \
    '
    if (.hooks // null) == null then .
    else
        reduce $events[] as $event (
            .;
            if (.hooks[$event] // null) == null then .
            else
                .hooks[$event] = (
                    .hooks[$event]
                    | map(
                        .hooks = ((.hooks // []) | map(select(.command != $cmd)))
                    )
                    | map(select((.hooks | length) > 0))
                )
                | if (.hooks[$event] | length) == 0 then del(.hooks[$event]) else . end
            end
        )
        | if (.hooks // {}) == {} then del(.hooks) else . end
    end
    ' "$SETTINGS" > "$TMP"

mv "$TMP" "$SETTINGS"
trap - EXIT

echo "[uninstall-narrator-hooks] removed narrator hook from ${EVENTS[*]}"
