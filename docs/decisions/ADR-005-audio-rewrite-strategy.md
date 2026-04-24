# ADR-005 — Audio rewrite via Claude (not rule-based)

**Status:** Accepted
**Date:** 2026-04-23

## Context
Transforming an `AssistantMessage` into an `AudioTranscript` requires many
judgment calls:
- Should this code block be read verbatim or described?
- Is this `*` punctuation or multiplication?
- Does this file path need spelling, or is the directory-separator cadence enough?
- This table has 12 rows — read all or summarize the schema and a few rows?

A rule-based rewriter (regex patterns, markdown parsing) can handle the mechanical
cases but fails on judgment. A Claude-mediated rewrite handles every case
contextually at the cost of one additional LLM round-trip.

## Decision
**The rewrite is performed by Claude itself, inline in the slash command, using
an explicit prompt contract.** The contract is captured in the slash command
body (`plugin/commands/speak.md`) and mirrored in
[artifacts/interface-contracts.md](../specification/artifacts/interface-contracts.md)
section "Runtime-filled contract: the audio-friendly rewrite".

Claude is already in-session; the marginal cost is tiny compared to TTS
generation and playback.

## Rationale
- Handles the whole spectrum of judgment calls uniformly.
- Trivially adapts when Claude responses start using new formats (new code
  styles, new diagram conventions, Unicode art, etc.).
- No maintenance burden for a rule library.
- The prompt contract is short enough to include verbatim in the slash command,
  so the rewrite logic is auditable in one place.

## Alternatives considered
- **Rule-based in Python.** Rejected: brittle against evolving Claude response
  shapes; cannot make judgment calls; would need continuous maintenance.
- **Local small LLM.** Rejected: we already have the primary Claude session;
  a second model is redundant latency and GPU contention with Kokoro.

## Consequences
- The rewrite prompt must be carefully designed and tested. It lives in
  `plugin/commands/speak.md`. Changes to it are recorded in commits as
  user-facing changes to behavior.
- The slash command flow is: user → Claude (rewrite pass) → `speak.py`
  (with the rewritten text as input) → audio pipeline. The slash command
  body orchestrates this handoff.
- We trust Claude's rewrite to be lossless. This trust is testable manually
  (compare source response to spoken audio for content coverage) but not
  automatable without a second judge model.
