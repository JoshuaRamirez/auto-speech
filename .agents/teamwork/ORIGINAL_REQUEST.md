# Original User Request

## 2026-10-03T17:45:27Z

# Teamwork Project Prompt — Draft

> Status: Ready for launch — awaiting user approval.
> Goal: Craft prompt → get user approval → delegate to teamwork_preview
> Requested team: The full multi-agent teamwork system

Refactor the `auto-speech` architecture into a Unified Daemon Server. Eliminate the `run_speak.sh` process sprawl, integrate the MLX Kokoro TTS engine in-process for zero-latency streaming, and replace the detached `mpv` and brittle `time.sleep` hacks with a clean, synchronous `NativeAudioSink`.

Working directory: /Users/joshua/Developer/auto-speech
Integrity mode: development

## Requirements

### R1. In-Process TTSEngine and Blocking AudioSink
Refactor `narrator_service.py` to instantiate `TTSEngine` directly in-process. Create a `NativeAudioSink` that plays the generated WAV files synchronously using `subprocess.run(["mpv", "--really-quiet", ...])`, completely removing the `run_speak.sh` script, the detached `mpv` background session logic, the `time.sleep(duration)` calculations, and the `SIGKILL` hacks.

### R2. Thin Client IPC via UNIX Sockets
Refactor `speak.py` into a thin CLI client that reads `stdin` and forwards the text to the daemon. In `narrator_service.py`, run a background thread using Python's `socketserver` to listen on a UNIX domain socket (e.g., `/tmp/auto-speech-daemon.sock`), enqueueing incoming speech requests into the main `_tts_queue`.

### R3. Remove Dead Architectural Sprawl
Delete obsolete files including `run_speak.sh`, `PipelineOrchestrator`, `ShortPathStrategy`, `MpvController`, and the `SessionDir` PID management, as the daemon now acts as the single authoritative owner of the audio hardware.

## Acceptance Criteria

### Execution & Architecture
- [ ] `narrator_service.py` boots successfully, loads `TTSEngine` into memory once, and synthesizes tool narrations without spinning up secondary Python interpreters.
- [ ] Tool narrations play sequentially via `mpv` in blocking mode; the Python thread naturally waits for the audio to finish without `time.sleep()` hacks.
- [ ] Manual test: Running `echo "test" | python3 plugin/scripts/python/speak.py` successfully sends text to the daemon's UNIX socket, which synthesizes and plays it without overlapping existing audio.
- [ ] No detached `mpv` processes are left running in the background after playback finishes.


## 2026-10-04T07:45:12Z

Investigate, analyze, and design the architectural sublimation of auto-speech into a unified, frictionless system. Produce a comprehensive Design RFC and phased migration blueprint that unifies audio ownership, integrates daemon-native disk caching, collapses multi-hop subprocess chains into direct client calls, and replaces disk-based FIFO polling with in-memory arbitration, all while preserving 100% compatibility across the existing 115 tests.

Working directory: /Users/joshua/Developer/auto-speech
Integrity mode: development

## Requirements

### R1. Comprehensive Architecture Audit & Friction Inventory
Conduct an exhaustive forensic audit of all existing communication pathways between callers (mcp_server.py, say_worker.py, autoplay_worker.py, speak.py), the daemon (narrator_service.py), and the audio sink (native_audio_sink.py, mpv). Detail every subprocess invocation, temporary disk artifact, redundant queue, and lock contention point.

### R2. Sublimated Architectural Specification (Design RFC)
Design the unified steady-state architecture adhering to four core principles:
1. Single Audio Owner: The daemon exclusively owns NativeAudioSink and device playback. No external CLI script or worker spawns mpv directly.
2. Daemon-Native Caching: The daemon natively integrates with CacheStore (config/cache/<hash_prefix>/full.wav), checking cache hits on arrival and promoting synthesized audio on misses.
3. Collapsed Subprocess Chains: Eliminate intermediate script hops (e.g. mcp_server spawning say_worker spawning speak.py) in favor of direct, lightweight in-process client calls to the daemon socket.
4. In-Memory Priority Arbitration: Replace disk-based polling (PlaybackFifo and /tmp/auto-speech-narration-depth) with in-memory priority queueing inside the daemon (User Interrupt > Explicit MCP > Autoplay > Tool Narration).

