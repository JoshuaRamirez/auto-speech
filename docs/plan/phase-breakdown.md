# Phase Breakdown — Concrete Tasks

Each task lists the artifact it produces. Tasks within a phase are ordered;
the phase's check gate is the final task.

---

## Phase 0 — Environment setup

| # | Task | Artifact |
|---|---|---|
| 0.1 | Write micro-design stub for phase 0 | `docs/micro-design/phase-0-environment.md` |
| 0.2 | Decide package manager (uv preferred; pip fallback) | noted in micro-design |
| 0.3 | Write `install.sh` — creates venv, installs mlx, mlx-audio | `setup/install.sh` |
| 0.4 | Write `verify.sh` — synthesizes "hello" → `/tmp/verify.wav` | `setup/verify.sh` |
| 0.5 | Run `install.sh` (after user confirms ~few-hundred-MB download) | venv + kokoro weights on disk |
| 0.6 | Run `verify.sh`; confirm audible WAV | phase gate passed |

---

## Phase 1 — Voice calibration

| # | Task | Artifact |
|---|---|---|
| 1.1 | Write micro-design for phase 1 | `docs/micro-design/phase-1-calibration.md` |
| 1.2 | Create reference prose (~500 chars, neutral register) | `tests/reference/calibration_prose.txt` |
| 1.3 | Write `voice_profile.py` (dataclass) | `plugin/scripts/python/voice_profile.py` |
| 1.4 | Write `calibration_run.py` (dataclass) | `plugin/scripts/python/calibration_run.py` |
| 1.5 | Write `voice_profile_store.py` (load/save JSON) | `plugin/scripts/python/voice_profile_store.py` |
| 1.6 | Write `wav_inspector.py` | `plugin/scripts/python/wav_inspector.py` |
| 1.7 | Write `calibrator.py` | `plugin/scripts/python/calibrator.py` |
| 1.8 | Run calibrator; inspect JSON | `config/voice_calibration.json` populated |

---

## Phase 2 — Transcript extraction

| # | Task | Artifact |
|---|---|---|
| 2.1 | Write micro-design for phase 2 | `docs/micro-design/phase-2-extraction.md` |
| 2.2 | Inspect an actual `~/.claude/projects/...jsonl` to confirm schema | notes in micro-design |
| 2.3 | Write `assistant_message.py` (dataclass) | `plugin/scripts/python/assistant_message.py` |
| 2.4 | Write `transcript_reader.py` (line-by-line JSON yield) | `plugin/scripts/python/transcript_reader.py` |
| 2.5 | Write `transcript_locator.py` (cwd slug + newest jsonl) | `plugin/scripts/python/transcript_locator.py` |
| 2.6 | Write `message_selector.py` (Nth-from-end qualifying) | `plugin/scripts/python/message_selector.py` |
| 2.7 | Manual run: locate + select 1st + 2nd for this session | phase gate passed |

---

## Phase 3 — Audio-friendly rewriter prompt

| # | Task | Artifact |
|---|---|---|
| 3.1 | Write micro-design for phase 3 | `docs/micro-design/phase-3-rewriter.md` |
| 3.2 | Draft rewrite prompt v1 | prompt in micro-design |
| 3.3 | Test on sample prose response; inspect output | notes in micro-design |
| 3.4 | Test on response containing code block; refine prompt | updated prompt |
| 3.5 | Test on response with table; refine prompt | updated prompt |
| 3.6 | Freeze prompt contract; install in `plugin/commands/speak.md` (draft) | `plugin/commands/speak.md` |

---

## Phase 4 — Chunk planner

