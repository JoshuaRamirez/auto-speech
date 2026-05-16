---
description: Enable real-time narration in this project — touches the narrate marker and starts the daemon.
argument-hint: ""
allowed-tools: Bash
---

You are executing `/auto-speech-narrate-on` for the auto-speech plugin.

Run this single Bash command, then respond with one short status line.

```
set -euo pipefail
PROJECT_ROOT="/Users/joshua/Developer/auto-speech"
MARKER="$PWD/.claude/narrate.enabled"

mkdir -p "$PWD/.claude"
touch "$MARKER"

"$PROJECT_ROOT/plugin/scripts/shell/narrator_service_start.sh"
echo "marker: $MARKER"
```

Respond with exactly: `narrate on — marker at <path>, daemon <pid or status>`.
On failure, surface the stderr verbatim and stop.
