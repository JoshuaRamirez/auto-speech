#!/usr/bin/env bash
# Anchor + functional tests for the autoplay worker SHIM and the hook.
#
# The worker logic now lives in Python (autoplay_worker.py); the bash
# worker is a thin shim. These anchors pin the shim contract (execs the
# Python worker, preserves the 3 positional args) and the hook's opt-out
# semantics. The functional cases prove the hook still bails for an
# opted-out session and under global mute.

set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$TESTS_DIR/.." && pwd)"
WORKER="$PROJECT_ROOT/plugin/scripts/shell/autoplay_worker.sh"
HOOK="$PROJECT_ROOT/plugin/scripts/shell/autoplay_hook.sh"
WORKER_PY="$PROJECT_ROOT/plugin/scripts/python/autoplay_worker.py"

failures=0
ran=0
ok()   { ran=$((ran+1)); printf '  ok  %s\n' "$1"; }
fail() { ran=$((ran+1)); failures=$((failures+1)); printf '  FAIL %s: %s\n' "$1" "$2"; }

# ---- Shim anchors: thin, execs the Python worker, 3-arg contract ----
grep -q 'autoplay_worker.py' "$WORKER" || fail "anchor:shim-py" "shim does not reference autoplay_worker.py"
grep -q 'exec .*WORKER_PY' "$WORKER" || fail "anchor:shim-exec" "shim does not exec the Python worker"
grep -q 'BEACON_MTIME="\${1:-0}"' "$WORKER" || fail "anchor:arg1" "shim arg1 (beacon_mtime) contract changed"
grep -q 'TRANSCRIPT_PATH="\${2:-}"' "$WORKER" || fail "anchor:arg2" "shim arg2 (transcript_path) contract changed"
grep -q 'SESSION_ID="\${3:-}"' "$WORKER" || fail "anchor:arg3" "shim arg3 (session_id) contract changed"
# The queue/dedup machinery must have MOVED OUT of the shim into Python.
grep -q 'enqueue_ticket()' "$WORKER" && fail "anchor:no-queue-in-shim" "queue logic still in the bash shim"
[[ -f "$WORKER_PY" ]] || fail "anchor:py-exists" "autoplay_worker.py missing"
ok "anchor:shim-is-thin-and-execs-python"

# ---- Hook anchors: opt-IN semantics (autoplay is OFF by default) ----
grep -q 'SESSION_ENROLL_DIR' "$HOOK" || fail "anchor:hook-optin" "SESSION_ENROLL_DIR missing in hook"
grep -q 'SESSION_OPTOUT_DIR' "$HOOK" && fail "anchor:hook-optout-gone" "opt-out gating still present in hook"
ok "anchor:hook-opt-in-semantics"

if ! command -v jq >/dev/null 2>&1; then
    echo
    echo "autoplay shim: $ran ran, $failures failed (hook tests skipped: no jq)"
    exit $failures
fi

# ---- Functional: hook exits silently for a session that never enrolled ----
# This is the DEFAULT state — no marker anywhere — so it is the case that
# matters most: a fresh session must stay quiet until asked to speak.
FAKE_HOME="$(mktemp -d -t auto-speech-hook-test-XXXXXX)"
mkdir -p "$FAKE_HOME/.claude"
PAYLOAD='{"session_id":"test-session-1","transcript_path":"/nonexistent.jsonl"}'
if HOME="$FAKE_HOME" bash "$HOOK" <<<"$PAYLOAD" >/dev/null 2>&1; then
    if [[ -e "/tmp/auto-speech-last-stop.test-session-1" ]]; then
        fail "hook-optin" "hook proceeded for a session that never enrolled (beacon written)"
        rm -f "/tmp/auto-speech-last-stop.test-session-1"
    else
        ok "hook-bails-for-unenrolled-session"
    fi
else
    fail "hook-optin-exit" "hook exited non-zero for unenrolled session"
fi

# ---- Functional: an ENROLLED session gets past the gate ----
# Guard the spawned worker with an unreachable min-length so it bails
# before any rewrite or audio, whatever the transcript heuristic finds.
mkdir -p "$FAKE_HOME/.claude/auto-speech-autoplay-enabled"
touch "$FAKE_HOME/.claude/auto-speech-autoplay-enabled/test-session-3"
PAYLOAD3='{"session_id":"test-session-3","transcript_path":"/nonexistent.jsonl"}'
if HOME="$FAKE_HOME" AUTO_SPEECH_AUTOPLAY_MIN_LEN=99999999 \
        bash "$HOOK" <<<"$PAYLOAD3" >/dev/null 2>&1; then
    if [[ -e "/tmp/auto-speech-last-stop.test-session-3" ]]; then
        ok "hook-proceeds-for-enrolled-session"
        rm -f "/tmp/auto-speech-last-stop.test-session-3"
    else
        fail "hook-optin-enrolled" "hook bailed for an ENROLLED session (no beacon)"
    fi
else
    fail "hook-optin-enrolled-exit" "hook exited non-zero for enrolled session"
fi

# ---- Functional: global mute overrides an ENROLLED session ----
# Enrolment is required for this to test anything: an unenrolled session
# bails at the opt-in gate, which would pass this case vacuously.
touch "$FAKE_HOME/.claude/auto-speech.disabled"
touch "$FAKE_HOME/.claude/auto-speech-autoplay-enabled/test-session-2"
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
echo "autoplay shim: $ran ran, $failures failed"
exit $failures
