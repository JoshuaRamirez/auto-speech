# ADR-004 — Boundary snapping heuristic

**Status:** Accepted
**Date:** 2026-04-23

## Context
Fibonacci scheduling produces *target* character counts. A chunk whose text cuts
mid-word or mid-sentence produces awkward audio — the TTS engine doesn't know it
needs to fade out, so consecutive chunks have unnatural prosody at the seam.

## Decision
**Snap each chunk's end offset to the nearest available natural boundary within
a ±25% tolerance window around the Fibonacci target.** Priority order:

1. **Paragraph break** (double newline or `\n\n`).
2. **Sentence terminator** (`.`, `?`, `!` followed by whitespace or end-of-text).
3. **Clause terminator** (`,`, `;`, `:` followed by whitespace).
4. **Word break** (whitespace).

The snapper examines the window `[offset, offset + target × 1.25]` and searches
from strongest to weakest boundary. The **first** acceptable boundary wins.
If no boundary of any priority exists in the window, fall back to the hard
target offset *but* search backward to the nearest word boundary to avoid
mid-word cuts.

## Rationale
- Paragraph breaks are the most natural pause point; listening experience
  improves measurably when respected.
- Sentence terminators give TTS engines the right prosodic cue (falling
  intonation, trailing silence).
- Clauses are a reasonable fallback for very long sentences.
- Word breaks are the absolute minimum; cutting mid-word produces garbled audio.

## Tolerance choice (±25%)
A wider window increases the probability of finding a strong boundary but
risks fragmenting the Fibonacci contract (chunks drift from their target
sizes, the lead-over-playback margin shrinks). 25% is empirically generous
without meaningfully affecting buffering margin: a 25% short chunk plays 25%
faster than expected, which at Kokoro's 5× realtime generation is a non-issue.

## Consequences
- Chunk actual sizes will diverge slightly from pure Fibonacci. The `ChunkPlan`
  records both `target_char_count` and `actual_char_count` so observability is
  preserved.
- Adjacent chunks reconstruct the original text losslessly — this is a tested
  invariant (see [interface-contracts.md](../specification/artifacts/interface-contracts.md)
  contract 3).
- The `BoundarySnapper` is pure and testable. Property-based tests can assert
  "concat reconstructs source" for any input.
