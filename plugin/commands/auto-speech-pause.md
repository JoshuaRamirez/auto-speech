---
description: Pause the active auto-speech playback.
allowed-tools: Bash
---

Run this Bash command:

```
/Users/joshua/Developer/auto-speech/plugin/scripts/shell/run_control.sh pause
```

On exit 0 respond with "paused".
On exit 2 respond with "no active playback".
On any other exit respond with "pause failed: exit <code>" and the stderr output.
No additional explanation.
