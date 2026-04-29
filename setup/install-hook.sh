#!/usr/bin/env bash
# auto-speech — install the Stop hook into ~/.claude/settings.json.
# Idempotent: re-running yields exactly one Stop block for our hook.
# Uses jq for safe JSON edits.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_CMD="$PROJECT_ROOT/plugin/scripts/shell/autoplay_hook.sh"
SETTINGS="$HOME/.claude/settings.json"

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
    echo "[install-hook] creating $SETTINGS"
    echo '{}' > "$SETTINGS"
fi

# Validate current contents are JSON before editing.
if ! jq -e . "$SETTINGS" >/dev/null 2>&1; then
    echo "error: $SETTINGS is not valid JSON; refusing to edit. Back it up and fix manually." >&2
    exit 1
fi

# Count existing entries pointing at our hook.
EXISTING_COUNT="$(jq --arg cmd "$HOOK_CMD" '
    (.hooks // {}).Stop // []
    | [.[]?.hooks[]? | select(.type == "command" and .command == $cmd)]
    | length
' "$SETTINGS")"

if [[ "${EXISTING_COUNT:-0}" -ge 1 ]]; then
    echo "[install-hook] hook already present in $SETTINGS"
    exit 0
fi

TMP="$(mktemp -t auto-speech-settings-XXXXXX)"
trap 'rm -f "$TMP"' EXIT

jq --arg cmd "$HOOK_CMD" '
    .hooks //= {} |
    .hooks.Stop = ((.hooks.Stop // []) + [
        { "hooks": [ { "type": "command", "command": $cmd } ] }
    ])
' "$SETTINGS" > "$TMP"

# Atomically replace the settings file.
mv "$TMP" "$SETTINGS"
trap - EXIT

echo "[install-hook] added Stop hook → $HOOK_CMD"
echo "[install-hook] pause without uninstall: touch ~/.claude/auto-speech.disabled"
echo "[install-hook] resume:                   rm   ~/.claude/auto-speech.disabled"
echo "[install-hook] uninstall:                setup/uninstall-hook.sh"
