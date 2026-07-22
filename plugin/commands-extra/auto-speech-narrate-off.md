---
description: Disable real-time narration for THIS Claude session — removes the per-session marker. Other sessions keep their settings. Daemon idle-exits.
argument-hint: ""
allowed-tools: Bash
---

You are executing `/auto-speech-narrate-off` for the auto-speech plugin.

Run this single Bash command, then respond with one short status line.

```
set -euo pipefail
SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}"

if [[ -z "$SESSION_ID" ]]; then
    echo "narrate off: CLAUDE_CODE_SESSION_ID not set; nothing to remove"
    exit 0
fi

MARKER="$HOME/.claude/auto-speech-narrate-sessions/$SESSION_ID"
if [[ -e "$MARKER" ]]; then
    rm -f "$MARKER"
    echo "removed: $MARKER"
else
    echo "marker already absent: $MARKER"
fi

# Also remove the deprecated per-project marker if present.
if [[ -e "$PWD/.claude/narrate.enabled" ]]; then
    rm -f "$PWD/.claude/narrate.enabled"
    echo "(also removed legacy per-project marker)"
fi
```

Respond with exactly one line: `narrate off — session <session-id>, <message from stdout>`.

The daemon stays running for `idle_shutdown_seconds` (default 600 s) and
will exit on its own once there are no more events. To stop it immediately,
run `/auto-speech-narrate-stop`.
