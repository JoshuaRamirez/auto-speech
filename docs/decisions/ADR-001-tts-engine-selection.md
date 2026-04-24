# ADR-001 — TTS engine selection

**Status:** Accepted
**Date:** 2026-04-23

## Context
We need a local TTS that runs on Apple Silicon with high quality and low
time-to-first-audio. The user has 128 GB RAM on an M-series Mac, so model size
is not a binding constraint; inference latency and voice quality are.

## Candidates considered

| Engine | Quality | Latency on M-series | Apple-Silicon native | Install burden |
|---|---|---|---|---|
| Kokoro-82M via mlx-audio | Very good | ~5–10× realtime | Yes (MLX) | Low |
| Dia-1.6B | Excellent / expressive | ~1–2× realtime | Partial (PyTorch MPS) | Medium |
| F5-TTS | Good + voice cloning | ~1–3× realtime | Partial | Medium |
| XTTS v2 (Coqui) | Good | ~0.5–1× realtime | No (PyTorch MPS) | Medium |
| Piper | Moderate | ~20× realtime | CPU-only | Low |

## Decision
**Use Kokoro-82M via mlx-audio as the v1 engine.**

## Rationale
Kokoro is the only option that is (a) Apple-Silicon-native via MLX,
(b) faster than realtime by a large enough margin that the Fibonacci producer
comfortably stays ahead of the consumer, and (c) small enough to load quickly
on first use. Its voice quality is acceptable for a conversational readback of
Claude's prose.

Dia would give higher quality but its realtime-ish throughput erodes the
buffering margin we need. If quality becomes the primary complaint, ADR-001
can be superseded by ADR-007 (TBD) switching to Dia at the cost of slightly
later time-to-first-audio.

## Consequences
- The `TTSEngine` L1 adapter is written against mlx-audio's API/CLI, not against
  a generic TTS abstraction. Swapping engines later means rewriting that class —
  acceptable because it is deliberately small and isolated.
- Voice IDs in `VoiceProfile` follow Kokoro's naming (`af_*`, `am_*`, etc.).
- Calibration runs measure Kokoro's specific throughput; recalibration is
  required on any engine swap.
