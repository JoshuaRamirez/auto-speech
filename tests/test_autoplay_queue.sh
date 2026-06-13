#!/usr/bin/env bash
# Unit tests for the FIFO playback-queue helpers in autoplay_worker.sh
# (enqueue_ticket / ticket_is_head) and the opt-out gating in
# autoplay_hook.sh.
#
# Like test_autoplay_dedup.sh, we can't source the full worker (top-level
# side effects), so we reproduce the helpers inline and anchor-grep the
# real files so a divergence shows up as an anchor failure.

set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$TESTS_DIR/.." && pwd)"
WORKER="$PROJECT_ROOT/plugin/scripts/shell/autoplay_worker.sh"
HOOK="$PROJECT_ROOT/plugin/scripts/shell/autoplay_hook.sh"

failures=0
ran=0

ok()   { ran=$((ran+1)); printf '  ok  %s\n' "$1"; }
fail() { ran=$((ran+1)); failures=$((failures+1)); printf '  FAIL %s: %s\n' "$1" "$2"; }

# ---- Anchors: worker has the queue machinery, kill path is gone ----
grep -q 'enqueue_ticket()' "$WORKER" || fail "anchor:enqueue" "enqueue_ticket() missing in worker"
grep -q 'ticket_is_head()' "$WORKER" || fail "anchor:head" "ticket_is_head() missing in worker"
grep -q 'wait_for_queue_turn()' "$WORKER" || fail "anchor:wait" "wait_for_queue_turn() missing in worker"
grep -q 'wait_for_mpv_idle' "$WORKER" && fail "anchor:old-wait" "old wait_for_mpv_idle still present in worker"
grep -q 'already_playing_same_hash()' "$WORKER" || fail "anchor:dedup-kept" "literal-duplicate dedup must be kept"
ok "anchor:worker-queue-helpers-present"

# ---- Anchors: hook is opt-OUT, not opt-in strict mode ----
grep -q 'SESSION_OPTOUT_DIR' "$HOOK" || fail "anchor:hook-optout" "SESSION_OPTOUT_DIR missing in hook"
grep -q 'SESSION_OPTIN_DIR' "$HOOK" && fail "anchor:hook-optin-gone" "old SESSION_OPTIN_DIR still present in hook"
ok "anchor:hook-opt-out-semantics"

# ---- Helper copies under test ----
QUEUE_DIR="$(mktemp -d -t auto-speech-queue-test-XXXXXX)"
trap 'rm -rf "$QUEUE_DIR"' EXIT
QUEUE_TICKET=""

enqueue_ticket() {
    mkdir -p "$QUEUE_DIR"
    local stamp
    stamp="$(python3 -c 'import time; print(f"{time.time_ns():020d}")' 2>/dev/null || date +%s)"
    QUEUE_TICKET="$QUEUE_DIR/$stamp.$$"
    printf '%d\n' $$ > "$QUEUE_TICKET"
}

