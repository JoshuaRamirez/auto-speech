# Phase 8 Micro-Design — Plugin Integration

## Scope
Wire the slash command `/speak` into Claude Code and make it discoverable.

## M1 — Artifacts
- `plugin/.claude-plugin/plugin.json` — manifest
- `plugin/commands/speak.md` — slash command body (rewrite prompt + invocation)
- `plugin/scripts/python/extract_message.py` — CLI that prints Nth message text
- `plugin/scripts/shell/run_extract.sh` — venv-activating wrapper for extract
- `plugin/scripts/shell/run_speak.sh` — venv-activating wrapper for speak.py
- `setup/install-plugin.sh` — symlink-based user-level install

## M2 — Semantics
- **speak.md** is the prompt the slash command sends to Claude. Structured
  into four steps: argument parsing, source extraction, rewrite, invocation.
- **run_extract.sh** and **run_speak.sh** exist so the slash command can call
  the Python scripts without knowing the venv path.
- **install-plugin.sh** symlinks the project's command file into
  `~/.claude/commands/speak.md` — user-level command.

## M3 — Dataflow
```
User types "/speak 2"
    │
    ▼
Claude reads speak.md as the prompt
    │ parses $ARGUMENTS → ORDINAL=2
    ▼
Claude runs run_extract.sh --ordinal 2
    │ stdout: SOURCE_TEXT (verbatim message)
    ▼
Claude rewrites SOURCE_TEXT per the 12 rules → AUDIO_TEXT
    │
    ▼
Claude runs run_speak.sh --ordinal 2 <<EOF ... AUDIO_TEXT ... EOF
    │ pipeline runs: plan → produce → consume → afplay
    ▼
User hears audio, Claude reports "spoke message #2 (N chars)"
```

## M4 — Install decisions

- **User-level command (symlink)** chosen over full plugin marketplace for v1.
  Symlinked install means editing the project propagates live. Marketplace
  packaging is a future task when distribution is desired.
- **stderr discipline** in `transcript_locator.py` fixed so
  `run_extract.sh`'s stdout is exclusively the message text.
- **Heredoc in speak.md** uses `__AUTO_SPEECH_EOF__` (non-trivial, unlikely to
  collide with rewritten text).
- **Absolute paths** — RESOLVED (v0.1.0 release prep): command files no
  longer hard-code the clone path. Each bash block resolves `PROJECT_ROOT`
  at runtime from `~/.config/auto-speech/root`, which `install-plugin.sh`
  writes (and rewrites on re-run, so a moved clone self-heals). Contract
  pinned by `tests/test_install_plugin.sh`.

## Check gate
- `/speak` invoked in a live Claude Code session plays the most-recent
  assistant message.
- `/speak 2` plays the second-most-recent.
- `/speak 99` (when there aren't 99 messages) produces a clean error line.
- Malformed arg (`/speak abc`) produces a clean argument-validation error.

Phase 9 will capture outputs under `tests/outputs/` for each case.
