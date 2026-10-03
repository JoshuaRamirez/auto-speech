# Project Orchestration Plan: auto-speech Unified Daemon Server

## Objective
Refactor the `auto-speech` architecture into a Unified Daemon Server with in-process TTSEngine, blocking NativeAudioSink, UNIX socket IPC thin client, and complete elimination of legacy architectural sprawl (`run_speak.sh`, detached `mpv`, `time.sleep` hacks, `ShortPathStrategy`, etc.).

## Phase 0: Survey & Discovery
1. Spawn 3 exploratory agents in parallel:
   - Explorer 1: Inspect `narrator_service.py`, `speak.py`, existing audio playback, process lifecycle, queues, and dependencies.
   - Explorer 2: Inspect MLX Kokoro TTS engine integration (`TTSEngine`, initialization, model loading, audio generation API, wav outputs).
   - Spec Miner: Map legacy architectural components targeted for deletion (`run_speak.sh`, `PipelineOrchestrator`, `ShortPathStrategy`, `MpvController`, `SessionDir` PID management) and CLI expectations.
2. Merge explorer reports into `PROJECT.md` Feature Inventory & Architecture.

## Phase 1: Dual Track Decomposition
- **Track 1: E2E Testing Track**
  - Spawn E2E Testing Sub-orchestrator.
  - Design opaque-box test infrastructure and tests (Tiers 1-4: Feature, Boundary, Pairwise, Real-World application scenarios).
  - Produce `TEST_READY.md`.
- **Track 2: Implementation Track**
  - Sub-orchestrator M1: In-Process `TTSEngine` and blocking `NativeAudioSink`.
  - Sub-orchestrator M2: Thin Client IPC via UNIX Sockets (`speak.py` and daemon socket listener).
  - Sub-orchestrator M3: Architectural cleanup & dead code deletion (`run_speak.sh`, `PipelineOrchestrator`, `ShortPathStrategy`, `MpvController`, `SessionDir`).

## Phase 2: Final Verification & Adversarial Hardening
- Final Milestone Phase 1: Pass 100% of E2E tests (Tiers 1-4).
- Final Milestone Phase 2: Adversarial coverage hardening (Tier 5) with Challenger stress testing.
- Forensic Integrity Audit pass.
- Final Report and Victory Claim to Parent.
