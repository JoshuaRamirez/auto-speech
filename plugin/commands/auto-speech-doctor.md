---
description: Health check for auto-speech — binaries (mpv, uv, jq), disk headroom, log sizes, narrator daemon, queue depth, autoplay scope. Add `json` for machine-readable output. Exits non-zero when unhealthy.
argument-hint: "[json]"
allowed-tools: Bash
---

You are executing `/auto-speech-doctor` for the auto-speech plugin.

If `$ARGUMENTS` (trimmed, case-insensitive) is `json`, append ` --json` to
the command below; otherwise run it as-is. Respond with the command's full
output verbatim inside a fenced code block — do not summarize or reinterpret.

```
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/plugin/scripts/python/doctor.py"
```

The command exits non-zero when any check is `FAIL` (unhealthy). After
showing the output, if there is a `FAIL`, state the single most actionable
remedy in one line (e.g. `brew install mpv`, or `run setup/install.sh`).
