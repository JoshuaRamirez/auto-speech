# Phase 10 Micro-Design — Mandatory WAV Concatenation

Implements [ADR-007](../decisions/ADR-007-mandatory-concat-and-cache-centric-artifact.md).

## Scope
Every successful `/speak` run produces exactly one WAV file, `full.wav`, in
the run's tmpdir. Per-chunk WAVs are deleted unless `--keep-artifacts` is set.
The short path renames its sole output to `full.wav` for naming uniformity.

## M1 — Classes at this level of abstraction

- **`WavConcatenator`** (L1 I/O adapter, new)

No other new classes. Everything else is a small edit to existing L1/L3/L4
code (`short_path.py`, `pipeline.py`).

## M2 — Semantics

### `WavConcatenator`
Pure I/O adapter. Given an ordered list of source WAV paths and a destination
path, reads each source, verifies format consistency (channels, sample width,
sample rate), writes the concatenated samples to the destination atomically.

- `concat(sources: list[Path], dest: Path) -> None`
- Pre: every `sources[i]` is a readable PCM WAV with identical format params.
- Post: `dest` exists and is a valid PCM WAV whose frame count is the sum of
  the sources' frame counts and whose format matches.
- Atomicity: writes to `dest.partial.wav`, flushes, closes, renames.
- Raises: `WavConcatError` if format params disagree or any read/write fails.

## M3 — Relationships

```
PipelineOrchestrator ── long path post-join ──► WavConcatenator
PipelineOrchestrator ── long path post-concat ──► unlink chunk-*.wav (unless --keep-artifacts)
PipelineOrchestrator ── short path post-play ──► rename short-path.wav full.wav
```

No new dependencies. `WavConcatenator` stands alone; uses stdlib `wave` only.

## M4 — Interfaces & implementation

### `plugin/scripts/python/wav_concatenator.py`
```python
class WavConcatError(RuntimeError): ...

class WavConcatenator:
    @staticmethod
    def concat(sources: list[Path], dest: Path) -> None:
        # open first source -> pick format
        # open dest.partial -> set same format
        # for each source: verify format, stream frames into dest
        # close, os.replace to dest
```

### `plugin/scripts/python/pipeline.py` edits
Inside `_long_path`, after `t_cons.join()` and before the success return:

```python
if consumer.error is None and producer.error is None:
    full_path = tmpdir / "full.wav"
    WavConcatenator.concat(
        [seg.wav_path for seg in played_in_order],  # descriptor order
        full_path,
    )
    if not self._keep_artifacts:
        for descriptor in plan:
            chunk = tmpdir / f"chunk-{descriptor.index:03d}.wav"
            chunk.unlink(missing_ok=True)
```

Because the plan's descriptor order is the canonical order, we derive source
paths from the plan directly rather than tracking them through the producer —
the plan is the source of truth for ordering.

Short-path edit: `ShortPathStrategy.execute` writes to `tmpdir / "full.wav"`
instead of `tmpdir / "short-path.wav"`. One-line change.

### Tmpdir cleanup invariant
The orchestrator's existing `if exit_code == EXIT_OK and not keep_artifacts: rmtree`
stays. That deletes the tmpdir including `full.wav` on success (since v0.1's
behavior is "nothing persists on success"). Phase 11 will change this by
promoting `full.wav` into the cache before the rmtree runs.

## Failure modes and handling
| Mode | Handling |
|---|---|
| A chunk WAV missing post-producer (should be impossible given file-then-enqueue) | `WavConcatError` surfaces; exit 5 (TTS failure category, closest match). |
| Chunks have mismatched sample rates (should be impossible with a single Kokoro load) | `WavConcatError`; exit 5. |
| Disk full mid-concat | Concat fails, `.partial` left behind, no `full.wav` is promoted; exit 5. |
| `--keep-artifacts` and concat fails | Chunks preserved (they never got deleted); user can inspect. |

## Invariants introduced
- **I-10.1 Concat-iff-success.** `full.wav` exists at exit iff both threads
  completed without error.
- **I-10.2 Chunks-absent-on-success-unless-kept.** After a successful run
  without `--keep-artifacts`, no `chunk-*.wav` survives the orchestrator.
- **I-10.3 Format parity.** `full.wav` has the same sample rate, channel
  count, and sample width as any of its input chunks.
- **I-10.4 Frame-count sum.** `full.wav`'s frame count equals the sum of
  its input chunks' frame counts (modulo atomicity-safe reads).

## Check gate

1. **Long path run with default flags.** `/speak` → tmpdir gets deleted;
   during the run, after both threads join, a `full.wav` briefly existed
   (verifiable by inserting a `--keep-artifacts` run). Manual test.
2. **Long path with `--keep-artifacts`.** All chunks and `full.wav` present
   after run; `afplay full.wav` reproduces the full audio.
3. **Short path with `--keep-artifacts`.** Only `full.wav` exists in tmpdir
   (no `chunk-*.wav`, no `short-path.wav`).
4. **Format parity.** `python -m wave full.wav` or `file full.wav` reports
   the expected parameters (mono, 16-bit, 24000 Hz) for any run.
5. **Frame-count sum.** A scripted assertion in
   `tests/test_wav_concat.py` generates three short WAVs with known frame
   counts and verifies the concat frame count equals the sum.

## Out of scope for this phase
- Caching or any persistent storage of `full.wav` beyond the run's tmpdir —
  Phase 11 (ADR-008).
- Playback seeking or pause/resume — Phase 12 (ADR-009).