| # | Task | Artifact |
|---|---|---|
| 4.1 | Write micro-design for phase 4 | `docs/micro-design/phase-4-chunk-planner.md` |
| 4.2 | Write `fibonacci.py` (generator class) | `plugin/scripts/python/fibonacci.py` |
| 4.3 | Write `boundary_snapper.py` | `plugin/scripts/python/boundary_snapper.py` |
| 4.4 | Write `duration_estimator.py` | `plugin/scripts/python/duration_estimator.py` |
| 4.5 | Write `chunk_descriptor.py` (dataclass) | `plugin/scripts/python/chunk_descriptor.py` |
| 4.6 | Write `chunk_plan.py` (dataclass) | `plugin/scripts/python/chunk_plan.py` |
| 4.7 | Write `audio_transcript.py` (dataclass) | `plugin/scripts/python/audio_transcript.py` |
| 4.8 | Write `chunk_planner.py` | `plugin/scripts/python/chunk_planner.py` |
| 4.9 | Self-test: feed a 2000-char string; assert concat equals source | phase gate passed |

---

## Phase 5 — TTS engine + producer

| # | Task | Artifact |
|---|---|---|
| 5.1 | Write micro-design for phase 5 | `docs/micro-design/phase-5-tts-engine.md` |
| 5.2 | Write `tts_engine.py` (wraps mlx-audio; atomic write) | `plugin/scripts/python/tts_engine.py` |
| 5.3 | Write `audio_segment.py` (dataclass) | `plugin/scripts/python/audio_segment.py` |
| 5.4 | Write `segment_producer.py` | `plugin/scripts/python/segment_producer.py` |
| 5.5 | Self-test: 3-chunk plan → 3 WAVs in tmpdir | phase gate passed |

---

## Phase 6 — Playback consumer

| # | Task | Artifact |
|---|---|---|
| 6.1 | Write micro-design for phase 6 | `docs/micro-design/phase-6-playback.md` |
| 6.2 | Write `playback_queue.py` (wraps `queue.Queue` + close) | `plugin/scripts/python/playback_queue.py` |
| 6.3 | Write `afplay_launcher.py` | `plugin/scripts/python/afplay_launcher.py` |
| 6.4 | Write `playback_consumer.py` | `plugin/scripts/python/playback_consumer.py` |
| 6.5 | Self-test: hand-built queue of 3 WAVs plays in order | phase gate passed |

---

## Phase 7 — Orchestrator

| # | Task | Artifact |
|---|---|---|
| 7.1 | Write micro-design for phase 7 | `docs/micro-design/phase-7-orchestrator.md` |
| 7.2 | Write `config_constants.py` | `plugin/scripts/python/config_constants.py` |
| 7.3 | Write `short_path.py` | `plugin/scripts/python/short_path.py` |
| 7.4 | Write `pipeline.py` (PipelineOrchestrator) | `plugin/scripts/python/pipeline.py` |
| 7.5 | Write `speak.py` (CLI entry) | `plugin/scripts/python/speak.py` |
| 7.6 | Stdin smoke test: short input → short path | phase gate (short) |
| 7.7 | Stdin smoke test: long input → Fibonacci path | phase gate (long) |

---

## Phase 8 — Plugin integration

| # | Task | Artifact |
|---|---|---|
| 8.1 | Write micro-design for phase 8 | `docs/micro-design/phase-8-plugin.md` |
| 8.2 | Write `plugin.json` manifest | `plugin/.claude-plugin/plugin.json` |
| 8.3 | Finalize `speak.md` — combine rewrite prompt + speak.py invocation | `plugin/commands/speak.md` |
| 8.4 | Install plugin locally; verify registration | plugin appears in Claude Code |

---

## Phase 9 — E2E validation

| # | Task | Artifact |
|---|---|---|
| 9.1 | Write `manual_test_plan.md` | `tests/manual_test_plan.md` |
| 9.2 | Run `/speak` on a short prose response; save tmpdir | `tests/outputs/<ts>-short/` |
| 9.3 | Run `/speak` on a long response with code; save tmpdir | `tests/outputs/<ts>-long-code/` |
| 9.4 | Run `/speak 3` on a response 3 back; save tmpdir | `tests/outputs/<ts>-ordinal/` |
| 9.5 | Capture NFR measurements; write retrospective | `docs/plan/retrospective.md` |
