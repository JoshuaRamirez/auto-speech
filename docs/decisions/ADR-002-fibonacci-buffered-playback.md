# ADR-002 — Fibonacci-scaled buffered playback

**Status:** Accepted
**Date:** 2026-04-23

## Context
For responses of any significant length, generating the entire audio before
playback begins makes the user wait too long for first audio. Constant-size
chunking solves time-to-first-audio but fragments the audio into many small
files with extra boundary artifacts and model-warmup overhead on the first
chunk.

## Decision
**Chunk sizes follow the Fibonacci sequence scaled by a base duration N and
the calibrated chars-per-second C:** `target(k) = F(k) × N × C`.

## Rationale
- **Small first chunk (F(1) = 1)** minimizes time-to-first-audio (~N seconds
  of audio, generated in ~N/speed_factor time).
- **Gentle growth (~1.618× per step)** keeps chunks growing faster than linear
  but slower than geometric. Avoids two failure modes: staying small forever
  (many seams, many model-load warmups) and blowing up so fast that generation
  of the next chunk can't finish before the previous finishes playing.
- **Sum grows fast enough to cover long responses in ≤ 10 chunks.** For a
  500-second response, k reaches 10; plan is small and bounded.

## Alternatives considered
- **Constant-size chunks (e.g., 4 s each).** Rejected: dozens of seams in long
  output, and the per-chunk subprocess/file-I/O overhead becomes significant.
- **Geometric growth (1, 2, 4, 8).** Rejected: chunk 5 at 16N seconds is already
  large enough that it may not generate before chunk 4 finishes playing on slower
  hardware.
- **Linear growth (1, 2, 3, 4).** Rejected: grows too slowly; too many chunks
  on long responses.
- **Content-aware adaptive sizing.** Rejected for v1: adds state and complexity
  for marginal gain; Fibonacci is a good enough universal prior.

## Consequences
- `ChunkPlanner` has an explicit Fibonacci dependency. Swapping schedules means
  rewriting the planner.
- `BASE_DURATION_SECONDS` is a tuning knob. Default 4 s; will be revisited after
  first human listening test.
- The short-path strategy (ADR-008? — currently captured in UC-03) is needed
  because Fibonacci is inappropriate overhead for short responses.
