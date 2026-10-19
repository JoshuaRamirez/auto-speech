---
description: Hold speech while a player app plays sound — show or set the switch, the players list and the exception list, or check who is playing now. Args = status | on | off | hold-for players|any | player-add <apps…> | player-remove <apps…> | player-reset | add <apps…> | remove <apps…> | clear | check (empty = status).
argument-hint: "[status|on|off|hold-for players|any|player-add <apps…>|player-remove <apps…>|player-reset|add <apps…>|remove <apps…>|clear|check]"
allowed-tools: Bash
---

You are executing `/auto-speech-yield` for the auto-speech plugin.

By default auto-speech does not start speaking while a **player** app (Music,
Spotify, a browser, Zoom, the ChatGPT app, ...) is playing sound; the speech
waits in the queue and plays once the sound stops. An unknown app's open
stream never holds (Core Audio cannot tell a silent stream from a quiet one).
`player-add` / `player-remove` / `player-reset` edit the players list;
`hold-for any` holds for every app instead. The **exception list** names apps
whose sound never holds speech, by bundle id (`com.apple.Music`) or name
(`Music`, `Spotify`). `off` switches the behaviour off; `on` restores it.
`check` says who is playing right now and whether speech would hold. The same verbs exist in the MCP tool
`sound_yield` and the file `~/.config/auto-speech/sound-yield.toml`. A held
line is never dropped: past the longest wait it goes to the missed list
(`/auto-speech-missed`).

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
