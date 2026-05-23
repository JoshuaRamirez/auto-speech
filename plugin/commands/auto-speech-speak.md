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

Run this ONE bash command. It does extract + rewrite + speak end-to-end.
All subprocess noise (extractor traces, cli_rewrite logs, TTS pipeline
chatter, mpv startup messages) is redirected to a per-run log file and
only surfaced on failure — on success the tool result is a single
`OK ...` line. Substitute `ORDINAL`:

```
set -uo pipefail
PROJECT_ROOT=/Users/joshua/Developer/auto-speech
SRC_FILE=$(mktemp -t auto-speech-src-XXXX)
REWRITE_FILE=$(mktemp -t auto-speech-rewrite-XXXX)
LOG=$(mktemp -t auto-speech-speak-log-XXXX)
trap 'rm -f "$SRC_FILE" "$REWRITE_FILE" "$LOG"' EXIT

dump_log_and_fail() {
    local rc="$1"
    local step="$2"
    echo "speak failed at step $step (exit $rc); last 30 log lines:" >&2
    tail -30 "$LOG" >&2 || true
    exit "$rc"
}

# 1. Extract the Nth-most-recent assistant message text. --exclude-regex
#    skips this slash command's own status echoes so repeated
#    /auto-speech-speak invocations target the real prior content.
"$PROJECT_ROOT/plugin/scripts/shell/run_extract.sh" \
    --ordinal ORDINAL \
    --exclude-regex '^(spoke message #[0-9]+|speak failed:|speak: argument must)' \
    > "$SRC_FILE" 2>>"$LOG" \
    || dump_log_and_fail $? extract
SRC_CHARS=$(wc -c < "$SRC_FILE" | tr -d ' ')
SOURCE_HASH=$("$PROJECT_ROOT/plugin/scripts/shell/compute_hash.sh" < "$SRC_FILE" 2>>"$LOG") \
    || dump_log_and_fail $? hash

# 2. Rewrite via cli_rewrite.py (verbatim mode) — same code path as
#    end-of-turn autoplay. Stderr to log.
"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/plugin/scripts/python/cli_rewrite.py" \
    --mode verbatim --timeout 90 < "$SRC_FILE" > "$REWRITE_FILE" 2>>"$LOG" \
    || dump_log_and_fail $? rewrite
REWRITE_CHARS=$(wc -c < "$REWRITE_FILE" | tr -d ' ')

# 3. Speak (TTS + mpv). Both stdout and stderr to log — nothing on
#    stdout we'd want to see.
"$PROJECT_ROOT/plugin/scripts/shell/run_speak.sh" \
    --ordinal ORDINAL --source-hash "$SOURCE_HASH" < "$REWRITE_FILE" >>"$LOG" 2>&1 \
    || dump_log_and_fail $? speak

echo "OK ordinal=ORDINAL src=$SRC_CHARS rewrite=$REWRITE_CHARS hash=${SOURCE_HASH:0:16}"
```

## Reporting

Exit-code handling:

- `0` and stdout matches `OK ordinal=…`: respond with one line:
  `spoke message #ORDINAL — N source chars, M rewrite chars`
  where N is `src` and M is `rewrite` from the OK line.
- Any non-zero exit: stderr will contain `speak failed at step <name>
  (exit <code>); last 30 log lines: …`. Respond with one line:
  `speak failed at <step> (exit <code>)`. Do NOT echo the log lines
  unless the user asked for diagnostics.

Do not output the source text or the rewritten text. The user already
saw the source on screen, and the rewrite is what they're hearing.
