---
description: Show or set the end-of-turn autoplay mode. Args = (verbatim | small | medium | large) or empty to show current.
argument-hint: "[verbatim|small|medium|large]"
allowed-tools: Bash
---

You are executing `/auto-speech-autoplay-mode` for the auto-speech plugin.

## Argument

Parse `$ARGUMENTS` (case-insensitive, trimmed):
- empty / whitespace → action is `show`
- `verbatim` → mode=verbatim, summary_size stays at whatever it was
- `small`    → mode=summary, summary_size=small
- `medium`   → mode=summary, summary_size=medium
- `large`    → mode=summary, summary_size=large
- anything else → respond `autoplay-mode: unknown value <value> (expected verbatim | small | medium | large)` and stop.

## show

If action is `show`, run this Bash command and respond with the resolved
mode + size + config path inside a fenced block:

```
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/plugin/scripts/python/autoplay_config.py"
```

## set

For any set action, run this Bash command with the chosen `NEW_MODE`
and `NEW_SIZE`:

```
set -euo pipefail
CONFIG_DIR="$HOME/.config/auto-speech"
CONFIG_FILE="$CONFIG_DIR/autoplay.toml"
NEW_MODE='<NEW_MODE>'        # "verbatim" or "summary"
NEW_SIZE='<NEW_SIZE>'        # "small" | "medium" | "large" (only used when NEW_MODE=summary)

mkdir -p "$CONFIG_DIR"
cat > "$CONFIG_FILE" <<TOML
[autoplay]
mode = "$NEW_MODE"
summary_size = "$NEW_SIZE"
TOML
echo "wrote $CONFIG_FILE: mode=$NEW_MODE summary_size=$NEW_SIZE"
```

When setting `verbatim`, you still need a value for `NEW_SIZE` (it's
ignored by the rewriter when mode=verbatim, but the config file needs
something valid). Use `small` if the existing config didn't have one
or you can't determine it cheaply — that way switching back to summary
later picks the conventional default.

Respond with one line: `autoplay mode set: <NEW_MODE>` (and append
`/<NEW_SIZE>` when NEW_MODE=summary).
