---
description: Restart the active auto-speech playback from the beginning.
allowed-tools: Bash
---

Run this Bash command:

```
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
"$PROJECT_ROOT/plugin/scripts/shell/run_control.sh" restart
```

On exit 0 respond with "restarted from beginning".
On exit 2 respond with "no active playback".
On any other exit respond with "restart failed: exit <code>" and the stderr output.
No additional explanation.
