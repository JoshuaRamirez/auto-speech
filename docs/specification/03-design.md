# 03 — Design

## Architecture overview

The system is a **unidirectional pipeline** with a single concurrency fork: the
producer/consumer split between audio generation and playback. Everything upstream
of that fork is synchronous and deterministic; everything downstream is a simple
bounded-FIFO coordination problem.

```
  ┌────────────────────────────────────────────────────────────────┐
  │  SYNCHRONOUS PLANNING                                          │
  │                                                                │
  │  TranscriptLocator → TranscriptReader → MessageSelector        │
  │           ↓                                                    │
  │       AssistantMessage                                         │
  │           ↓                                                    │
  │  AudioRewriter (Claude pass, in-session)                       │
  │           ↓                                                    │
  │       AudioTranscript                                          │
  │           ↓                                                    │
  │  [ShortPathStrategy decision]                                  │
  │      |                                     \                   │
  │      | short                                \ long             │
  │      ↓                                       ↓                 │
  │  (one-shot gen + play)        ChunkPlanner → ChunkPlan         │
  │                                              ↓                 │
  └──────────────────────────────────────────────┼─────────────────┘
                                                 │
  ┌──────────────────────────────────────────────┼─────────────────┐
  │  CONCURRENT MATERIALIZATION                  ↓                 │
  │                                                                │
  │    SegmentProducer ────► PlaybackQueue ────► PlaybackConsumer  │
  │         ↓                                         ↓            │
  │       TTSEngine                                 afplay         │
  │                                                                │
  └────────────────────────────────────────────────────────────────┘
```

## Component decomposition

See [diagrams/component-architecture.mmd](diagrams/component-architecture.mmd) for
the visual; see [artifacts/class-inventory.md](artifacts/class-inventory.md) for
the full CRC-style inventory.

Components group into five **layers of abstraction**:

| Layer | Concern | Components |
|---|---|---|
| L1 — I/O adapters | Disk, process spawn, external binary invocation | `TranscriptReader`, `TTSEngine` (subprocess wrapper), `AfplayLauncher`, `WavInspector` |
| L2 — Domain primitives | Stateless transformations on domain values | `FibonacciSeq`, `BoundarySnapper`, `DurationEstimator` |
| L3 — Domain services | Orchestrating L2 + L1 at a single abstraction level | `TranscriptLocator`, `MessageSelector`, `ChunkPlanner`, `Calibrator`, `SegmentProducer`, `PlaybackConsumer` |
| L4 — Orchestration | Cross-service coordination | `PipelineOrchestrator`, `ShortPathStrategy` |
| L5 — Interface | Entry points | `speak.py` CLI entry, `plugin/commands/speak.md` slash command |

Per user standing rule: code in L2 is behavioral-pattern territory; coordination
between layers (L3↔L4, L4↔L1) is coordination-pattern territory. The producer/
consumer between L3 `SegmentProducer` and L3 `PlaybackConsumer` is the only
coordination pattern we explicitly name.

## Interaction model

See [diagrams/sequence-pipeline.mmd](diagrams/sequence-pipeline.mmd).

### Timeline (long path)

```
t=0    user: /speak
t≈0    orchestrator starts
t=0.05 transcript located + message selected
t=0.1  rewrite prompt returned to Claude, rewrite emitted
t=0.5  ChunkPlan complete
t=0.5  SegmentProducer begins generating chunk #1
t=0.5  PlaybackConsumer starts polling queue
t≈3.0  chunk #1 WAV ready → enqueued → afplay begins
t=3.0  SegmentProducer begins chunk #2 (generates while #1 plays)
t=7.0  chunk #1 finishes (4 s at N=4) → afplay begins chunk #2
...    generator stays ahead; Fibonacci growth means lead widens
```

Time-to-first-audio dominated by: rewrite round-trip (~0.5 s) + first-chunk
generation (~2–3 s with Kokoro model warmup). Target ≤ 6 s.

## Concurrency model

- **One producer thread** (`SegmentProducer`).
- **One consumer thread** (`PlaybackConsumer`).
- **One main thread** (`PipelineOrchestrator`) — joins both at the end.
- **`queue.Queue`** (stdlib) as the `PlaybackQueue`. Bounded capacity `= 3`
  to prevent unbounded memory if something goes wrong.
- The `SENTINEL` value is enqueued by the producer when the last segment is
  placed; consumer terminates on `SENTINEL`.
- Cancellation: `KeyboardInterrupt` in orchestrator sets a `stop_event`;
  producer checks it between chunks; consumer finishes its current `afplay`
  then exits. `afplay` in progress is killed via SIGINT to its subprocess.

