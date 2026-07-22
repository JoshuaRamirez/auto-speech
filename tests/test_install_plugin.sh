#!/usr/bin/env bash
# Contract tests for setup/install-plugin.sh (+ the uninstall root cleanup).
#
# Strategy: point HOME at a temp dir so the real installer runs against a
# sandbox ~/.claude/commands and ~/.config/auto-speech. Verifies:
#   - curated install → exactly the 8 keep-set symlinks
#   - the recorded project root file is written and points at this clone
#   - installed command blocks resolve PROJECT_ROOT from that file (the
#     doctor block actually executes against the sandbox root file)
#   - --with-extras → 21 links; plain re-run preserves the opted-in extras
#   - prune: a stale link into plugin/commands/ (not in keep-set) is
#     removed; a dangling link into plugin/commands-extra/ is removed
#   - uninstall.sh removes the recorded root file
#   - no command file carries a hardcoded absolute project path

set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$TESTS_DIR/.." && pwd)"
INSTALL="$PROJECT_ROOT/setup/install-plugin.sh"

failures=0; ran=0
ok()   { ran=$((ran+1)); printf '  ok  %s\n' "$1"; }
fail() { ran=$((ran+1)); failures=$((failures+1)); printf '  FAIL %s: %s\n' "$1" "$2"; }

SANDBOX="$(mktemp -d -t auto-speech-install-test-XXXXXX)"
trap 'rm -rf "$SANDBOX"' EXIT

# --- curated install --------------------------------------------------------
HOME="$SANDBOX" bash "$INSTALL" >/dev/null 2>&1
n="$(find "$SANDBOX/.claude/commands" -name 'auto-speech-*.md' | wc -l | tr -d ' ')"
if [[ "$n" == "8" ]]; then ok "curated install links exactly 8 commands"
else fail "curated install" "expected 8 links, got $n"; fi

root_file="$SANDBOX/.config/auto-speech/root"
if [[ -f "$root_file" && "$(cat "$root_file")" == "$PROJECT_ROOT" ]]; then
    ok "root file recorded with the clone path"
else fail "root file" "missing or wrong content: $(cat "$root_file" 2>/dev/null)"; fi

# --- a real command block runs against the recorded root --------------------
# Execute the doctor command's fenced block with HOME pointed at the sandbox;
# it must resolve PROJECT_ROOT and reach the real doctor.py (any exit status
# proves resolution — an unresolved root exits with the guard message).
block="$(awk '/^```/{f=!f;next} f' "$PROJECT_ROOT/plugin/commands/auto-speech-doctor.md")"
out="$(HOME="$SANDBOX" bash -c "$block" 2>&1)" || true
if [[ "$out" == *"not configured"* ]]; then
    fail "doctor block resolves root" "guard fired despite root file: $out"
else
    ok "doctor block resolves root from ~/.config/auto-speech/root"
fi

# Guard path: with the root file absent the block must fail with the message.
rm "$root_file"
out="$(HOME="$SANDBOX" bash -c "$block" 2>&1)" && guard_rc=0 || guard_rc=$?
if [[ $guard_rc -ne 0 && "$out" == *"not configured"* ]]; then
    ok "guard fires when root file is absent"
else fail "guard" "expected non-zero + message, rc=$guard_rc out=$out"; fi
HOME="$SANDBOX" bash "$INSTALL" >/dev/null 2>&1   # restore root file

# --- extras + idempotency ---------------------------------------------------
HOME="$SANDBOX" bash "$INSTALL" --with-extras >/dev/null 2>&1
n="$(find "$SANDBOX/.claude/commands" -name 'auto-speech-*.md' | wc -l | tr -d ' ')"
if [[ "$n" == "21" ]]; then ok "--with-extras links 21 commands"
else fail "--with-extras" "expected 21 links, got $n"; fi

HOME="$SANDBOX" bash "$INSTALL" >/dev/null 2>&1
n="$(find "$SANDBOX/.claude/commands" -name 'auto-speech-*.md' | wc -l | tr -d ' ')"
if [[ "$n" == "21" ]]; then ok "plain re-run preserves opted-in extras"
else fail "extras preservation" "expected 21 links after re-run, got $n"; fi

# --- prune tiers ------------------------------------------------------------
ln -s "$PROJECT_ROOT/plugin/commands/auto-speech-bogus.md" \
    "$SANDBOX/.claude/commands/auto-speech-bogus.md"
ln -s "$PROJECT_ROOT/plugin/commands-extra/auto-speech-gone.md" \
    "$SANDBOX/.claude/commands/auto-speech-gone.md"
HOME="$SANDBOX" bash "$INSTALL" >/dev/null 2>&1
if [[ ! -L "$SANDBOX/.claude/commands/auto-speech-bogus.md" ]]; then
    ok "prune removes non-keep-set links into plugin/commands/"
else fail "prune tier 1" "bogus curated-dir link survived"; fi
if [[ ! -L "$SANDBOX/.claude/commands/auto-speech-gone.md" ]]; then
    ok "prune removes dangling links into plugin/commands-extra/"
else fail "prune tier 2" "dangling extras link survived"; fi

# --- uninstall removes the root file ---------------------------------------
# uninstall.sh pkills the web server / mpv; stub out pgrep+pkill via PATH so
# the test never touches real processes (tests must not stop live playback).
mkdir -p "$SANDBOX/bin"
printf '#!/bin/sh\nexit 1\n' > "$SANDBOX/bin/pgrep"
printf '#!/bin/sh\nexit 0\n' > "$SANDBOX/bin/pkill"
chmod +x "$SANDBOX/bin/pgrep" "$SANDBOX/bin/pkill"
mkdir -p "$SANDBOX/tmp"
HOME="$SANDBOX" PATH="$SANDBOX/bin:$PATH" AUTO_SPEECH_TMP_ROOT="$SANDBOX/tmp" \
    bash "$PROJECT_ROOT/setup/uninstall.sh" >/dev/null 2>&1 || true
if [[ ! -f "$root_file" ]]; then ok "uninstall removes the recorded root file"
else fail "uninstall root" "root file still present"; fi

# --- portability invariant --------------------------------------------------
if grep -rq "/Users/joshua" "$PROJECT_ROOT/plugin/commands" \
        "$PROJECT_ROOT/plugin/commands-extra" 2>/dev/null; then
    fail "portability" "hardcoded /Users/joshua path present in command files"
else
    ok "no hardcoded absolute project paths in command files"
fi

echo
echo "install-plugin: $((ran - failures))/$ran passed"
[[ $failures -eq 0 ]] || exit 1
