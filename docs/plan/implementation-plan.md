# Implementation Plan

## Execution principle

Each phase is an **Evolution cycle** in macro-OOAD. Within every phase we run
the **micro process** explicitly:

> **M1. Identify classes/objects** at the phase's abstraction level.
> **M2. Identify semantics** — each class's responsibility, in one sentence.
> **M3. Identify relationships** — what calls what; composition vs. aggregation.
> **M4. Specify interface & implementation** — method signatures, then body.

The output of M1–M3 goes into the per-phase micro-design doc
(`docs/micro-design/phase-N-<topic>.md`). M4 produces the actual code.

Each phase exits with a "check" gate — evidence that the phase's deliverables
work — before moving on. This is the Check and Act in the user's standing PDCA
discipline.

## Phase sequencing (dependency-driven)

```
Phase 0  environment        ──┐
                              ├─► Phase 1  calibration
                              │              │
Phase 2  transcript ──────────┤              │
Phase 3  rewriter prompt ─────┤              │
Phase 4  chunk planner ───────┼──────────────┤
                              │              │
Phase 5  TTS engine + producer┘              │
                                             │
Phase 6  playback consumer ──────────────────┤
                                             │
                              Phase 7  orchestrator
                                             │
                              Phase 8  plugin integration
                                             │
                              Phase 9  e2e test
```

Phases 2, 3, 4 are independent of each other and could be parallelized in
a multi-engineer setting; here they run sequentially to keep context focused.

## Phases

### Phase 0 — Environment setup
**Goal:** mlx-audio + Kokoro installed and a one-line `TTSEngine.synthesize` call
succeeds against a known input.
**Micro-process stub:**
- M1/M2: identify `install.sh`, `verify.sh`. No classes yet.
- M3: scripts depend on `uv` or `pip`, Homebrew for ffmpeg if needed.
- M4: write scripts; run them; run verify.
**Check gate:** A 10-character test string produces a playable WAV under `/tmp`.
**Deliverables:** `setup/install.sh`, `setup/verify.sh`, `docs/micro-design/phase-0-environment.md`.

### Phase 1 — Voice calibration
**Goal:** measured `chars_per_second` for the default voice persists to
`config/voice_calibration.json`.
**Micro-process stub:**
- M1: `VoiceProfile`, `VoiceProfileStore`, `CalibrationRun`, `Calibrator`,
  `WavInspector`.
- M2: see [class-inventory.md](../specification/artifacts/class-inventory.md).
- M3: `Calibrator` uses `TTSEngine` + `WavInspector`; writes via `VoiceProfileStore`.
- M4: write classes; run calibrator; inspect persisted JSON.
**Check gate:** `python -m plugin.scripts.python.calibrator` produces a JSON file
with `chars_per_second` in the range 10–25 (sanity band for Kokoro).
**Deliverables:** 5 Python files, reference prose file, micro-design doc.

### Phase 2 — Transcript extraction
**Goal:** locate the active JSONL and return the 1st or Nth most recent
`AssistantMessage`.
**Micro-process stub:**
- M1: `TranscriptLocator`, `TranscriptReader`, `MessageSelector`, `AssistantMessage`.
- M2: see class inventory.
- M3: `MessageSelector` consumes `TranscriptReader`; `TranscriptLocator`
  independent.
- M4: write classes; manually run against a real `~/.claude/projects/...` JSONL.
**Check gate:** Given a real JSONL, return the most-recent and 2nd-most-recent
assistant messages; visually verify their text matches what I see in Claude Code.
**Deliverables:** 4 Python files, micro-design doc.

### Phase 3 — Audio-friendly rewriter prompt
**Goal:** the slash command's rewrite prompt is finalized, tested against a
variety of sample messages (prose, code, table, list), and produces fluent
spoken output losslessly.
**Micro-process stub:**
- M1: no runtime classes; the artifact is the prompt itself.
- M2/M3: N/A.
- M4: write `plugin/commands/speak.md` with the prompt contract; run it on 3
  sample Claude messages; inspect outputs.
**Check gate:** Three sample messages rewritten; each preserves every named
entity, number, and example; reads naturally.
**Deliverables:** `plugin/commands/speak.md` (draft), prompt test log in micro-design doc.