### R3. Protocol & Component Schema Definitions
Specify concrete, type-checked schemas for:
- The daemon UNIX socket protocol (structured JSON payloads for actions: speak, play_cache, interrupt, status).
- The Python client library interface (DaemonClient).
- The internal daemon state machine and priority queue transitions.

### R4. Phased Migration & Regression Strategy
Formulate a zero-downtime, phase-by-phase implementation roadmap that maintains 100% test passing status across all 115 tests (41 unit/regression suites and 74 E2E test suites in Tiers 1–5) at every intermediate milestone.

## Acceptance Criteria

### Deliverable Completeness
- [ ] A formal architectural RFC document is produced in reports/ following the timestamped naming convention (AutoSpeech-Sublimation-RFC-YYYY-MM-DD-HHMMSS.html or .md).
- [ ] The friction inventory explicitly maps every file in plugin/scripts/python/ and plugin/scripts/shell/ to its sublimated target (Retain, Consolidate, or Retire).
- [ ] The socket protocol specifies exact request/response schemas, error handling, and timeout behavior.
- [ ] The caching specification demonstrates how source_hash flows from callers through CacheStore promotion without temporary file leakage.
- [ ] The migration plan breaks implementation into verified milestones with specific test-suite verification gates for each milestone.
- [ ] Zero modifications are made to tracked production code files during this design phase.


## 2026-10-04T08:46:26Z

Execute the complete end-to-end architectural sublimation of auto-speech across all 4 migration phases (M1 through M4) strictly per the ratified Design RFC (reports/AutoSpeech-Sublimation-RFC-2026-10-04-074610.md). Unify audio playback under the central daemon, integrate closed-loop CacheStore disk caching, collapse caller subprocess chains into direct in-process DaemonClient socket calls, and replace on-disk FIFO polling with an in-memory priority queue, maintaining 100% passing status across all 115 tests and 0 ruff lint errors at every milestone.

Working directory: /Users/joshua/Developer/auto-speech
Integrity mode: development

Reference material: reports/AutoSpeech-Sublimation-RFC-2026-10-04-074610.md

## Requirements

### R1. Phase M1: In-Memory Priority Queue & Hardened Dual-Wire Socket Server
Implement the 4-tier in-memory priority queue (P1 User Interrupt > P2 Explicit MCP > P3 Autoplay > P4 Tool Narration) and the hardened dual-wire socket server in narrator_service.py. Include the duck-typed QueueProxyFacade on _tts_queue (get_nowait, put_nowait, maxsize) to guarantee full backward compatibility with legacy unit tests.

### R2. Phase M2: Single Audio Owner & Daemon-Native CacheStore
Unify all audio playback under the daemon's NativeAudioSink. Wire closed-loop caching directly into synthesis: lookup config/cache/<hash_prefix>/full.wav on incoming requests with source_hash, and promote synthesized audio via CacheStore.promote() before playback on cache misses. Ensure all bisected fragment WAVs are cleaned up without leaking into /tmp.

### R3. Phase M3: Caller Sublimation & In-Process DaemonClient
Implement the high-performance, in-process DaemonClient Python library with connection pooling and retries. Refactor mcp_server.py and autoplay_worker.py to talk directly to the daemon socket via DaemonClient, eliminating intermediate subprocess hops (say_worker.py and speak.py cascades) while preserving offline fallbacks.

### R4. Phase M4: Legacy Scaffolding Deprecation & Final Verification
Retire obsolete on-disk coordination loops and detached scripts per the RFC inventory. Enforce that all 115 test suites pass cleanly with zero regressions and zero lint violations.

## Verification Resources
- Unit / Regression Suite: bash tests/run_all.sh (must pass 41/41 suites)
- E2E Test Suite: .venv/bin/python tests/e2e/run_e2e.py (must pass 74/74 tests across Tiers 1–5)
- Lint & Code Quality: .venv/bin/ruff check . (must report 0 errors)

## Acceptance Criteria

### Execution & Verification Integrity
- [ ] All 4 milestones (M1–M4) are implemented in sequence with programmatic test gates verified after each phase.
- [ ] Multi-hop subprocess chains (mcp_server → say_worker → speak.py) are collapsed into direct DaemonClient calls.
- [ ] When the daemon is active, no external script or caller directly spawns mpv.
- [ ] Cache misses successfully populate config/cache/<hash_prefix>/full.wav via CacheStore.promote().
- [ ] All 115 tests pass (41/41 unit/regression suites and 74/74 E2E tests) on the final tree.
- [ ] .venv/bin/ruff check . returns 0 violations.
