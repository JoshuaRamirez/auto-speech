---
description: Switch autoplay between reading ALL sessions or just THIS one. Args = (all | solo) or empty to show current scope.
argument-hint: "[all|solo]"
allowed-tools: Bash
---

You are executing `/auto-speech-scope` for the auto-speech plugin.

This toggles the autoplay **spotlight**. Autoplay is OFF unless a session
enrolls (`/auto-speech-autoplay-on`). ALL scope (the default) lets every
enrolled session read; `solo` claims the spotlight for THIS session, so
only this session reads aloud and every other session is muted until you
switch back to `all`. Soloing this session also enrolls it.

## Execution

Run this Bash command, substituting `ARGUMENT` with `$ARGUMENTS` (trimmed, lowercase) or omitting if empty:

```
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
"$PROJECT_ROOT/bin/auto-speech" autoplay scope [ARGUMENT]
```

## Response

- When showing scope (no argument): respond with the command output verbatim on one line.
- `all`: respond `autoplay scope: ALL — every enrolled session reads`.
- `solo`: respond with the command output or `autoplay scope: SOLO — only this session (<id>) reads; all others muted`.
- If error (e.g. no session ID): respond `cannot solo: CLAUDE_CODE_SESSION_ID is not set in this shell`.
