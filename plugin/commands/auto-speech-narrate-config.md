---
description: Show or open the active narrator config file for editing.
argument-hint: "[edit|show|path]"
allowed-tools: Bash
---

You are executing `/auto-speech-narrate-config` for the auto-speech plugin.

## Argument

Parse `$ARGUMENTS`:
- empty or "show" → action is `show` (default)
- "edit" → action is `edit`
- "path" → action is `path`
- anything else → respond `config: unknown action <value> (expected show|edit|path)` and stop.

## Step 1 — Resolve the active config path

Run this Bash command and capture the resolved `config_path`:

```
/Users/joshua/Developer/auto-speech/.venv/bin/python /Users/joshua/Developer/auto-speech/plugin/scripts/python/narrator_config.py
```

The JSON output's `config_path` field is the file currently in use.
If `config_path` is null, no config has been loaded — the plugin is
using hardcoded defaults. Tell the user this and suggest running
`/auto-speech-narrate-install` to create one.

## Step 2 — Act on the path

- `path` → respond with one line: `<config_path>`.
- `show` → cat the file and return its contents inside a fenced toml block.
- `edit` → tell the user to open `<config_path>` in their editor. Do not
  try to launch an editor yourself; describe the path and the key fields
  they can edit (provider, model, max_tokens, silence_seconds).

After any edit, the next phase summary will pick up the new config when
the daemon next loads its summarizer (model swap requires `/auto-speech-narrate-stop`
followed by `/auto-speech-narrate-on` to force a reload).