## Interface contracts (stable boundaries)

See [artifacts/interface-contracts.md](artifacts/interface-contracts.md).

The **five interfaces that matter** — designed to be stable because crossing them
is expensive or externally observable:

1. `TTSEngine.synthesize(text, voice_profile, out_wav_path) → AudioSegment`
2. `Calibrator.measure(voice_id, speed) → VoiceProfile`
3. `ChunkPlanner.plan(audio_transcript, voice_profile) → ChunkPlan`
4. `PlaybackQueue` (`put`/`get`/`close`) — uses stdlib `queue.Queue` shape.
5. `PipelineOrchestrator.run(turn_ordinal: int) → ExitCode`

Everything else is internal and free to evolve.

## Design patterns employed (judiciously — user policy)

| Pattern | Where | Role | Kind |
|---|---|---|---|
| **Pipeline** | L3→L3 data flow | Sequential transformation stages | coordination |
| **Producer/Consumer** | SegmentProducer + PlaybackConsumer via PlaybackQueue | Decouple generation from playback timing | coordination |
| **Strategy** | `ShortPathStrategy` vs long-path orchestration | Swap planning mode based on estimated duration | behavioral |
| **Template Method** | `BoundarySnapper` search (paragraph → sentence → clause → word) | Same skeleton, varying boundary predicate | behavioral |
| **Adapter** | `TTSEngine` wrapping `mlx-audio` CLI/API | Hide the external library's shape | behavioral |
| **Aggregate / Value Object** | `ChunkPlan` / `ChunkDescriptor` | Immutable planning data | domain |

No Abstract Factory, no Observer, no Singleton. Adding them would be a smell.

## Failure modes & handling

| Mode | Detection | Handling |
|---|---|---|
| TTS model not installed | Import/exec fails in `TTSEngine` init | Fail loud with setup hint. |
| Kokoro weights missing | First generation call errors | Fail loud; do not fall back to an unnamed model. |
| Transcript not found | `TranscriptLocator` returns none | Fail loud; UC-07. |
| Requested N > available | `MessageSelector` raises | Fail loud; UC-06. |
| Rewrite returns empty text | `AudioRewriter` post-condition check | Fail loud. |
| Segment generation fails mid-plan | Subprocess nonzero exit | Stop producer, signal consumer, halt orchestrator. Any played chunks stay played; user gets an error. |
| `afplay` fails | Nonzero exit | Stop consumer, signal orchestrator, halt. |

**No silent fallback.** Per user policy: don't assume anything is working without
evidence. Exceptions surface with precise context.

## Observability

During development:
- Every L3 service writes structured log lines (`[stage] event detail=…`) to
  stderr via a shared logger.
- Generated WAVs persist in a run-specific tmpdir until the run completes,
  then are deleted by the orchestrator. If the orchestrator errors, the
  directory is preserved for inspection.
- A single `--keep-artifacts` flag retains the tmpdir unconditionally.

When code stabilizes per the user's CLAUDE.md:
- Verbose stage logs remain behind a `LOG_VERBOSE` env flag.
- Stable components get XML-doc-equivalent (Python docstring) descriptions of
  their contract.

## Configuration

`config/voice_calibration.json` holds the active `VoiceProfile`:

```json
{
  "voice_id": "af_heart",
  "speed": 1.0,
  "chars_per_second": 15.2,
  "calibrated_at": "2026-04-23T16:50:00Z",
  "calibration_source_chars": 487
}
```

Constants (`BASE_DURATION_SECONDS`, `SHORT_THRESHOLD_SECONDS`,
`BOUNDARY_TOLERANCE`) live in `plugin/scripts/python/config_constants.py` —
code-owned, not config-owned, because changing them changes behavior enough
to warrant a code review.

## What's deliberately absent

- **No caching of generated audio.** Fast enough to regenerate; caching adds a
  cache invalidation burden with no user-visible win.
- **No streaming synthesis.** mlx-audio emits WAVs, not PCM streams. Segments
  are granular enough.
- **No multi-voice dialogue.** One voice per run.
- **No GUI.** The slash command is the UI.
- **No background daemon.** The pipeline runs as a short-lived process per `/speak`.

## See also
- [Component architecture diagram](diagrams/component-architecture.mmd)
- [Sequence diagram](diagrams/sequence-pipeline.mmd)
- [Fibonacci activity diagram](diagrams/activity-fibonacci.mmd)
- [Playback state diagram](diagrams/state-playback.mmd)
- [Class inventory](artifacts/class-inventory.md)
- [Interface contracts](artifacts/interface-contracts.md)
