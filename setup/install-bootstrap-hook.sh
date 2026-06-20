#!/usr/bin/env bash
# auto-speech — register the self-update bootstrap on SessionStart in
# ~/.claude/settings.json. On each new session it cheaply reconciles the
# venv with the committed uv.lock (a no-op unless the lock changed), so the
# install stays current across machines without manual steps.
#
# Idempotent: re-running yields exactly one SessionStart block for our hook.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_CMD="$PROJECT_ROOT/setup/bootstrap.sh"
SETTINGS="$HOME/.claude/settings.json"
EVENTS=("SessionStart")

if ! command -v jq >/dev/null 2>&1; then
    echo "error: jq is required. Install via: brew install jq" >&2
    exit 1
fi

if [[ ! -x "$HOOK_CMD" ]]; then
    echo "error: bootstrap script missing or not executable: $HOOK_CMD" >&2
    exit 1
fi

mkdir -p "$HOME/.claude"
if [[ ! -f "$SETTINGS" ]]; then
    echo "[install-bootstrap-hook] creating $SETTINGS"
    echo '{}' > "$SETTINGS"
fi

if ! jq -e . "$SETTINGS" >/dev/null 2>&1; then
    echo "error: $SETTINGS is not valid JSON; refusing to edit." >&2
    exit 1
fi

TMP="$(mktemp -t auto-speech-bootstrap-install-XXXXXX)"
trap 'rm -f "$TMP"' EXIT

# Append a block containing our hook iff it is not already present under
# .hooks[$event]. Same reduce idiom as install-narrator-hooks.sh.
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

echo "[install-bootstrap-hook] registered ${EVENTS[*]} → $HOOK_CMD"
echo "[install-bootstrap-hook] sync log: /tmp/auto-speech-sync.log"
echo "[install-bootstrap-hook] uninstall: setup/uninstall-bootstrap-hook.sh"
