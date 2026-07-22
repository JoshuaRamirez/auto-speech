---
description: Stop the narrator daemon immediately (independent of the per-project marker).
argument-hint: ""
allowed-tools: Bash
---

You are executing `/auto-speech-narrate-stop` for the auto-speech plugin.

Run this single Bash command and respond with its stdout as a one-line status.

```
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
"$PROJECT_ROOT/plugin/scripts/shell/narrator_service_stop.sh"
```
