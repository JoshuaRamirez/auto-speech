# Domain Model

A narrative description of the domain entities, value objects, services, and
their relationships. The matching UML-style view is in
[../diagrams/domain-model.mmd](../diagrams/domain-model.mmd).

## Entities (have identity)

### TranscriptSession
The JSONL file Claude Code persists for one session. Identified by the session UUID
embedded in its file name. Its relevant content is an ordered sequence of turns.

**Attributes**
- `session_id: UUID`
- `file_path: Path`
- `turns: list[Turn]` (lazy-read, line by line)

**Operations**
- `iter_turns() → Iterator[Turn]`
- `iter_assistant_messages() → Iterator[AssistantMessage]`

### AssistantMessage
One assistant turn with **at least one** non-empty text content block.
Turns composed solely of tool-use or tool-result blocks are not `AssistantMessage`s
by this definition (they are skipped for selection purposes).

**Attributes**
- `turn_index: int`   (position in the full turn sequence)
- `ordinal_from_end: int`   (1 = most recent, 2 = next-most-recent, …)
- `timestamp: datetime`
- `text: str`   (concatenation of text blocks, newline-separated if multiple)

### AudioTranscript
The audio-friendly rewrite of an `AssistantMessage`. Identity-equal to its source
message (one-to-one).

**Attributes**
- `source: AssistantMessage`
- `text: str` (plain text, TTS-ready)
- `char_count: int`
- `estimated_duration_seconds: float` (derived from `VoiceProfile.chars_per_sec`)

**Invariant:** the rewrite preserves every fact, example, caveat, and nuance of
`source.text`. Form differs; content is lossless.

### ChunkDescriptor
One planned chunk within a `ChunkPlan`.

**Attributes**
- `index: int` (1-based)
- `fib_index: int` (the Fibonacci position used)
- `target_char_count: int`
- `actual_char_count: int`
- `text: str`
- `estimated_duration_seconds: float`
- `boundary_offset_start: BoundaryOffset`
- `boundary_offset_end: BoundaryOffset`

### AudioSegment
The synthesized WAV for one `ChunkDescriptor`.

**Attributes**
- `descriptor: ChunkDescriptor`
- `wav_path: Path`
- `actual_duration_seconds: float` (measured from the WAV header/samples)
- `generation_elapsed_seconds: float`

### VoiceProfile
A calibrated, named voice.

**Attributes**
- `voice_id: str` (e.g., `"af_heart"` for Kokoro)
- `speed: float` (default `1.0`)
- `chars_per_second: float` (measured)
- `calibrated_at: datetime`
- `calibration_source_chars: int`

### CalibrationRun
A historical record of one calibration measurement.

**Attributes**
- `timestamp: datetime`
- `voice_id: str`
- `speed: float`
- `reference_text_chars: int`
- `measured_duration_seconds: float`
- `computed_chars_per_second: float`

### ChunkPlan (aggregate root for scheduling)
The immutable, in-order list of `ChunkDescriptor`s for one run.

**Attributes**
- `descriptors: tuple[ChunkDescriptor, ...]`
- `total_estimated_duration_seconds: float`
- `transcript: AudioTranscript` (back-reference)

**Operations**
- `total_chars() → int`
- `len() → int`

### PlaybackQueue
Thread-safe FIFO of `AudioSegment`s.

**Operations**
- `put(segment: AudioSegment)` — blocks until consumer has capacity (optional backpressure).
- `get() → AudioSegment | SENTINEL`
- `close()` — producer signals end-of-plan.

## Value objects (no identity)

| VO | Meaning |
|---|---|
| `FibonacciIndex` | Nonnegative integer position. |
| `BoundaryOffset` | Character offset into an `AudioTranscript`. |
| `DurationEstimate` | `(seconds: float, basis: str)` — basis is `"calibrated"` or `"default_fallback"`. |
| `TurnOrdinal` | Positive integer, 1-indexed from the most recent turn. |

## Services (stateless behavior)

| Service | Responsibility |
|---|---|
| `TranscriptLocator` | Given the current session context, resolve the right JSONL file path. |
| `TranscriptReader` | Stream JSONL lines and yield parsed turns. |
| `MessageSelector` | Pick the Nth-most-recent `AssistantMessage` from a stream of turns. |
| `AudioRewriter` | Produce the `AudioTranscript` from an `AssistantMessage`. (Realized at runtime by Claude in-session following the rewrite-prompt contract.) |
| `FibonacciPlanner` | Yield target char counts per chunk index. |
| `BoundarySnapper` | Given a text and a target offset, return the snapped boundary offset. |
| `ChunkPlanner` | Combine planner + snapper to produce a `ChunkPlan` for an `AudioTranscript`. |
| `TTSEngine` | Synthesize a WAV for a given text. |
| `SegmentProducer` | Iterate `ChunkPlan` → produce `AudioSegment`s → enqueue. |
| `PlaybackConsumer` | Dequeue `AudioSegment`s → play via `afplay`. |
| `Calibrator` | Measure `chars_per_second` for a voice; persist a `VoiceProfile`. |
| `PipelineOrchestrator` | Wire all services for a single `/speak` invocation. |
| `ShortPathStrategy` | Bypass planning/producer for brief transcripts. |

## Relationships

- `TranscriptSession` *has many* `AssistantMessage`s (derived from its turns).
- `AssistantMessage` *produces one* `AudioTranscript`.
- `AudioTranscript` *produces one* `ChunkPlan`.
- `ChunkPlan` *has many* `ChunkDescriptor`s, ordered by `index`.
- `ChunkDescriptor` *produces one* `AudioSegment`.
- `SegmentProducer` *writes to* `PlaybackQueue`.
- `PlaybackConsumer` *reads from* `PlaybackQueue`.
- `VoiceProfile` *is referenced by* `TTSEngine`, `Calibrator`, `ChunkPlanner`
  (via `chars_per_second`).
- `CalibrationRun` *is the historical source of* `VoiceProfile.chars_per_second`.

## Aggregates and boundaries

- The **scheduling aggregate** is `ChunkPlan` rooted. No external code mutates
  descriptors once the plan is emitted.
- The **materialization aggregate** is implicit: the `PlaybackQueue` is the
  synchronization point between producer and consumer. Everything upstream of
  the queue writes; everything downstream reads.

## Ontological notes

The model deliberately separates **scheduling intent** (ChunkPlan — what we *will*
produce) from **materialized output** (AudioSegment — what exists on disk). This
keeps planning pure and deterministic, and isolates generation-side flakiness
(TTS model warmup, disk latency) behind the producer service.

The boundary between `AssistantMessage` and `AudioTranscript` is the only
*semantic* transformation in the system. Every other stage is either
measurement, decomposition, synthesis, or playback — all form, no meaning.
