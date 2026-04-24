# Glossary

Ubiquitous language for the project. Use these exact terms in code, comments,
commits, and discussion.

| Term | Definition |
|---|---|
| **AssistantMessage** | One assistant turn in a session transcript containing at least one non-empty text content block. |
| **AudioSegment** | A generated WAV file corresponding to exactly one ChunkDescriptor. |
| **AudioTranscript** | The audio-friendly rewritten form of an AssistantMessage. Lossless in meaning; different in form. |
| **Base duration (N)** | The seconds-per-unit constant that sets the scale of the Fibonacci chunk-size sequence. Default 4 s. |
| **BoundaryOffset** | A character offset inside an AudioTranscript identifying where a chunk starts or ends. |
| **BoundarySnapper** | The service that maps a target character offset to the nearest natural text boundary (paragraph → sentence → clause → word) within tolerance. |
| **Calibration** | The one-time measurement of `chars_per_second` for a given voice at a given speed. |
| **CalibrationRun** | A historical record of one calibration measurement. |
| **ChunkDescriptor** | One element in a ChunkPlan: index, Fibonacci position, target/actual char counts, text, estimated duration. |
| **ChunkPlan** | The immutable ordered list of ChunkDescriptors for a single `/speak` invocation. |
| **Fibonacci scheduling** | The scaling rule `target_k = F(k) × N × C` where `F(k)` is the k-th Fibonacci number. |
| **FibonacciIndex** | The position `k` in the Fibonacci sequence for a given ChunkDescriptor. |
| **File-then-enqueue** | The invariant that an AudioSegment is never enqueued until its WAV file has been fully written (rename-after-write). |
| **Long path** | The full pipeline: plan → produce → consume. Used when estimated duration > SHORT_THRESHOLD. |
| **MessageSelector** | The service that picks the Nth-most-recent AssistantMessage. |
| **PipelineOrchestrator** | The top-level service that wires every other service for one `/speak` invocation. |
| **PlaybackConsumer** | The service that drains the PlaybackQueue and calls `afplay` on each segment in order. |
| **PlaybackQueue** | A thread-safe FIFO of AudioSegments between the producer and consumer. |
| **SegmentProducer** | The service that walks the ChunkPlan, generates AudioSegments, and enqueues them. |
| **Short path** | The fast path: one AudioSegment, no queue. Used when estimated duration ≤ SHORT_THRESHOLD. |
| **SHORT_THRESHOLD** | The seconds boundary below which the short path is taken. Default 15 s. |
| **TranscriptLocator** | The service that resolves the current session's JSONL file path. |
| **TranscriptReader** | The service that streams JSONL lines and yields parsed turns. |
| **TTSEngine** | The Kokoro-via-mlx-audio wrapper that synthesizes a WAV from text. |
| **TurnOrdinal** | 1-indexed "Nth-most-recent" count for assistant messages. The `n` in `/speak n`. |
| **VoiceProfile** | A calibrated voice: voice_id + speed + chars_per_second. |

## Deliberately avoided terms

| Avoid | Use instead | Why |
|---|---|---|
| "Streaming" | Fibonacci-scaled buffered playback | We don't stream audio bytes; we pipeline file-sized segments. |
| "Chunk" as a verb | Plan (noun); plan_chunks (verb) | Keeps the planning noun distinct from the materialization verb. |
| "Piece" / "block" / "segment" interchangeably | ChunkDescriptor (plan-time), AudioSegment (runtime) | The two concepts are distinct and non-interchangeable. |
| "Summarize" | Rewrite for audio | We never summarize; we restate. |
| "Playback" when we mean "generation" | Use "generation" for TTS, "playback" for afplay | The producer-consumer split is foundational. |
