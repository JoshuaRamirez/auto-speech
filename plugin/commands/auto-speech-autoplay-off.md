---
description: Disable autoplay for THIS Claude session by removing its enrollment marker. Autoplay is OFF by default, so this only matters after /auto-speech-autoplay-on. To mute every session at once, touch ~/.claude/auto-speech.disabled.
allowed-tools: Bash
---

Run this single Bash command:

```
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
"$PROJECT_ROOT/bin/auto-speech" autoplay off
```

If the output contains `autoplay disabled for session <id>`, respond:
`autoplay disabled for session <id> (other sessions unchanged)`.

If the output contains `set global mute marker instead`, respond:
`no session id — set global mute marker instead (~/.claude/auto-speech.disabled)`.

On any other output, respond with the output verbatim.
