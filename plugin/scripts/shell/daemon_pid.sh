#!/usr/bin/env bash
# Shared PID-identity guard for the narrator daemon.
#
# A bare `kill -0 <pid>` only proves SOME process owns that number — after a
# crash the OS may recycle the pid for an unrelated process. Trusting the
# number alone risks a false "already running" (blocking restart) or, worse,
# `kill`ing an unrelated process on stop. This confirms the live pid is
# actually OUR daemon by matching its command line, mirroring
# _pid_is_our_daemon() in narrator_service.py. The leading slash avoids
# matching the test runner (.../test_narrator_service.py).
#
# Source this file and call `narrator_pid_is_ours <pid>`.

# shellcheck disable=SC2329  # sourced helper; invoked by callers, not here
narrator_pid_is_ours() {
    local pid="${1:-}"
    [[ -n "$pid" ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    ps -p "$pid" -o command= 2>/dev/null | grep -q "/narrator_service.py"
}
