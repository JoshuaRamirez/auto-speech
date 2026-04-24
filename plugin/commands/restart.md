---
description: Restart the active auto-speech playback from the beginning.
allowed-tools: Bash
---

Run this Bash command:

```
/Users/joshua/Developer/auto-speech/plugin/scripts/shell/run_control.sh restart
```

On exit 0 respond with "restarted from beginning".
On exit 2 respond with "no active playback".
On any other exit respond with "restart failed: exit <code>" and the stderr output.
No additional explanation.
