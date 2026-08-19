---
description: Switch autoplay between reading ALL sessions or just THIS one. Args = (all | solo) or empty to show current scope.
argument-hint: "[all|solo]"
allowed-tools: Bash
---

You are executing `/auto-speech-scope` for the auto-speech plugin.

This toggles the autoplay **spotlight**. Autoplay is OFF unless a session
enrolls (`/auto-speech-autoplay-on`). ALL scope (the default) lets every
enrolled session read; `solo` claims the spotlight for THIS session, so
only this session reads aloud and every other session is muted until you
switch back to `all`. Soloing this session also enrolls it.

## Argument

Parse `$ARGUMENTS` (case-insensitive, trimmed):
- empty / whitespace → action is `show`
- `all` → scope=all (every enrolled session reads; the default)
- `solo` | `this` | `one` | `only` → scope=solo (only THIS session reads)
- anything else → respond `scope: unknown value <value> (expected all | solo)` and stop.

## show

Run this Bash command and respond with its output verbatim on one line:

```
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
CLAUDE_CODE_SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}" "$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/plugin/scripts/python/autoplay_scope.py"
```

## set all

Run:

```
set -euo pipefail
rm -f "$HOME/.claude/auto-speech-autoplay-solo"
echo "scope-all"
```

Respond with one line: `autoplay scope: ALL — every enrolled session reads`.

## set solo

Run:

```
set -euo pipefail
SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}"
if [ -z "$SESSION_ID" ]; then
    echo "no-session-id"
    exit 0
fi
mkdir -p "$HOME/.claude"
printf '%s' "$SESSION_ID" > "$HOME/.claude/auto-speech-autoplay-solo"
# Soloing THIS session implies it should actually read. Autoplay is
# opt-IN, so enrol this session — without a marker the spotlight would
# name a session that still cannot play.
mkdir -p "$HOME/.claude/auto-speech-autoplay-enabled"
touch "$HOME/.claude/auto-speech-autoplay-enabled/$SESSION_ID"
echo "scope-solo $SESSION_ID"
```

If the output is `scope-solo <id>`, respond with one line:
`autoplay scope: SOLO — only this session (<id>) reads; all others muted`.

If the output is `no-session-id`, respond with one line:
`cannot solo: CLAUDE_CODE_SESSION_ID is not set in this shell`.

On any other output, respond with the output verbatim.
