---
description: Replay a previously-spoken message from the audio cache. Arg = Nth-most-recent (default 1).
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

## Step 1 — Play the cached entry

Run this single Bash command, substituting `ORDINAL`:

```
/Users/joshua/Developer/auto-speech/plugin/scripts/shell/run_replay.sh --ordinal ORDINAL
```

`/replay` does NOT consult the current session transcript. It only reads
from the cache directory at `config/cache/`. This means it works in any
Claude Code session, not just the one where the original `/speak` ran.

If the command exits with code `2` ("no cache entries" or
"only K entries; you asked for Nth"), stop and pass the stderr message
to the user verbatim.

## Step 2 — Report

When the command returns:
- Exit 0: respond with a single line: "replayed cached entry #ORDINAL".
- Exit 2: respond with the stderr message verbatim.
- Any other exit: respond with "replay failed: exit <code>" and include the
  stderr output.

Do not explain what you did beyond the single status line.
