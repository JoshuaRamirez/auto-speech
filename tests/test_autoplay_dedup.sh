#!/usr/bin/env bash
# Unit tests for the already_playing_same_hash helper in autoplay_worker.sh.
#
# We can't source the full worker (it has top-level side effects), so we
# reproduce the helper inline here and exercise its branches against
# controlled marker files and PIDs. If the worker's helper diverges from
# this copy, the test below will be silently wrong — protect against
# that by also asserting the worker file still contains the function
# definition with the expected predicates.

set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$TESTS_DIR/.." && pwd)"
WORKER="$PROJECT_ROOT/plugin/scripts/shell/autoplay_worker.sh"

failures=0
ran=0

ok()   { ran=$((ran+1)); printf '  ok  %s\n' "$1"; }
fail() { ran=$((ran+1)); failures=$((failures+1)); printf '  FAIL %s: %s\n' "$1" "$2"; }

# ---- Anchor: worker file contains the function we're emulating ----
if ! grep -q 'already_playing_same_hash()' "$WORKER"; then
    fail "anchor:function-exists" "already_playing_same_hash() not found in $WORKER"
fi
if ! grep -q 'NOW_PLAYING_MARKER' "$WORKER"; then
    fail "anchor:marker-name" "NOW_PLAYING_MARKER not referenced in $WORKER"
fi
# The 120-second cap is part of the contract; pin it.
if ! grep -qE '\(\(\s*age\s*<\s*120\s*\)\)' "$WORKER"; then
    fail "anchor:120s-cap" "120-second age cap not present in $WORKER"
fi
ok "anchor:worker-file-still-has-helper-and-cap"

# ---- Helper copy under test ----
NOW_PLAYING_MARKER="$(mktemp -t auto-speech-dedup-test-XXXXXX)"
rm -f "$NOW_PLAYING_MARKER"  # remove so default state is "absent"

already_playing_same_hash() {
    [[ -f "$NOW_PLAYING_MARKER" ]] || return 1
    local current_hash
    current_hash="$(cat "$NOW_PLAYING_MARKER" 2>/dev/null || true)"
    [[ "$current_hash" == "$SOURCE_HASH" ]] || return 1
    local marker_mtime now age
    marker_mtime="$(stat -f %m "$NOW_PLAYING_MARKER" 2>/dev/null || echo 0)"
    now="$(date +%s)"
    age=$(( now - marker_mtime ))
    (( age < 120 )) || return 1
    local mpv_pid
    mpv_pid="$(cat "$FAKE_MPV_PID_FILE" 2>/dev/null || true)"
    [[ -n "${mpv_pid:-}" ]] && kill -0 "$mpv_pid" 2>/dev/null
}

# Need a substitute for /tmp/auto-speech/mpv.pid that we control.
FAKE_MPV_PID_FILE="$(mktemp -t auto-speech-dedup-fake-pid-XXXXXX)"
trap 'rm -f "$NOW_PLAYING_MARKER" "$FAKE_MPV_PID_FILE"' EXIT

SOURCE_HASH="abc123"

# ---- Case 1: marker absent → not playing ----
if already_playing_same_hash; then
    fail "marker-absent" "returned true; expected false"
else
    ok "marker-absent-returns-not-playing"
fi

# ---- Case 2: marker present with non-matching hash → not playing ----
echo "different-hash" > "$NOW_PLAYING_MARKER"
if already_playing_same_hash; then
    fail "different-hash" "returned true; expected false"
else
    ok "different-hash-returns-not-playing"
fi

# ---- Case 3: matching hash, but no mpv → not playing ----
echo "$SOURCE_HASH" > "$NOW_PLAYING_MARKER"
echo "" > "$FAKE_MPV_PID_FILE"
if already_playing_same_hash; then
    fail "no-mpv" "returned true; expected false"
else
    ok "no-mpv-returns-not-playing"
fi

# ---- Case 4: matching hash, mpv pid = our own shell ($$, definitely
# alive AND signal-able by current user) → IS playing ----
echo "$SOURCE_HASH" > "$NOW_PLAYING_MARKER"
echo "$$" > "$FAKE_MPV_PID_FILE"
if already_playing_same_hash; then
    ok "matching-hash-and-live-mpv-returns-playing"
else
    fail "matching-hash-and-live-mpv" "returned false; expected true"
fi

# ---- Case 5: matching hash, mpv alive, BUT marker is older than 120 s → not playing ----
echo "$SOURCE_HASH" > "$NOW_PLAYING_MARKER"
# Backdate the marker by 200 seconds.
touch -t "$(date -v-200S +%Y%m%d%H%M.%S)" "$NOW_PLAYING_MARKER"
echo "1" > "$FAKE_MPV_PID_FILE"
if already_playing_same_hash; then
    fail "stale-marker" "returned true; expected false (marker > 120s old)"
else
    ok "stale-marker-falls-through"
fi

# ---- Case 6: matching hash, mpv pid is a definitely-dead PID → not playing ----
echo "$SOURCE_HASH" > "$NOW_PLAYING_MARKER"
touch "$NOW_PLAYING_MARKER"  # refresh mtime so it's not stale
# PID 999999 should be safely-non-existent.
echo "999999" > "$FAKE_MPV_PID_FILE"
if already_playing_same_hash; then
    fail "dead-mpv-pid" "returned true; expected false"
else
    ok "dead-mpv-pid-falls-through"
fi

echo
echo "autoplay dedup helper: $ran ran, $failures failed"
exit $failures
