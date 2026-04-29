---
description: Launch the auto-speech localhost web app at http://127.0.0.1:7860/ (idempotent).
allowed-tools: Bash
---

Run this single Bash command:

```
/Users/joshua/Developer/auto-speech/plugin/scripts/shell/start_webapp.sh
```

The command prints exactly two lines to stdout: a status word and the URL.

Read the first line and respond with one line:

- If the status is `already-running` → respond:
  "auto-speech web app already running at http://127.0.0.1:7860/"
- If the status is `started` → respond:
  "auto-speech web app started at http://127.0.0.1:7860/"
- If the status is `started-but-not-responsive` → respond:
  "auto-speech web app starting — not yet responsive. See /tmp/auto-speech-webapp.log"
- Anything else → respond with the raw two lines verbatim.

No additional explanation.
