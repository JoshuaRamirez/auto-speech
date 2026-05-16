#!/usr/bin/env bash
# Idempotency tests for setup/install-narrator-hooks.sh and
# setup/uninstall-narrator-hooks.sh.
#
# Strategy: point HOME at a temp dir so we exercise the real scripts'
# settings.json writes without touching the user's actual config.
# Verifies:
#   - install creates the hooks dict + four event entries on a fresh settings
#   - install run TWICE doesn't duplicate the entries (the "exactly one
#     block per event" guarantee)
#   - install preserves any existing PostToolUse hook entries (we don't
#     stomp on other plugins)
#   - uninstall removes ONLY our entries and cleanly drops empty event
#     lists / the hooks dict entirely

set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$TESTS_DIR/.." && pwd)"
INSTALL="$PROJECT_ROOT/setup/install-narrator-hooks.sh"
UNINSTALL="$PROJECT_ROOT/setup/uninstall-narrator-hooks.sh"
HOOK_CMD="$PROJECT_ROOT/plugin/scripts/shell/narrator_hook.sh"

failures=0; ran=0
ok()   { ran=$((ran+1)); printf '  ok  %s\n' "$1"; }
fail() { ran=$((ran+1)); failures=$((failures+1)); printf '  FAIL %s: %s\n' "$1" "$2"; }

count_entries() {
    local settings="$1"
    local event="$2"
    jq --arg cmd "$HOOK_CMD" --arg event "$event" '
        [.hooks[$event][]? | .hooks[]? | select(.command == $cmd)] | length
    ' "$settings" 2>/dev/null
}

# ---- Case 1: fresh install creates four event entries ----
TMP_HOME="$(mktemp -d -t install-idem-XXXX)"
HOME="$TMP_HOME" bash "$INSTALL" >/dev/null 2>&1
settings="$TMP_HOME/.claude/settings.json"
for event in PreToolUse PostToolUse Stop UserPromptSubmit; do
    n=$(count_entries "$settings" "$event")
    if [[ "$n" == "1" ]]; then ok "fresh install: 1 entry under $event"
    else fail "fresh install: $event" "expected 1 got $n"; fi
done
rm -rf "$TMP_HOME"

# ---- Case 2: install twice is idempotent ----
TMP_HOME="$(mktemp -d -t install-idem-XXXX)"
HOME="$TMP_HOME" bash "$INSTALL" >/dev/null 2>&1
HOME="$TMP_HOME" bash "$INSTALL" >/dev/null 2>&1
settings="$TMP_HOME/.claude/settings.json"
for event in PreToolUse PostToolUse Stop UserPromptSubmit; do
    n=$(count_entries "$settings" "$event")
    if [[ "$n" == "1" ]]; then ok "double install stays at 1: $event"
    else fail "double install $event" "expected 1 got $n"; fi
done
rm -rf "$TMP_HOME"

# ---- Case 3: install preserves a foreign PostToolUse entry ----
TMP_HOME="$(mktemp -d -t install-idem-XXXX)"
mkdir -p "$TMP_HOME/.claude"
cat > "$TMP_HOME/.claude/settings.json" <<'JSON'
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [{"type": "command", "command": "/some/other/plugin/hook.sh"}]
      }
    ]
  }
}
JSON
HOME="$TMP_HOME" bash "$INSTALL" >/dev/null 2>&1
foreign_present=$(jq '[.hooks.PostToolUse[]? | .hooks[]? | select(.command == "/some/other/plugin/hook.sh")] | length' "$TMP_HOME/.claude/settings.json")
ours_present=$(count_entries "$TMP_HOME/.claude/settings.json" "PostToolUse")
if [[ "$foreign_present" == "1" && "$ours_present" == "1" ]]; then
    ok "install preserves foreign PostToolUse hook"
else
    fail "preserve-foreign" "foreign=$foreign_present ours=$ours_present"
fi
rm -rf "$TMP_HOME"

# ---- Case 4: uninstall removes our entries but keeps foreign ones ----
TMP_HOME="$(mktemp -d -t install-idem-XXXX)"
mkdir -p "$TMP_HOME/.claude"
cat > "$TMP_HOME/.claude/settings.json" <<'JSON'
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [{"type": "command", "command": "/some/other/plugin/hook.sh"}]
      }
    ]
  }
}
JSON
HOME="$TMP_HOME" bash "$INSTALL" >/dev/null 2>&1
HOME="$TMP_HOME" bash "$UNINSTALL" >/dev/null 2>&1
foreign_after=$(jq '[.hooks.PostToolUse[]? | .hooks[]? | select(.command == "/some/other/plugin/hook.sh")] | length' "$TMP_HOME/.claude/settings.json")
ours_after=$(count_entries "$TMP_HOME/.claude/settings.json" "PostToolUse")
pre_count=$(count_entries "$TMP_HOME/.claude/settings.json" "PreToolUse")
if [[ "$foreign_after" == "1" && "$ours_after" == "0" && "$pre_count" == "0" ]]; then
    ok "uninstall removes ours, keeps foreign"
else
    fail "uninstall mixed" "foreign=$foreign_after ours=$ours_after pre=$pre_count"
fi
rm -rf "$TMP_HOME"

# ---- Case 5: uninstall on already-clean settings is a no-op ----
TMP_HOME="$(mktemp -d -t install-idem-XXXX)"
mkdir -p "$TMP_HOME/.claude"
echo '{}' > "$TMP_HOME/.claude/settings.json"
HOME="$TMP_HOME" bash "$UNINSTALL" >/dev/null 2>&1
if jq -e . "$TMP_HOME/.claude/settings.json" >/dev/null 2>&1; then
    ok "uninstall on clean settings leaves valid JSON"
else
    fail "uninstall-clean" "produced invalid JSON"
fi
rm -rf "$TMP_HOME"

echo
echo "install/uninstall idempotency: $ran ran, $failures failed"
exit $failures
