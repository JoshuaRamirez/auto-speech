# Phase 2 Micro-Design — Transcript Extraction

## Scope
Locate the active session's JSONL, read it, and return the Nth-most-recent
`AssistantMessage` (defined as an assistant turn containing at least one
non-empty text content block).

## M1 — Classes
- `AssistantMessage` (frozen dataclass)
- `TranscriptReader` (stream JSONL → parsed dicts)
- `TranscriptLocator` (resolve the JSONL path for the current session)
- `MessageSelector` (pick the Nth-most-recent qualifying turn)

## M2 — Semantics

### AssistantMessage
Holds the already-concatenated text of a single assistant turn along with its
ordinal (1 = most recent) and timestamp.

### TranscriptReader
`iter_lines(path: Path) -> Iterator[dict]`. No filtering; just decode each line.

### TranscriptLocator
`locate(cwd: Path | None) -> Path`. Strategy:
1. If `CLAUDE_SESSION_ID` env var is set, prefer `<slug>/<CLAUDE_SESSION_ID>.jsonl`
   under `~/.claude/projects/`.
2. Otherwise, return the most recently modified `.jsonl` inside the slug dir
   derived from `cwd` (or current working directory if `cwd` is None).
3. If neither exists → raise `TranscriptNotFoundError`.

Slug derivation rule: `str(cwd).replace('/', '-')`. In practice the macOS
absolute path starts with `/`, so the slug starts with `-`.

### MessageSelector
`select(path: Path, n: int) -> AssistantMessage`. Reads the JSONL, filters to
qualifying assistant turns, and returns the `n`-th from the end (1-indexed).
Raises `NoSuchAssistantTurn` if fewer than `n` qualifying turns exist.

## M3 — Relationships
```
TranscriptLocator ── path ──► MessageSelector ── uses ──► TranscriptReader
                                   │
                                   └── constructs ──► AssistantMessage
```

## M4 — Interface & implementation decisions

### JSONL schema (from observation of a real file)
```
{ "type": "assistant", "timestamp": "...",
  "message": {
    "role": "assistant",
    "content": [
      {"type": "text", "text": "..."},
      {"type": "thinking", ...},
      {"type": "tool_use", ...},
      ...
    ]
  }, ... }
```
Qualifying assistant turn: at least one content block with `type == "text"`
and non-empty (post-strip) `text`.

Text extraction: concatenate all `text`-type blocks' text, joined by newline.
This preserves multi-block assistant responses (rare but possible).

### Ordinal counting
Count only qualifying turns. A pure-tool-use turn doesn't advance the counter.
This keeps `/speak 2` intuitively mean "the message before the most recent one"
from the user's perspective.

### Memory
For a live Claude Code session the JSONL can be ~10 MB. We read line by line
via TranscriptReader, but MessageSelector must scan to the end before it can
answer "Nth-most-recent". For v1 we load the qualifying turns' texts into a
ring buffer of size `n` (so we only retain what we might return). This is
O(file) time, O(n) memory — far more efficient than loading everything.

## Failure modes
- `TranscriptNotFoundError` — UC-07 exit code 3.
- `NoSuchAssistantTurn` — UC-06 exit code 2.

## Check gate
From the live session (this one), the script must:
1. Print the slug-derived directory → matches
   `/Users/joshua/.claude/projects/-Users-joshua-Developer-auto-speech/`.
2. Pick up `0c99956f-...jsonl` via "newest JSONL" fallback.
3. Return the most-recent assistant text → matches what I see at the top of
   this very turn when I run it from here (mod the current turn, which is
   still in flight at invocation time).
