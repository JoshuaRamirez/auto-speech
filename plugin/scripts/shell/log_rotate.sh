#!/usr/bin/env bash
# Shared size-capped log rotation for auto-speech shell entrypoints.
#
# In-process daemons rotate via auto_speech_log.get_logger
# (RotatingFileHandler). Shell entrypoints that redirect a child's stdio
# onto a file must rotate it BEFORE the redirect opens it — an in-process
# handler can't coordinate with an inherited append fd. This helper is
# that pre-spawn rotation, and mirrors auto_speech_log.py's policy:
# default cap 5 MiB, 3 backups (.1/.2/.3), oldest dropped. Override the
# cap with AUTO_SPEECH_LOG_MAX_BYTES.
#
# Source this file and call `as_rotate_log <path> [cap_bytes]`.

# shellcheck disable=SC2329  # sourced helper; invoked by callers, not here
as_rotate_log() {
    local f="$1"
    local cap="${2:-${AUTO_SPEECH_LOG_MAX_BYTES:-5242880}}"
    local sz
    sz="$(stat -f %z "$f" 2>/dev/null || echo 0)"
    if [[ "$sz" -ge "$cap" ]]; then
        mv -f "$f.2" "$f.3" 2>/dev/null || true
        mv -f "$f.1" "$f.2" 2>/dev/null || true
        mv -f "$f"   "$f.1" 2>/dev/null || true
    fi
}
