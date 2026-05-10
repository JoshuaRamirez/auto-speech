---
description: Pause autoplay (create the disable marker).
allowed-tools: Bash
---

Run this single Bash command:

```
mkdir -p "$HOME/.claude" && touch "$HOME/.claude/auto-speech.disabled" && echo off
```

If the output is `off`, respond with one line: "autoplay paused".
On any other output, respond with the output verbatim. No additional explanation.
