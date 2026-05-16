---
description: Disable real-time narration in this project — removes the marker. Daemon keeps running until idle-shutdown.
argument-hint: ""
allowed-tools: Bash
---

You are executing `/auto-speech-narrate-off` for the auto-speech plugin.

Run this single Bash command, then respond with one short status line.

```
set -euo pipefail
MARKER="$PWD/.claude/narrate.enabled"
if [[ -e "$MARKER" ]]; then
    rm -f "$MARKER"
    echo "removed: $MARKER"
else
    echo "marker already absent: $MARKER"
fi
```

Respond with exactly one line: `narrate off — <message from stdout>`.
The daemon stays running for `idle_shutdown_seconds` (default 600s) and
will exit on its own once there are no more events. To stop it immediately,
run `/auto-speech-narrate-stop`.
