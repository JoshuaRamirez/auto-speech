---
description: Re-enable autoplay for THIS Claude session. Removes the per-session opt-out marker and clears the global mute. Autoplay is ON by default; this undoes a prior /auto-speech-autoplay-off.
allowed-tools: Bash
---

Run this single Bash command:

```
set -euo pipefail
SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}"
# Clear the global mute if set — autoplay-on always means "I want sound".
rm -f "$HOME/.claude/auto-speech.disabled"
if [ -z "$SESSION_ID" ]; then
    echo "on-global-only"
    exit 0
fi
# Autoplay is opt-out: remove this session's opt-out marker if present.
MARKER="$HOME/.claude/auto-speech-autoplay-sessions/$SESSION_ID"
if [ -e "$MARKER" ]; then
    rm -f "$MARKER"
    echo "on $SESSION_ID"
else
    echo "on-already $SESSION_ID"
fi
```

If the output is `on <id>`, respond with one line:
`autoplay re-enabled for session <id> (opt-out marker removed)`.

If the output is `on-already <id>`, respond with one line:
`autoplay was already enabled for session <id> (autoplay is on by default)`.

If the output is `on-global-only`, respond with one line:
`global mute cleared; CLAUDE_CODE_SESSION_ID not set, but autoplay is on by default anyway`.

On any other output, respond with the output verbatim.
