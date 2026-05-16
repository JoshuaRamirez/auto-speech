---
description: Show, edit, or print the path of the active narrator config file.
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

Run this Bash command:

```
/Users/joshua/Developer/auto-speech/.venv/bin/python /Users/joshua/Developer/auto-speech/plugin/scripts/python/narrator_config.py
```

Parse its JSON output; `config_path` is the file currently in use.
If `config_path` is null, no config has been loaded — tell the user
this and suggest running `/auto-speech-narrate-install` to create one.

## Step 2 — Act on the path

- `path` → respond with one line containing the path verbatim.

- `show` → cat the file and return its contents inside a fenced ```toml block.

- `edit` → resolve the editor preference in this order:
    1. `$EDITOR` env var
    2. `$VISUAL` env var
    3. fall back to `open -t` (macOS — opens in TextEdit)
  Then run:
  ```
  "$EDITOR_RESOLVED" "<config_path>"
  ```
  If the command exits cleanly, respond:
  `opened <config_path> in <editor> — restart daemon with /auto-speech-narrate-stop then /auto-speech-narrate-on to apply.`

  If no editor is available, fall back to the previous instruction-only
  behaviour: tell the user to open `<config_path>` manually.
