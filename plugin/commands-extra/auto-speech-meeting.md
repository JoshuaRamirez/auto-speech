---
description: His own meeting mode — speech is set aside as missed while it is on, and one line afterwards says how many lines were missed. Args = status | on (60 min) | off | for <45m|2h|1h30m> (8 h at most); empty = status.
argument-hint: "[status|on|off|for <duration>]"
allowed-tools: Bash
---

You are executing `/auto-speech-meeting` for the auto-speech plugin.

While he is in a meeting auto-speech does not talk. A meeting app holding
the microphone (Teams, Zoom, FaceTime, a browser on Meet, Slack, Webex,
Discord) is detected by itself, with no permission and no audio captured;
this command is his own switch for the rest. `on` holds for 60 minutes,
`for 45m` / `for 2h` / `for 1h30m` for that long (8 h at most), and the mode
switches itself off at the end, so a forgotten `on` never mutes him for
good. `off` ends it now. Every line asked for meanwhile goes straight to
the missed list (`/auto-speech-missed`), nothing is dropped, and when the
meeting ends one spoken line says how many were missed. The same verb exists
in the MCP tool `sound_yield` (`action = "meeting"`, the words in `apps`).

## Execution

Run this Bash command, substituting `ARGUMENTS` with `$ARGUMENTS` (trimmed) or omitting it if empty:

```
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
"$PROJECT_ROOT/bin/auto-speech" yield meeting ARGUMENTS
```

## Response

Respond with the command output verbatim inside a fenced code block. Do not
interpret or summarize. If the command fails, show its stderr verbatim.
