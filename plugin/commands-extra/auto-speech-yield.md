---
description: Hold speech while another app plays sound — show or set the switch and the exception list, or check who is playing now. Args = status | on | off | add <apps…> | remove <apps…> | clear | check (empty = status).
argument-hint: "[status|on|off|add <apps…>|remove <apps…>|clear|check]"
allowed-tools: Bash
---

You are executing `/auto-speech-yield` for the auto-speech plugin.

By default auto-speech does not start speaking while another app (music, a
video, a call) is playing sound; the speech waits in the queue and plays once
the sound stops. The **exception list** names apps whose sound does not hold
speech, by bundle id (`com.apple.Music`) or name (`Music`, `Spotify`). `off`
switches the behaviour off; `on` restores it. `check` says who is playing
right now and whether speech would hold. The same verbs exist in the MCP tool
`sound_yield` and the file `~/.config/auto-speech/sound-yield.toml`.

## Execution

Run this Bash command, substituting `ARGUMENTS` with `$ARGUMENTS` (trimmed) or omitting it if empty:

```
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
"$PROJECT_ROOT/bin/auto-speech" yield ARGUMENTS
```

## Response

Respond with the command output verbatim inside a fenced code block. Do not
interpret or summarize. If the command fails, show its stderr verbatim.
