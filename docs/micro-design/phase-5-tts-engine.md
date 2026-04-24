# Phase 5 Micro-Design — TTS Engine + Producer

## Scope
`TTSEngine` (written in Phase 1 already) remains unchanged. Add:
- `AudioSegment` dataclass
- `SegmentProducer` that walks a `ChunkPlan` and enqueues segments

## M1 / M2 / M3

### AudioSegment (dataclass)
```
descriptor: ChunkDescriptor
wav_path: Path
actual_duration_seconds: float
generation_elapsed_seconds: float
```

### SegmentProducer
- Takes: `tts_engine`, `voice_profile`, `tmpdir`, `queue`, `stop_event`.
- Iterates a `ChunkPlan`, calls `tts_engine.synthesize`, inspects the WAV
  for exact duration, constructs an `AudioSegment`, puts it on the queue.
- On last chunk, enqueues `queue.close()` (SENTINEL).
- Honors `stop_event` between chunks: if set, stops and enqueues SENTINEL.
- On TTS failure, logs and enqueues SENTINEL before propagating the
  exception to the calling thread (the orchestrator picks it up).

## Invariants
- WAVs are written atomically via `TTSEngine.synthesize`.
- Segments appear on the queue strictly in plan-order.
- Each segment's `wav_path` is a real, fully-written WAV.

## Check gate
Build a fake 3-descriptor plan from three short strings, run the producer
against a real queue consumer that just collects outputs, and verify:
1. Three WAVs appear in the tmpdir in sequence.
2. The SENTINEL arrives last.
3. Durations are plausible.
