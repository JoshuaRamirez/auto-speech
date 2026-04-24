# ADR-007 — Mandatory concatenation; the single WAV is the durable artifact

**Status:** Accepted
**Date:** 2026-04-23
**Supersedes (in spirit):** the short-vs-long artifact asymmetry implied by v0.1.

## Context
In v0.1 a successful run leaves a tmpdir containing either `short-path.wav`
(short path) or `chunk-001.wav … chunk-N.wav` (long path). On cleanup, the
tmpdir is deleted unless `--keep-artifacts` was passed. The chunks exist *only*
as an operational artifact of the buffered playback scheme.

Two upcoming features (ADR-008 replay cache; ADR-009 mpv-based seekable
playback) both require a **single** WAV as their input. Preserving N chunks per
run per voice as the cache format would force both features to concatenate
on demand — redundant work, and structurally asymmetric with the short path.

## Decision

**A successful `/speak` run always produces exactly one WAV file (`full.wav`)
in the run's working directory, and deletes the per-chunk WAVs on success.**

- Long path: after both producer and consumer threads join cleanly,
  `WavConcatenator` reads `chunk-*.wav` in plan order and writes `full.wav`
  with the same sample rate / sample width / channel count. On success, the
  per-chunk files are unlinked.
- Short path: the single synthesized WAV is named `full.wav` from the start.
  No concatenation is needed; the naming contract is uniform.
- On any failure (producer error, consumer error, interrupt): the chunks are
  preserved for debugging, and no `full.wav` is produced. The tmpdir policy
  (`--keep-artifacts`) is unchanged.

## Rationale

1. **Downstream features are simpler.** Replay cache (ADR-008) stores one
   file per entry. Seekable playback (ADR-009) receives one file, not N.
2. **The short/long asymmetry disappears.** Both paths produce `full.wav`;
   callers never have to branch on plan length.
3. **Chunks are a scheduling artifact, not a durable one.** Their role
   (decouple generation from playback timing) is complete once playback
   succeeds. Keeping them past that point would duplicate data.
4. **Atomicity and debuggability are preserved.** Concat writes to
   `full.wav.partial` then renames. If the run errors, chunks remain on
   disk untouched — every post-mortem question is still answerable.
5. **No user-visible flag.** "Always produce the concat" is simpler than
   "opt into the concat"; the concat cost is trivial (~50 ms for 5 min of
   audio on SSD) and dwarfed by playback time.

## Alternatives considered

- **Opt-in via `--save <path>`.** Rejected: leaves the short/long asymmetry,
  gives downstream features two things to reason about, and adds a flag that
  users would have to remember.
- **Keep chunks + concat.** Rejected: duplicates data. Short-term useful for
  debugging but better offered via `--keep-artifacts` preserving everything
  pre-concat, which is simpler — we add that behavior explicitly.
- **Concat in parallel with playback (overlap producer finish with concat).**
  Deferred: sequential concat after both threads join is simpler, ~50 ms
  slower in theory, but with no user-visible impact. We can revisit if
  measurement shows it matters.

## Consequences

- A new L1 I/O adapter `WavConcatenator` exists.
- `PipelineOrchestrator` gains a post-threads-join concat step on the long
  path, and renames `short-path.wav` to `full.wav` on the short path.
- `--keep-artifacts` now means "preserve the tmpdir and its contents
  *including* chunks on the long path before concat runs." We change the
  order: if `--keep-artifacts`, concat still runs, *but* the chunks are not
  deleted post-concat. A user debugging timing sees both the chunks and the
  concat side-by-side.
- The "concat artifact" is the only file a downstream user needs to know about.
- Phase 9's historical captures under `tests/outputs/` keep their chunk
  files as historical record; the new behavior applies from Phase 10 onward.

## Invariant additions

- **Concat-on-success, never-on-failure.** `full.wav` appears in the tmpdir
  only on a run that played cleanly to completion.
- **Chunks-deleted-unless-kept.** Post-concat chunk cleanup is suppressed iff
  `--keep-artifacts` was requested.
- **Atomic concat.** `full.wav` is either fully present or absent — never
  half-written.
