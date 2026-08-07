---
description: Disable autoplay for THIS Claude session by removing its enrollment marker. Autoplay is OFF by default, so this only matters after /auto-speech-autoplay-on. To mute every session at once, touch ~/.claude/auto-speech.disabled.
allowed-tools: Bash
---

Run this single Bash command:

```
set -euo pipefail
SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}"
if [ -z "$SESSION_ID" ]; then
    # No session id to scope by — fall back to the global mute marker.
    mkdir -p "$HOME/.claude"
    touch "$HOME/.claude/auto-speech.disabled"
    echo "off-global"
    exit 0
fi
# Autoplay is opt-IN: withdraw this session's enrollment marker.
MARKER="$HOME/.claude/auto-speech-autoplay-enabled/$SESSION_ID"
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
`autoplay was already off for session <id> (it is off by default)`.

If the output is `off-global`, respond:
`no session id — set global mute marker instead (~/.claude/auto-speech.disabled)`.

On any other output, respond with the output verbatim.
