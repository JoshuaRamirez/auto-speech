---
description: Enable autoplay for THIS Claude session. Creates a per-session opt-in marker and clears the global mute. Other sessions without their own marker are silenced.
allowed-tools: Bash
---

Run this single Bash command:

```
set -euo pipefail
SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}"
if [ -z "$SESSION_ID" ]; then
    echo "no-session"
    exit 1
fi
# Clear the global mute if set.
rm -f "$HOME/.claude/auto-speech.disabled"
# Opt this session in. Once ANY session has opted in, the autoplay hook
# switches to strict mode: only opted-in sessions fire.
mkdir -p "$HOME/.claude/auto-speech-autoplay-sessions"
touch "$HOME/.claude/auto-speech-autoplay-sessions/$SESSION_ID"
echo "on $SESSION_ID"
```

If the output is `on <id>`, respond with one line:
`autoplay enabled for session <id> (other sessions silenced unless opted in)`.

If the output is `no-session`, respond with one line:
`autoplay-on: CLAUDE_CODE_SESSION_ID not set; cannot register this session`.

On any other output, respond with the output verbatim.
