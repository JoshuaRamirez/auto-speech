---
description: Name the desk ("Master Desk here,") only when the speaker changes or after a quiet gap — show or set the switch, the gap and hand-set names. Args = status | on | off | gap <seconds> | name <session> <name…> | forget <session> | who [session] (empty = status).
argument-hint: "[status|on|off|gap <seconds>|name <session> <name…>|forget <session>|who [session]]"
allowed-tools: Bash
---

You are executing `/auto-speech-speaker` for the auto-speech plugin.

Several desks speak through one daemon. A line opens with "<desk> here,"
only when the session speaking differs from the last one heard, or when the
last line was heard more than the quiet gap ago (15 minutes by default).
Names come from the desk agents' notes (`session:` → `name:` or the note's
file name); `name` sets one by hand and wins over the notes; a line that
already opens with its own name is not doubled; a session no note names is
announced as "Another desk". The same verbs exist in the MCP tool `speaker`
and in `~/.config/auto-speech/speaker.toml`.

## Execution

Run this Bash command, substituting `ARGUMENTS` with `$ARGUMENTS` (trimmed) or omitting it if empty:

```
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
"$PROJECT_ROOT/bin/auto-speech" speaker ARGUMENTS
```

Report the command's output verbatim.
