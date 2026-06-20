#!/usr/bin/env bash
# Idempotency tests for setup/install-bootstrap-hook.sh and its uninstaller.
#
# Same strategy as test_install_idempotent.sh: point HOME at a temp dir so we
# exercise the real settings.json writes without touching the user's config.
# Verifies:
#   - fresh install registers exactly one SessionStart block for our hook
#   - install run twice stays at exactly one (no duplication)
#   - install preserves a foreign SessionStart hook
#   - uninstall removes ONLY ours, keeps foreign, drops the empty slot
#   - uninstall on already-clean settings leaves valid JSON

set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$TESTS_DIR/.." && pwd)"
INSTALL="$PROJECT_ROOT/setup/install-bootstrap-hook.sh"
UNINSTALL="$PROJECT_ROOT/setup/uninstall-bootstrap-hook.sh"
HOOK_CMD="$PROJECT_ROOT/setup/bootstrap.sh"
EVENT="SessionStart"

failures=0; ran=0
ok()   { ran=$((ran+1)); printf '  ok  %s\n' "$1"; }
fail() { ran=$((ran+1)); failures=$((failures+1)); printf '  FAIL %s: %s\n' "$1" "$2"; }

count_ours() {
    jq --arg cmd "$HOOK_CMD" --arg event "$EVENT" '
        [.hooks[$event][]? | .hooks[]? | select(.command == $cmd)] | length
    ' "$1" 2>/dev/null
}

# ---- Case 1: fresh install → exactly one ----
TMP_HOME="$(mktemp -d -t bootstrap-idem-XXXX)"
HOME="$TMP_HOME" bash "$INSTALL" >/dev/null 2>&1
settings="$TMP_HOME/.claude/settings.json"
n=$(count_ours "$settings")
if [[ "$n" == "1" ]]; then ok "fresh install: 1 SessionStart entry"
else fail "fresh install" "expected 1 got $n"; fi
rm -rf "$TMP_HOME"

# ---- Case 2: install twice is idempotent ----
TMP_HOME="$(mktemp -d -t bootstrap-idem-XXXX)"
HOME="$TMP_HOME" bash "$INSTALL" >/dev/null 2>&1
HOME="$TMP_HOME" bash "$INSTALL" >/dev/null 2>&1
n=$(count_ours "$TMP_HOME/.claude/settings.json")
if [[ "$n" == "1" ]]; then ok "double install stays at 1"
else fail "double install" "expected 1 got $n"; fi
rm -rf "$TMP_HOME"

# ---- Case 3: install preserves a foreign SessionStart hook ----
TMP_HOME="$(mktemp -d -t bootstrap-idem-XXXX)"
mkdir -p "$TMP_HOME/.claude"
cat > "$TMP_HOME/.claude/settings.json" <<'JSON'
{
  "hooks": {
    "SessionStart": [
      { "hooks": [{"type": "command", "command": "/some/other/plugin/start.sh"}] }
    ]
  }
}
JSON
HOME="$TMP_HOME" bash "$INSTALL" >/dev/null 2>&1
foreign=$(jq '[.hooks.SessionStart[]? | .hooks[]? | select(.command == "/some/other/plugin/start.sh")] | length' "$TMP_HOME/.claude/settings.json")
ours=$(count_ours "$TMP_HOME/.claude/settings.json")
if [[ "$foreign" == "1" && "$ours" == "1" ]]; then ok "install preserves foreign SessionStart hook"
else fail "preserve-foreign" "foreign=$foreign ours=$ours"; fi
rm -rf "$TMP_HOME"

# ---- Case 4: uninstall removes ours, keeps foreign ----
TMP_HOME="$(mktemp -d -t bootstrap-idem-XXXX)"
mkdir -p "$TMP_HOME/.claude"
cat > "$TMP_HOME/.claude/settings.json" <<'JSON'
{
  "hooks": {
    "SessionStart": [
      { "hooks": [{"type": "command", "command": "/some/other/plugin/start.sh"}] }
    ]
  }
}
JSON
HOME="$TMP_HOME" bash "$INSTALL" >/dev/null 2>&1
HOME="$TMP_HOME" bash "$UNINSTALL" >/dev/null 2>&1
foreign=$(jq '[.hooks.SessionStart[]? | .hooks[]? | select(.command == "/some/other/plugin/start.sh")] | length' "$TMP_HOME/.claude/settings.json")
ours=$(count_ours "$TMP_HOME/.claude/settings.json")
if [[ "$foreign" == "1" && "$ours" == "0" ]]; then ok "uninstall removes ours, keeps foreign"
else fail "uninstall mixed" "foreign=$foreign ours=$ours"; fi
rm -rf "$TMP_HOME"

# ---- Case 5: uninstall on clean settings is a no-op leaving valid JSON ----
TMP_HOME="$(mktemp -d -t bootstrap-idem-XXXX)"
mkdir -p "$TMP_HOME/.claude"
echo '{}' > "$TMP_HOME/.claude/settings.json"
HOME="$TMP_HOME" bash "$UNINSTALL" >/dev/null 2>&1
if jq -e . "$TMP_HOME/.claude/settings.json" >/dev/null 2>&1; then ok "uninstall on clean leaves valid JSON"
else fail "uninstall-clean" "produced invalid JSON"; fi
rm -rf "$TMP_HOME"

echo
echo "bootstrap-hook idempotency: $ran ran, $failures failed"
exit $failures
