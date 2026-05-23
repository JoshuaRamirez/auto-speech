---
description: Speak a prior Claude response aloud (local TTS). Arg = N-th most recent (default 1).
argument-hint: "[n]"
allowed-tools: Bash
---

You are executing `/auto-speech-speak` for the auto-speech plugin.

## Argument

Parse `$ARGUMENTS` (case-insensitive, trimmed):
- empty, unset, or whitespace-only → `ORDINAL=1`
- a positive integer → use that value as `ORDINAL`
- anything else → stop and respond with one line:
  `speak: argument must be a positive integer, got <value>`. Do not proceed.

## Single bash pipeline

Run this ONE bash command. It does extract + rewrite + speak end-to-end,
so the executing model doesn't have to construct or pass the audio
rewrite by hand (which was historically a source of bugs — the model
sometimes confused the status template with the rewrite content).
Substitute `ORDINAL`:

```
set -euo pipefail
PROJECT_ROOT=/Users/joshua/Developer/auto-speech
SRC_FILE=$(mktemp -t auto-speech-src-XXXX)
REWRITE_FILE=$(mktemp -t auto-speech-rewrite-XXXX)
trap 'rm -f "$SRC_FILE" "$REWRITE_FILE"' EXIT

# 1. Extract the Nth-most-recent assistant message text from the
#    current session transcript. --exclude-regex skips the slash
#    command's own status echoes (e.g. "spoke message #1 — 28 source
#    chars, 48 rewrite chars") so repeated /auto-speech-speak
#    invocations target the real prior content, not the previous
#    /speak's own status line.
"$PROJECT_ROOT/plugin/scripts/shell/run_extract.sh" \
    --ordinal ORDINAL \
    --exclude-regex '^(spoke message #[0-9]+|speak failed:|speak: argument must)' \
    > "$SRC_FILE"
SRC_CHARS=$(wc -c < "$SRC_FILE" | tr -d ' ')
SOURCE_HASH=$("$PROJECT_ROOT/plugin/scripts/shell/compute_hash.sh" < "$SRC_FILE")

# 2. Rewrite via cli_rewrite.py — verbatim mode preserves all
#    content, expands symbols, and removes markdown. This is the
#    same code path the autoplay uses, so behaviour is identical
#    between manual /auto-speech-speak and end-of-turn autoplay.
"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/plugin/scripts/python/cli_rewrite.py" \
    --mode verbatim --timeout 90 < "$SRC_FILE" > "$REWRITE_FILE"
REWRITE_CHARS=$(wc -c < "$REWRITE_FILE" | tr -d ' ')

# 3. Speak it (TTS render + mpv playback). --source-hash enables the
#    replay cache: subsequent /auto-speech-speak of the same message
#    skips both the rewrite and the TTS pipeline.
"$PROJECT_ROOT/plugin/scripts/shell/run_speak.sh" \
    --ordinal ORDINAL --source-hash "$SOURCE_HASH" < "$REWRITE_FILE"

echo "OK ordinal=ORDINAL src=$SRC_CHARS rewrite=$REWRITE_CHARS hash=${SOURCE_HASH:0:16}"
```

## Reporting

Exit-code handling:

- `0` and stdout matches `OK ordinal=…`: respond with one line:
  `spoke message #ORDINAL — N source chars, M rewrite chars`
  where N is `src` and M is `rewrite` from the OK line.
- `2` (extract: no such turn): respond with the stderr message verbatim.
- `3` (extract: no transcript): respond with the stderr message verbatim.
- Any other non-zero exit: respond with `speak failed: exit <code>`
  and include the failing command's stderr.

Do not output the source text or the rewritten text — the user already
saw the source on screen, and the rewrite is what they're hearing.
