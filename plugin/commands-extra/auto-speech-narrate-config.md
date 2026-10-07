---
description: Show, edit, or print the path of the active narrator config file.
argument-hint: "[edit|show|path]"
allowed-tools: Bash
---

You are executing `/auto-speech-narrate-config` for the auto-speech plugin.

## Execution

Parse `$ARGUMENTS`:
- empty or "show" → action is `show` (default)
- "edit" → action is `edit`
- "path" → action is `path`
- anything else → respond `config: unknown action <value> (expected show|edit|path)` and stop.

Run this Bash command, substituting `ACTION` with the chosen action:

```
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
"$PROJECT_ROOT/bin/auto-speech" narrate config ACTION
```

## Response

- `path` → respond with the single line containing the path verbatim.
- `show` → respond with the command output inside a fenced code block.
- `edit` → respond with the output confirmation.
