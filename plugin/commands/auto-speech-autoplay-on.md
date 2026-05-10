---
description: Resume autoplay (remove the disable marker).
allowed-tools: Bash
---

Run this single Bash command:

```
if [ -e "$HOME/.claude/auto-speech.disabled" ]; then rm -f "$HOME/.claude/auto-speech.disabled" && echo on; else echo already-on; fi
```

If the output is `on`, respond with one line: "autoplay enabled".
If the output is `already-on`, respond with one line: "autoplay was already enabled".
On any other output, respond with the output verbatim. No additional explanation.
