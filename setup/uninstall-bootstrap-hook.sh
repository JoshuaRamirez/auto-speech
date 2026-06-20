#!/usr/bin/env bash
# auto-speech — remove the self-update bootstrap from the SessionStart slot
# in ~/.claude/settings.json. Idempotent: safe to re-run once it is gone.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_CMD="$PROJECT_ROOT/setup/bootstrap.sh"
SETTINGS="$HOME/.claude/settings.json"
EVENTS=("SessionStart")

if ! command -v jq >/dev/null 2>&1; then
    echo "error: jq is required. Install via: brew install jq" >&2
    exit 1
fi

if [[ ! -f "$SETTINGS" ]]; then
    echo "[uninstall-bootstrap-hook] $SETTINGS does not exist; nothing to do"
    exit 0
fi

if ! jq -e . "$SETTINGS" >/dev/null 2>&1; then
    echo "error: $SETTINGS is not valid JSON; refusing to edit." >&2
    exit 1
fi

TMP="$(mktemp -t auto-speech-bootstrap-uninstall-XXXXXX)"
trap 'rm -f "$TMP"' EXIT

# Strip our hook, drop emptied blocks, drop emptied event keys, drop .hooks
# if nothing remains. Same idiom as uninstall-narrator-hooks.sh.
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

echo "[uninstall-bootstrap-hook] removed bootstrap hook from ${EVENTS[*]}"
