---
description: Enable real-time narration for THIS Claude session — touches a per-session marker and starts the daemon. Other sessions in the same project are NOT affected.
argument-hint: ""
allowed-tools: Bash
---

You are executing `/auto-speech-narrate-on` for the auto-speech plugin.

Run this single Bash command, then respond with one short status line.

```
set -euo pipefail
PROJECT_ROOT="/Users/joshua/Developer/auto-speech"
SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}"

if [[ -z "$SESSION_ID" ]]; then
    echo "narrate on: CLAUDE_CODE_SESSION_ID not set; cannot register session"
    exit 1
fi

MARKER_DIR="$HOME/.claude/auto-speech-narrate-sessions"
MARKER="$MARKER_DIR/$SESSION_ID"
mkdir -p "$MARKER_DIR"
touch "$MARKER"

# Clean up the deprecated per-project marker if present — the old
# scheme couldn't distinguish concurrent sessions in the same project.
if [[ -e "$PWD/.claude/narrate.enabled" ]]; then
    rm -f "$PWD/.claude/narrate.enabled"
    echo "(cleaned legacy per-project marker)"
fi

"$PROJECT_ROOT/plugin/scripts/shell/narrator_service_start.sh"
echo "session: $SESSION_ID"
echo "marker:  $MARKER"
```

Respond with exactly one line: `narrate on — session <session-id>, daemon <pid or status>`.
On failure, surface the stderr verbatim and stop.
