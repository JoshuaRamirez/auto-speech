---
description: Health check for auto-speech — binaries, disk headroom, log sizes, narrator daemon, queue depth, autoplay scope. Add `json` for machine-readable output. Exits non-zero when unhealthy.
argument-hint: "[json]"
allowed-tools: Bash
---

You are executing `/auto-speech-doctor` for the auto-speech plugin.

If `$ARGUMENTS` (trimmed, case-insensitive) is `json`, append ` --json` to
the command below; otherwise run it as-is. Respond with the command's full
output verbatim inside a fenced code block — do not summarize or reinterpret.

```
/Users/joshua/Developer/auto-speech/.venv/bin/python /Users/joshua/Developer/auto-speech/plugin/scripts/python/doctor.py
```

The command exits non-zero when any check is `FAIL` (unhealthy). After
showing the output, if there is a `FAIL`, state the single most actionable
remedy in one line (e.g. `brew install mpv`, or `run setup/install.sh`).
