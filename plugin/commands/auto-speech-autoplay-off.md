---
description: Disable autoplay for THIS Claude session. Removes the per-session opt-in marker. Other sessions keep their own settings. To globally mute all sessions, use the disable marker (touch ~/.claude/auto-speech.disabled).
allowed-tools: Bash
---

Run this single Bash command:

```
set -euo pipefail
SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}"
if [ -z "$SESSION_ID" ]; then
    # No session id to scope by — fall back to the global mute marker,
    # which matches the historical /auto-speech-autoplay-off behaviour.
    mkdir -p "$HOME/.claude"
    touch "$HOME/.claude/auto-speech.disabled"
    echo "off-global"
    exit 0
fi
# Remove the per-session marker. Other opted-in sessions are unaffected.
MARKER="$HOME/.claude/auto-speech-autoplay-sessions/$SESSION_ID"
if [ -e "$MARKER" ]; then
    rm -f "$MARKER"
    echo "off $SESSION_ID"
else
    echo "off-already $SESSION_ID"
fi
```

If the output is `off <id>`, respond:
`autoplay disabled for session <id> (other sessions unchanged)`.

If the output is `off-already <id>`, respond:
`autoplay was already disabled for session <id>`.

If the output is `off-global`, respond:
`no session id — set global mute marker instead (~/.claude/auto-speech.disabled)`.

On any other output, respond with the output verbatim.
