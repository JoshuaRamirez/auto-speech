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

### Phase 10 — Mandatory WAV concatenation (post-v0.1)
**Goal:** every successful run leaves exactly one `full.wav`; chunks are
deleted unless `--keep-artifacts`. Short path renames to `full.wav`.
Supersedes (in spirit) v0.1's "nothing persists on success" asymmetry
between paths.
**Micro-process stub:**
- M1: `WavConcatenator` (L1 I/O adapter).
- M2: see phase-10 micro-design.
- M3: orchestrator calls concatenator post-join (long), renames (short).
- M4: stdlib `wave`; write-then-rename atomicity.
**Check gate:** `--keep-artifacts` run shows only `full.wav` (short) or
chunks + `full.wav` (long); frame-count sum invariant verified.
**Deliverables:** `wav_concatenator.py`, edits to `pipeline.py` and
`short_path.py`, test, micro-design doc, ADR-007.

### Phase 11 — Source-hash cache + /replay (post-v0.1)
**Goal:** a run whose source text hashes to an existing cache entry skips
the entire pipeline and plays the cached `full.wav`. `/replay` slash
command plays the most recent cache entry without consulting the transcript.
**Micro-process stub:**
- M1: `CacheStore`, `CacheEntry`. New slash command `/replay`.
- M2: see phase-11 micro-design.
- M3: `pipeline.py` checks store before full run; promotes tmpdir
  `full.wav` into cache on successful miss-runs.
- M4: cache dir `config/cache/<hash>/{full.wav, meta.json}`.
**Check gate:** Two consecutive `/speak` invocations on the same source
skip TTS on the second; `/replay` plays the most-recent.
**Deliverables:** `cache_store.py`, `cache_entry.py`, `replay.md`
slash command, `run_replay.sh`, edits to `speak.py`/`pipeline.py`,
micro-design, ADR-008.

### Phase 12 — mpv-based seekable playback (post-v0.1)
**Goal:** swap `afplay` for `mpv` so playback supports pause, resume,
seek (absolute and relative), jump-to-start, jump-to-end. The mpv
process is long-lived across slash-command invocations via a JSON-IPC
Unix socket.
**Micro-process stub:**
- M1: `MpvController` (L3 service), `MpvIpc` (L1 adapter). New slash
  commands `/pause`, `/resume`, `/seek`, etc.
- M2: see phase-12 micro-design.
- M3: `MpvController` owns the mpv subprocess lifecycle + socket.
  `PlaybackConsumer` routes through it for long-path, `ShortPathStrategy`
  likewise.
- M4: mpv `--input-ipc-server=<sock>`; JSON command line protocol.
**Check gate:** During a long playback, `/pause` stops audio; `/resume`
resumes from the same offset; `/seek +15` fast-forwards; `/seek end`
jumps to the last second.
**Deliverables:** `mpv_controller.py`, `mpv_ipc.py`, the new slash
commands, Homebrew install note in `setup/install.sh`, micro-design,
ADR-009.

### Phase 13 — Localhost web UI (post-v0.1)
**Goal:** a Flask server bound to 127.0.0.1:7860 exposes the existing
pipeline + cache + mpv control surface through HTTP, with a single-page
UI for paste-and-speak. Holds one TTSEngine for the life of the process
so requests after the first skip the model load.
**Micro-process stub:**
- M1: `WebServer` (L4 service, single class). Refactor `PipelineOrchestrator`
  to accept an injected `TTSEngine`.
- M2: see phase-13 micro-design.
- M3: routes call existing services; thread-lock serializes pipeline-running
  endpoints.
- M4: Flask + a single static index.html (inline CSS/JS).
**Check gate:** Page loads at `http://127.0.0.1:7860/`; pasting text
triggers a cache-miss run; second paste of same text hits cache; mpv
controls work from the UI.
**Deliverables:** `web_server.py`, `index.html`, `run_server.sh`,
edits to `pipeline.py` and `install.sh`, micro-design, ADR-010.

## Act (post-completion)

After Phase 9 successfully gated, a single retrospective note in
`docs/plan/retrospective.md` captured:
- What took longer than expected.
- What the calibration revealed about Kokoro's actual throughput.
- What the first listening session surfaced for `BASE_DURATION_SECONDS`.
- Any emergent ADRs (e.g., bumping N from 4 → 5).

Phases 10–12 add a second retrospective entry after Phase 12's check
gate: lessons about always-concat ergonomics, cache hit rate in real
usage, and mpv-controller lifecycle quirks.
