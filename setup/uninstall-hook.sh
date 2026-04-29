#!/usr/bin/env bash
# auto-speech — remove our Stop hook from ~/.claude/settings.json.
# Idempotent: running twice is a no-op the second time.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_CMD="$PROJECT_ROOT/plugin/scripts/shell/autoplay_hook.sh"
SETTINGS="$HOME/.claude/settings.json"

if ! command -v jq >/dev/null 2>&1; then
    echo "error: jq is required. Install via: brew install jq" >&2
    exit 1
fi

if [[ ! -f "$SETTINGS" ]]; then
    echo "[uninstall-hook] $SETTINGS does not exist; nothing to do"
    exit 0
fi

if ! jq -e . "$SETTINGS" >/dev/null 2>&1; then
    echo "error: $SETTINGS is not valid JSON; refusing to edit." >&2
    exit 1
fi

EXISTING_COUNT="$(jq --arg cmd "$HOOK_CMD" '
    (.hooks // {}).Stop // []
    | [.[]?.hooks[]? | select(.type == "command" and .command == $cmd)]
    | length
' "$SETTINGS")"

if [[ "${EXISTING_COUNT:-0}" -eq 0 ]]; then
    echo "[uninstall-hook] hook not present; nothing to do"
    exit 0
fi

TMP="$(mktemp -t auto-speech-settings-XXXXXX)"
trap 'rm -f "$TMP"' EXIT

# For each Stop block, remove any inner hook whose command is ours.
# Then drop blocks whose .hooks list ended up empty. Then drop .hooks.Stop
# entirely if it ended up empty.
jq --arg cmd "$HOOK_CMD" '
    if (.hooks.Stop // null) == null then .
    else
        .hooks.Stop = (
            .hooks.Stop
            | map(
                .hooks = ((.hooks // []) | map(select(.command != $cmd)))
              )
            | map(select((.hooks | length) > 0))
        )
        | if (.hooks.Stop | length) == 0 then del(.hooks.Stop) else . end
        | if (.hooks // {}) == {} then del(.hooks) else . end
    end
' "$SETTINGS" > "$TMP"

mv "$TMP" "$SETTINGS"
trap - EXIT

echo "[uninstall-hook] removed Stop hook → $HOOK_CMD"
