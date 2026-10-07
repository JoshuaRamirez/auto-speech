---
description: Show or set the end-of-turn autoplay mode. Args = (raw | verbatim | small | medium | large) or empty to show current.
argument-hint: "[raw|verbatim|small|medium|large]"
allowed-tools: Bash
---

You are executing `/auto-speech-autoplay-mode` for the auto-speech plugin.

## Execution

Run this Bash command, substituting `ARGUMENT` with `$ARGUMENTS` (trimmed, lowercase) or omitting if empty:

```
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
"$PROJECT_ROOT/bin/auto-speech" autoplay mode [ARGUMENT]
```

## Response

- When showing mode (no argument): respond with the command's output inside a fenced code block.
- When setting mode: respond with the command's status output (e.g. `autoplay mode set: <NEW_MODE>[/<NEW_SIZE>]`).
