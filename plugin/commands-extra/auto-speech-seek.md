---
description: Seek the active playback. Arg = +N / -N / N (absolute seconds) / end.
argument-hint: "+15 | -30 | 120 | end"
allowed-tools: Bash
---

You are executing `/seek $ARGUMENTS`.

Parse the argument:
- `+N` or `-N` (where N is a number) → relative seek by N seconds.
- an integer or decimal with no sign → absolute seek to that many seconds from the start.
- the word `end` → jump to near the end.
- anything else → respond with "seek: bad target '<value>'". Do not proceed.

Run this Bash command, substituting the raw argument in place of `TARGET`:

```
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
"$PROJECT_ROOT/plugin/scripts/shell/run_control.sh" seek TARGET
```

On exit 0 respond with "seeked to TARGET".
On exit 2 respond with "no active playback".
On any other exit respond with "seek failed: exit <code>" and the stderr output.
No additional explanation.
