---
description: Play a line from the speech history again (the lines the speech player lists). Arg = Nth-most-recent (default 1).
argument-hint: "[n]"
allowed-tools: Bash
---

You are executing the `/replay` slash command for the `auto-speech` plugin.

## Argument
The user invoked `/replay $ARGUMENTS`. Parse the argument:
- If empty, unset, or whitespace-only → `ORDINAL=1`.
- Else trim whitespace. If it's a positive integer → use that value as `ORDINAL`.
- Else → stop and respond with a one-line error:
  "replay: argument must be a positive integer, got `<value>`". Do not proceed.

## Step 1 — Play the history line

Run this single Bash command, substituting `ORDINAL`:

```
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
"$PROJECT_ROOT/bin/auto-speech" replay --ordinal ORDINAL
```

`/replay` does NOT consult the current session transcript. It reads the
speech history (the lines the speech player lists, newest first) and, with
the narrator running, queues the line through it like the player's own play
button. It works in any Claude Code session.

If the command exits with code `2` (the speech history is off, empty, or
holds fewer lines than asked), stop and pass the stderr message to the user
verbatim.

## Step 2 — Report

When the command returns:
- Exit 0: respond with a single line: "replayed history line #ORDINAL".
- Exit 2: respond with the stderr message verbatim.
- Any other exit: respond with "replay failed: exit <code>" and include the
  stderr output.

Do not explain what you did beyond the single status line.
