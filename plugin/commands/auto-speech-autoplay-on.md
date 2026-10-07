---
description: Enable autoplay for THIS Claude session. Autoplay is OFF by default; this enrolls the current session so it reads its end-of-turn responses. Other sessions are unaffected. Also clears the global mute.
allowed-tools: Bash
---

Run this single Bash command:

```
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
"$PROJECT_ROOT/bin/auto-speech" autoplay on
```

If the command succeeds and outputs `autoplay enabled for session <id>`, respond with one line:
`autoplay enabled for session <id> (other sessions stay off)`.

If the command fails because `CLAUDE_CODE_SESSION_ID` is not set, respond with one line:
`global mute cleared, but CLAUDE_CODE_SESSION_ID is not set — autoplay is opt-in per session and cannot enroll without it (check that jq is installed; /auto-speech-doctor reports it)`.

On any other output, respond with the output verbatim.
