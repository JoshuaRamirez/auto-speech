# ADR-003 — Transcript source & extraction strategy

**Status:** Accepted
**Date:** 2026-04-23

## Context
The `/speak` command needs access to a specific prior Claude response. Within a
slash command we do not have a stable API to the in-memory conversation history
of Claude Code, but Claude Code *does* write every session to a JSONL transcript
at a predictable filesystem location.

## Decision
**Read the session transcript from `~/.claude/projects/<cwd-slug>/<session-id>.jsonl`.**

Use the following strategy to locate the right file, in priority order:
1. If the environment exposes the active session id (`CLAUDE_SESSION_ID` or similar),
   use `<slug>/<session_id>.jsonl` directly.
2. Otherwise, find the most recently modified `.jsonl` under the slug directory
   and use it. In a live `/speak` invocation this is by construction the current
   session.

Where `<cwd-slug>` is the current working directory with `/` replaced by `-` and
a leading `-` (Claude Code's established convention).

## Rationale
- The JSONL is authoritative, versioned, and survives session restarts.
- "Most recently modified" is correct inside an interactive session because the
  current session is the one being written to.
- No private API dependency; no fragile `ps` inspection.

## Consequences
- The `TranscriptLocator` must tolerate the slug convention potentially changing
  in future Claude Code versions. If it changes, this ADR is superseded.
- If a user runs two Claude Code sessions for the same directory, "most recent"
  may ambiguate. Environment-variable-based resolution takes precedence when
  available and is the expected long-term path.
- We parse the JSONL with the standard `json` module line-by-line. No schema
  assumptions beyond `role`, `content`, and `timestamp`/`created` fields.
- A message selector skips turns whose content has no `type: "text"` block with
  non-empty text — pure tool-use turns are not counted toward `n`.
