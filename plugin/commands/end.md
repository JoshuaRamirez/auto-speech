---
description: Stop the active auto-speech playback.
allowed-tools: Bash
---

Run this Bash command:

```
/Users/joshua/Developer/auto-speech/plugin/scripts/shell/run_control.sh end
```

On exit 0 respond with "playback ended".
On exit 2 respond with "no active playback".
On any other exit respond with "end failed: exit <code>" and the stderr output.
No additional explanation.
