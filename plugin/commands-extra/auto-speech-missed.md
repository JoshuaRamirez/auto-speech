---
description: Speech held or set aside while another app played sound — list it, replay it (oldest first, over any other sound), or clear it. Args = (empty) | --play | --clear.
argument-hint: "[--play|--clear]"
allowed-tools: Bash
---

You are executing `/auto-speech-missed` for the auto-speech plugin.

A line auto-speech held for another app's sound is never dropped. While it
waits it is **held**; past the longest wait (180 s by default), or when it
turns out to be stale backlog once the sound stops, it is **missed** and
stays on this list until it plays. With no argument the command lists the
lines, oldest first. `--play` replays the missed ones in that order, over
any other sound, and each leaves the list once it has played. `--clear`
forgets them all. The same verbs exist in the MCP tool `missed`
(`action = list | play | clear`).

## Execution

Run this Bash command, substituting `ARGUMENTS` with `$ARGUMENTS` (trimmed) or omitting it if empty:

```
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
"$PROJECT_ROOT/bin/auto-speech" missed ARGUMENTS
```

## Response

Respond with the command output verbatim inside a fenced code block. Do not
interpret or summarize. If the command fails, show its stderr verbatim.
