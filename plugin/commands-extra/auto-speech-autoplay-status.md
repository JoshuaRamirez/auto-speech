---
description: Show autoplay status — per-session enrollment marker, enrollment dir, global disable, currently-playing mpv, log tail. Autoplay is OFF by default (opt-IN).
allowed-tools: Bash
---

Run this single Bash command and respond with its full output verbatim
inside a fenced code block. Do not interpret or summarize.

```
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
"$PROJECT_ROOT/plugin/scripts/shell/autoplay_status.sh"
```
