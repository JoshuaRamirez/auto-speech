---
description: Disable real-time narration for THIS Claude session — removes the per-session marker. Other sessions keep their settings. Daemon idle-exits.
argument-hint: ""
allowed-tools: Bash
---

You are executing `/auto-speech-narrate-off` for the auto-speech plugin.

Run this single Bash command, then respond with one short status line.

```
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
"$PROJECT_ROOT/bin/auto-speech" narrate off
```

Respond with exactly one line: `narrate off — session <session-id>, <message from stdout>`.

The daemon stays running for `idle_shutdown_seconds` (default 600 s) and
will exit on its own once there are no more events. To stop it immediately,
run `/auto-speech-narrate-stop`.
