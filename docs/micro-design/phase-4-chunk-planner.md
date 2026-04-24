# Phase 4 Micro-Design — Fibonacci Chunk Planner

## Scope
Decompose an `AudioTranscript` into a `ChunkPlan` of `ChunkDescriptor`s whose
target sizes follow `F(k) × N × C`. Boundaries snap to natural text breaks.

## M1 — Classes
- `FibonacciSeq` (L2 primitive)
- `BoundarySnapper` (L2 primitive)
- `DurationEstimator` (L2 primitive)
- `AudioTranscript` (data)
- `ChunkDescriptor` (data)
- `ChunkPlan` (aggregate)
- `ChunkPlanner` (L3 service)

## M2 — Semantics

### FibonacciSeq
Iterator-style class. `next_target()` returns the next Fibonacci number
as an integer. Starts at F(1)=1, F(2)=1, F(3)=2, ...

### BoundarySnapper
Pure function packaged as a class for testability and extension:
`snap(text: str, start: int, target: int, tolerance: float) -> int`.
Returns the end-offset to use (exclusive). Priority order:
1. Paragraph break (`\n\n` or `\n\s*\n`).
2. Sentence terminator (`.`, `?`, `!` + whitespace or EOF).
3. Clause terminator (`,`, `;`, `:` + whitespace).
4. Word break (whitespace).

Window: `[start + 1, min(len, start + target × (1 + tolerance))]`.
If the target exceeds the remaining length, return `len(text)`.
If no boundary of any priority exists in the window, fall back to the
last word-break ≤ target (scan backward). Never emit a cut inside a word.

### DurationEstimator
`estimate_seconds(char_count: int, voice_profile: VoiceProfile) -> float`
returns `char_count / voice_profile.chars_per_second`.

### AudioTranscript (frozen dataclass)
Holds the audio-friendly text. Paired with a derived `char_count` for
convenience.

### ChunkDescriptor (frozen dataclass)
One entry in the plan: index, fib_index, target_char_count,
actual_char_count, text, estimated_duration_seconds, boundary offsets.

### ChunkPlan (frozen dataclass)
Immutable aggregate: tuple of ChunkDescriptors + derived totals.

### ChunkPlanner (service)
`plan(transcript: AudioTranscript, voice_profile: VoiceProfile,
      base_duration_seconds: float = 4.0,
      tolerance: float = 0.25) -> ChunkPlan`.

Algorithm:
```
offset = 0
fib = FibonacciSeq()
k = 1
descriptors = []
while offset < len(text):
    target_chars = int(fib.next_target() * base_duration_seconds * cps)
    end = BoundarySnapper.snap(text, offset, target_chars, tolerance)
    slice_text = text[offset:end]
    descriptors.append(ChunkDescriptor(
        index=k, fib_index=F(k),
        target_char_count=target_chars,
        actual_char_count=len(slice_text),
        text=slice_text,
        estimated_duration_seconds=DurationEstimator.estimate_seconds(...),
        boundary_offset_start=offset,
        boundary_offset_end=end,
    ))
    offset = end
    k += 1
return ChunkPlan(tuple(descriptors), ...)
```

## M3 — Relationships

```
ChunkPlanner ──► FibonacciSeq          (target-size source)
             ──► BoundarySnapper       (where to cut)
             ──► DurationEstimator     (duration computation)
             └─► VoiceProfile (read)   (chars_per_second)
```

No external I/O. Pure, deterministic, trivially testable.

## M4 — Key invariants to test

- **Lossless concat.** `"".join(d.text for d in plan.descriptors) == transcript.text`.
- **Monotonic offsets.** descriptor[i].end <= descriptor[i+1].start for all i (adjacent, not overlapping).
- **Boundary tolerance.** For all non-final descriptors, either
  `target × (1 - tolerance) <= actual <= target × (1 + tolerance)`, or no
  stronger boundary existed (accepted weakest word-break within window).
- **No empty chunks.** `len(d.text) > 0` for every descriptor.
- **No mid-word splits.** For every descriptor except the last, the next
  character at the split position is either whitespace or end-of-text.

## Decisions

- **Tolerance 25%.** Matches ADR-004. Generous enough to reliably find a
  sentence boundary most of the time.
- **Integer target sizes.** We take `int(...)`, truncating toward 0.
  This biases slightly toward smaller-than-target chunks, which is fine.
- **Short path decision lives upstream** in `ShortPathStrategy`
  (Phase 7). The planner itself will still produce a plan even for short
  text; the caller chooses whether to use it. Keeps the planner pure.
- **Trim trailing whitespace at boundaries.** If the snapper lands just
  after whitespace, we include that whitespace in the *previous* chunk
  (so the next chunk starts with a non-space character). This prevents
  "silence spikes" caused by the TTS receiving leading whitespace.

## Check gate

Property test on ≥20 random inputs (varying length, punctuation
density) confirms the four invariants above. Manual inspection of the
chunk plan for a real ~2 KB rewrite confirms sizes look reasonable.
