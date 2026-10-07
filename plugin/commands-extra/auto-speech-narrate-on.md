---
description: Enable real-time narration for THIS Claude session — touches a per-session marker and starts the daemon. Other sessions in the same project are NOT affected.
argument-hint: ""
allowed-tools: Bash
---

You are executing `/auto-speech-narrate-on` for the auto-speech plugin.

Run this single Bash command, then respond with one short status line.

```
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
"$PROJECT_ROOT/bin/auto-speech" narrate on
```

Respond with exactly one line: `narrate on — session <session-id>, daemon running`.
On failure, surface the stderr verbatim and stop.