ticket_is_head() {
    local t owner
    for t in "$QUEUE_DIR"/*; do
        [[ -e "$t" ]] || return 0
        if [[ "$t" == "$QUEUE_TICKET" ]]; then
            return 0
        fi
        owner="$(cat "$t" 2>/dev/null || true)"
        if [[ -z "${owner:-}" ]] || ! kill -0 "$owner" 2>/dev/null; then
            rm -f "$t"
            continue
        fi
        return 1
    done
    return 0
}

# ---- Case 1: empty queue → our (unenqueued) ticket is head ----
QUEUE_TICKET="$QUEUE_DIR/does-not-exist"
if ticket_is_head; then
    ok "empty-queue-is-head"
else
    fail "empty-queue" "expected head on empty queue"
fi

# ---- Case 2: only our ticket → head ----
enqueue_ticket
if ticket_is_head; then
    ok "sole-ticket-is-head"
else
    fail "sole-ticket" "expected head with only our ticket"
fi

# ---- Case 3: an OLDER live ticket (owner = our own pid, alive) blocks us ----
OLDER="$QUEUE_DIR/00000000000000000001.999"
printf '%d\n' $$ > "$OLDER"
if ticket_is_head; then
    fail "older-live-ticket" "expected NOT head behind an older live ticket"
else
    ok "older-live-ticket-blocks"
fi

# ---- Case 4: older ticket with a DEAD owner is garbage-collected ----
printf '%d\n' 999999 > "$OLDER"   # PID 999999: safely non-existent
if ticket_is_head; then
    if [[ -e "$OLDER" ]]; then
        fail "dead-owner-gc" "dead-owner ticket not removed"
    else
        ok "dead-owner-ticket-collected-and-head"
    fi
else
    fail "dead-owner" "expected head after dead-owner GC"
fi

# ---- Case 5: a NEWER live ticket does not block us ----
NEWER="$QUEUE_DIR/99999999999999999999.42"
printf '%d\n' $$ > "$NEWER"
if ticket_is_head; then
    ok "newer-ticket-does-not-block"
else
    fail "newer-ticket" "expected head ahead of a newer ticket"
fi
rm -f "$NEWER"

# ---- Case 6: tickets sort by arrival (lexicographic = chronological) ----
QUEUE_TICKET=""
enqueue_ticket
FIRST="$QUEUE_TICKET"
enqueue_ticket
SECOND="$QUEUE_TICKET"
if [[ "$(basename "$FIRST")" < "$(basename "$SECOND")" ]]; then
    ok "tickets-sort-in-arrival-order"
else
    fail "ticket-order" "second ticket does not sort after first ($FIRST vs $SECOND)"
fi
rm -f "$FIRST" "$SECOND"

# ---- Functional: hook exits silently for an opted-out session ----
# The hook needs jq to parse session_id; without it the opt-out gate is
# skipped and the hook would spawn a real worker — skip in that case.
if ! command -v jq >/dev/null 2>&1; then
    echo
    echo "autoplay queue helpers: $ran ran, $failures failed (hook tests skipped: no jq)"
    exit $failures
fi
FAKE_HOME="$(mktemp -d -t auto-speech-hook-test-XXXXXX)"
mkdir -p "$FAKE_HOME/.claude/auto-speech-autoplay-sessions"
touch "$FAKE_HOME/.claude/auto-speech-autoplay-sessions/test-session-1"
PAYLOAD='{"session_id":"test-session-1","transcript_path":"/nonexistent.jsonl"}'
if HOME="$FAKE_HOME" bash "$HOOK" <<<"$PAYLOAD" >/dev/null 2>&1; then
    # Opted-out session: hook must NOT have left a beacon (it exits
    # before beacon creation), proving it bailed at the opt-out gate.
    if [[ -e "/tmp/auto-speech-last-stop.test-session-1" ]]; then
        fail "hook-optout" "hook proceeded past the opt-out gate (beacon written)"
        rm -f "/tmp/auto-speech-last-stop.test-session-1"
    else
        ok "hook-bails-for-opted-out-session"
    fi
else
    fail "hook-optout-exit" "hook exited non-zero for opted-out session"
fi

# ---- Functional: global mute still silences everything ----
touch "$FAKE_HOME/.claude/auto-speech.disabled"
PAYLOAD2='{"session_id":"test-session-2","transcript_path":"/nonexistent.jsonl"}'
if HOME="$FAKE_HOME" bash "$HOOK" <<<"$PAYLOAD2" >/dev/null 2>&1; then
    if [[ -e "/tmp/auto-speech-last-stop.test-session-2" ]]; then
        fail "hook-global-mute" "hook proceeded despite global mute (beacon written)"
        rm -f "/tmp/auto-speech-last-stop.test-session-2"
    else
        ok "hook-bails-on-global-mute"
    fi
else
    fail "hook-global-mute-exit" "hook exited non-zero under global mute"
fi
rm -rf "$FAKE_HOME"

echo
echo "autoplay queue helpers: $ran ran, $failures failed"
exit $failures
