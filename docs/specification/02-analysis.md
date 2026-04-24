# 02 — Analysis

## Approach

This phase models *what* the system does, independent of implementation choices.
The goal is to surface the domain entities, their relationships, the invariants
that bind them, and the use cases they serve.

## Primary use cases

See [artifacts/use-cases.md](artifacts/use-cases.md) for full forms. Summary:

| ID | Name | Trigger |
|---|---|---|
| UC-01 | Speak the last assistant message | `/speak` |
| UC-02 | Speak the Nth-most-recent assistant message | `/speak n` |
| UC-03 | Short-response fast path | automatic when transcript is brief |
| UC-04 | Voice calibration (prerequisite, one-time) | `setup/install.sh` or explicit recalibration |

Supporting / exceptional flows:
- UC-05 (exception): No calibration present → use sane default + warn.
- UC-06 (exception): Requested index exceeds assistant-turn count → fail loud.
- UC-07 (exception): Transcript JSONL malformed → fail loud; do not play partial.

## Domain model narrative

The domain breaks into **four conceptual planes**, each with its own language:

### 1. Source plane — the conversation
The raw material. A `TranscriptSession` is the JSONL file Claude Code writes for a
single session. It contains an ordered sequence of turns; some are user, some are
assistant, some are tool. An `AssistantMessage` is one assistant turn that contains
at least one block of non-empty text content. Non-text turns (pure tool calls) are
skipped when counting `n`.

### 2. Rewrite plane — the audio transcript
An `AudioTranscript` is the audio-friendly transformation of an `AssistantMessage`.
It preserves every fact, example, and nuance of the source, but restates them in a
form a human could read aloud at a meeting: code becomes narrated code, symbols
become words, file paths are spelled out, tables become sentences. Lossless in
meaning, different in form.

### 3. Scheduling plane — the chunk plan
Before any audio is generated, the full `AudioTranscript` is decomposed into a
`ChunkPlan`: an ordered list of `ChunkDescriptor`s whose target sizes follow the
Fibonacci sequence (1, 1, 2, 3, 5, 8, …) scaled by a **base duration** `N`
(seconds) and a **voice characters-per-second** calibration value. Each descriptor
snaps to the nearest natural text boundary within a tolerance so that no word is
split and no sentence ends mid-inflection.

### 4. Materialization plane — segments and playback
For each `ChunkDescriptor` the system synthesizes an `AudioSegment` (WAV file)
through the TTS engine. A `PlaybackQueue` receives segments in order; the
`PlaybackConsumer` plays them via `afplay`. Generation and playback run
concurrently under a producer/consumer model. Because Fibonacci chunks grow, the
generator's lead over the player widens monotonically after the first few chunks.

## Key abstractions

### Entities
- **TranscriptSession** — a Claude Code session on disk.
- **AssistantMessage** — one assistant turn with text content.
- **AudioTranscript** — rewritten, audio-ready text.
- **ChunkPlan** — ordered list of ChunkDescriptors with aggregate estimates.
- **ChunkDescriptor** — metadata + text slice for one chunk.
- **AudioSegment** — a generated WAV corresponding to one ChunkDescriptor.
- **VoiceProfile** — a voice ID + speed + measured chars/second.
- **CalibrationRun** — a record of one calibration measurement.

### Value objects
- **FibonacciIndex** — an integer position in the Fibonacci sequence.
- **BoundaryOffset** — a character index within an AudioTranscript.
- **DurationEstimate** — a seconds-value paired with its basis.
- **TurnOrdinal** — "Nth-most-recent" count, 1-indexed from the end.

### Services
- **TranscriptLocator** — finds the right JSONL for the current session.
- **MessageSelector** — picks the Nth-most-recent qualifying AssistantMessage.
- **AudioRewriter** — produces the AudioTranscript (realized via Claude in-session).
- **FibonacciPlanner** — emits the target-size sequence.
- **BoundarySnapper** — snaps a target size to a natural text boundary.
- **ChunkPlanner** — composes FibonacciPlanner + BoundarySnapper into a ChunkPlan.
- **TTSEngine** — generates WAV for a text string.
- **SegmentProducer** — produces AudioSegments from ChunkDescriptors.
- **PlaybackQueue** — FIFO of AudioSegments, thread-safe.
- **PlaybackConsumer** — drains the queue into `afplay`.
- **PipelineOrchestrator** — wires the full use case together.
- **Calibrator** — produces/maintains the VoiceProfile.

## Invariants

1. **Plan-before-generate.** The full `ChunkPlan` is finalized before the first
   `AudioSegment` is requested. (Prevents cascading re-planning costs and keeps
   segment ordering deterministic.)
2. **Generate-in-order.** `SegmentProducer` processes ChunkDescriptors strictly
   in plan order. (Allows playback consumer to consume in order without lookahead.)
3. **File-then-enqueue.** An `AudioSegment` is only enqueued *after* its WAV file
   has been fully written to disk (truncate-and-rename pattern). Prevents
   afplay from opening a partial file.
4. **Atomic playback.** `afplay` is invoked on a whole segment; no partial playback.
5. **Bounded boundary deviation.** A chunk's actual character count must be within
   `[0.75 × target, 1.25 × target]` of the Fibonacci target, unless the chunk is
   the last one (which takes whatever remains).
6. **No word splits.** Boundaries always fall at word-end at minimum.
7. **Fail loud.** On any error (missing transcript, TTS failure, disk I/O failure),
   halt the pipeline, log the cause, do not emit partial audio.
8. **Short-path skip-scheduling.** If estimated total duration ≤ `SHORT_THRESHOLD`
   seconds, produce one AudioSegment and bypass the queue.

## Behavioral model (run-time nouns become verbs)

```
locate_transcript  →  select_message  →  rewrite_for_audio
                                              │
                              ┌───────────────┴───────────────┐
                              │ short path                    │ long path
                              ▼                               ▼
                        generate_one  ─── play_one       plan_chunks
                                                              │
                                                              ▼
                                                    start_producer + start_consumer
                                                              │
                                                              ▼
                                                    wait_for_completion
```

See [diagrams/sequence-pipeline.mmd](diagrams/sequence-pipeline.mmd) for the
temporal view of the long path.

## State of scheduling — Fibonacci

Given calibration `C` (chars/sec) and base `N` (sec), the target char-count
for chunk index `k` (k = 1, 2, …) is `F(k) × N × C`, where `F(k)` is the
k-th Fibonacci number (F(1)=F(2)=1, F(3)=2, …).

For default `N = 4 s` and a calibrated `C ≈ 15 chars/s`, targets are:
```
k=1 → 60 chars      k=5 → 300 chars     k=8 → 1260 chars
k=2 → 60 chars      k=6 → 480 chars     k=9 → 2040 chars
k=3 → 120 chars     k=7 → 780 chars     k=10 → 3300 chars
```

Cumulative playtime after k chunks equals `N × (F(k+2) - 1)` seconds. After 8
chunks, ~132 s has played. For any reasonable Claude response, the plan converges
in ≤ 10 chunks.

## Boundary snapping hierarchy

When choosing the actual cut point near a target offset, snap to the strongest
available boundary within tolerance, in this priority:
1. Paragraph break (double newline).
2. Sentence terminator (`.`, `?`, `!`) followed by whitespace.
3. Clause terminator (`,`, `;`, `:`) followed by whitespace.
4. Word break (whitespace).

A lower-priority boundary is chosen only if no higher-priority boundary exists
within the tolerance window.

## See also
- [Domain model narrative](artifacts/domain-model.md)
- [Domain model diagram](diagrams/domain-model.mmd)
- [Use cases](artifacts/use-cases.md)
