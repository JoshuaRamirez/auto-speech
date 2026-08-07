---
description: Enable autoplay for THIS Claude session. Autoplay is OFF by default; this enrolls the current session so it reads its end-of-turn responses. Other sessions are unaffected. Also clears the global mute.
allowed-tools: Bash
---

Run this single Bash command:

```
set -euo pipefail
SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}"
# Clear the global mute if set — autoplay-on always means "I want sound".
rm -f "$HOME/.claude/auto-speech.disabled"
if [ -z "$SESSION_ID" ]; then
    echo "no-session-id"
    exit 0
fi
# Autoplay is opt-IN: create this session's enrollment marker.
mkdir -p "$HOME/.claude/auto-speech-autoplay-enabled"
MARKER="$HOME/.claude/auto-speech-autoplay-enabled/$SESSION_ID"
if [ -e "$MARKER" ]; then
    echo "on-already $SESSION_ID"
else
    touch "$MARKER"
    echo "on $SESSION_ID"
fi
```

If the output is `on <id>`, respond with one line:
`autoplay enabled for session <id> (other sessions stay off)`.

If the output is `on-already <id>`, respond with one line:
`autoplay was already enabled for session <id>`.

If the output is `no-session-id`, respond with one line:
`global mute cleared, but CLAUDE_CODE_SESSION_ID is not set — autoplay is opt-in per session and cannot enroll without it (check that jq is installed; /auto-speech-doctor reports it)`.

On any other output, respond with the output verbatim.