### Phase 4 — Fibonacci chunk planner
**Goal:** given an `AudioTranscript` and a `VoiceProfile`, emit a `ChunkPlan`
with the boundary-snap invariant holding.
**Micro-process stub:**
- M1: `FibonacciSeq`, `BoundarySnapper`, `DurationEstimator`, `ChunkPlanner`,
  `ChunkDescriptor`, `ChunkPlan`, `AudioTranscript`.
- M2: see class inventory.
- M3: `ChunkPlanner` composes the L2 primitives + VoiceProfile.
- M4: write classes; unit-test: `"".join(d.text for d in plan) == transcript.text`.
**Check gate:** Property test on 20 random strings confirms lossless concat;
manual inspection of a real rewrite shows sane chunk sizes.
**Deliverables:** 7 Python files, micro-design doc.

### Phase 5 — TTS engine + producer
**Goal:** `SegmentProducer` turns a `ChunkPlan` into `AudioSegment`s enqueued on
a `PlaybackQueue`; the `TTSEngine` class is production-ready.
**Micro-process stub:**
- M1: `TTSEngine`, `AudioSegment`, `SegmentProducer`, `WavInspector` (reused).
- M2: see class inventory.
- M3: `SegmentProducer` → `TTSEngine` → disk; enqueues atomically after
  rename-from-temp.
- M4: write classes; manually generate 3-chunk plan and verify files + queue
  ordering.
**Check gate:** Run a fake plan through the producer; inspect tmpdir; WAVs are
atomic and ordered.
**Deliverables:** 4 Python files, micro-design doc.

### Phase 6 — Playback consumer
**Goal:** `PlaybackConsumer` drains a queue and plays each WAV via `afplay`
with clean cancellation semantics.
**Micro-process stub:**
- M1: `PlaybackQueue` (wrapper), `PlaybackConsumer`, `AfplayLauncher`.
- M2: see class inventory.
- M3: consumer ↔ queue; consumer spawns afplay subprocess.
- M4: write classes; feed a hand-built queue of 3 WAVs; listen end-to-end.
**Check gate:** Three WAVs play in order with no audible gaps > 150 ms.
**Deliverables:** 3 Python files, micro-design doc.

### Phase 7 — Pipeline orchestrator
**Goal:** `PipelineOrchestrator.run(n)` wires every service; `ShortPathStrategy`
handles brief inputs; `speak.py` is the CLI entry.
**Micro-process stub:**
- M1: `PipelineOrchestrator`, `ShortPathStrategy`, `speak.py` (entry).
- M2: see class inventory.
- M3: orchestrator composes all L3 services + manages tmpdir + threads.
- M4: write classes; smoke-test with a fake pre-rewritten transcript from stdin.
**Check gate:** `echo "Hello world." | speak.py --stdin` plays "Hello world.";
a multi-paragraph stdin triggers Fibonacci path and plays cleanly.
**Deliverables:** 3 Python files, micro-design doc.

### Phase 8 — Plugin integration
**Goal:** `/speak` and `/speak n` work as slash commands inside Claude Code.
**Micro-process stub:**
- M1: plugin manifest, slash command.
- M2: `plugin.json` identifies the plugin; `speak.md` is the command body.
- M3: the command body contains the rewrite prompt + invocation of `speak.py`.
- M4: write `plugin.json` + finalize `speak.md`.
**Check gate:** From a live Claude Code session, invoke `/speak` and hear the
most-recent assistant message; `/speak 2` works; error paths fire clearly.
**Deliverables:** `plugin/.claude-plugin/plugin.json`, finalized
`plugin/commands/speak.md`, micro-design doc.

### Phase 9 — End-to-end validation
**Goal:** Capture a representative run's artifacts and confirm all NFRs.
**Micro-process stub:**
- M1/M2/M3: N/A.
- M4: run against 3 real Claude responses (short prose, long with code, mid with
  table); save tmpdirs to `tests/outputs/<timestamped>/`.
**Check gate:** NFR table in 01-conceptualization passes for each scenario.
**Deliverables:** `tests/manual_test_plan.md`, captured outputs, micro-design doc.

## Act (post-completion)

After Phase 9 successfully gates, a single retrospective note in
`docs/plan/retrospective.md` captures:
- What took longer than expected.
- What the calibration revealed about Kokoro's actual throughput.
- What the first listening session surfaced for `BASE_DURATION_SECONDS`.
- Any emergent ADRs (e.g., bumping N from 4 → 5).
