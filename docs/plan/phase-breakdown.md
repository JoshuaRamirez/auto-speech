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

---

## Phase 10 — Mandatory WAV concatenation (post-v0.1)

| # | Task | Artifact |
|---|---|---|
| 10.1 | Write ADR-007 (mandatory concat + cache-centric artifact) | `docs/decisions/ADR-007-mandatory-concat-and-cache-centric-artifact.md` |
| 10.2 | Write micro-design for phase 10 | `docs/micro-design/phase-10-concat.md` |
| 10.3 | Write `wav_concatenator.py` | `plugin/scripts/python/wav_concatenator.py` |
| 10.4 | Write unit test: concat frame-count sum + format parity | `tests/test_wav_concat.py` |
| 10.5 | Edit `short_path.py` — rename output to `full.wav` | `plugin/scripts/python/short_path.py` |
| 10.6 | Edit `pipeline.py` — call concat post-join; conditional chunk cleanup | `plugin/scripts/python/pipeline.py` |
| 10.7 | Run long-path test with `--keep-artifacts`; verify chunks + `full.wav` both present | manual gate |
| 10.8 | Run short-path test with `--keep-artifacts`; verify only `full.wav` | manual gate |
| 10.9 | Commit phase 10 | git commit |

---

## Phase 11 — Source-hash replay cache (post-v0.1)

| # | Task | Artifact |
|---|---|---|
| 11.1 | Write ADR-008 (source-hash replay cache) | `docs/decisions/ADR-008-source-hash-replay-cache.md` |
| 11.2 | Write micro-design for phase 11 | `docs/micro-design/phase-11-cache-replay.md` |
| 11.3 | Write `cache_entry.py` (dataclass) | `plugin/scripts/python/cache_entry.py` |
| 11.4 | Write `cache_store.py` (lookup / promote / list by mtime) | `plugin/scripts/python/cache_store.py` |
| 11.5 | Add `--source-hash` to `speak.py`; extend `pipeline.py` to consult/promote cache | edits |
| 11.6 | Edit `plugin/commands/speak.md` — compute sha256 of source, pass as flag | `plugin/commands/speak.md` |
| 11.7 | Write `plugin/commands/replay.md` slash command | `plugin/commands/replay.md` |
| 11.8 | Write `plugin/scripts/shell/run_replay.sh` | shell wrapper |
| 11.9 | Write `plugin/scripts/python/replay.py` — play most-recent cache entry | entry |
| 11.10 | Manual gate: run `/speak` twice on same source; second skips TTS | gate |
| 11.11 | Manual gate: `/replay` plays most recent cache entry | gate |
| 11.12 | Commit phase 11 | git commit |

---

## Phase 12 — mpv-based seekable playback (post-v0.1)

| # | Task | Artifact |
|---|---|---|
| 12.1 | Write ADR-009 (mpv controller) | `docs/decisions/ADR-009-mpv-controller.md` |
| 12.2 | Write micro-design for phase 12 | `docs/micro-design/phase-12-mpv-controller.md` |
| 12.3 | Update `setup/install.sh` to install mpv (brew) | edits |
| 12.4 | Write `mpv_ipc.py` (JSON line protocol over Unix socket) | `plugin/scripts/python/mpv_ipc.py` |
| 12.5 | Write `mpv_controller.py` (subprocess lifecycle + control surface) | `plugin/scripts/python/mpv_controller.py` |
| 12.6 | Edit `playback_consumer.py` / `short_path.py` to route through controller | edits |
| 12.7 | Write slash commands `pause.md`, `resume.md`, `seek.md`, `restart.md`, `end.md` | commands |
| 12.8 | Write `plugin/scripts/shell/run_control.sh` | shell wrapper |
| 12.9 | Write `plugin/scripts/python/control.py` — send a single control op | entry |
| 12.10 | Manual gate: during a long playback, pause / resume / seek / end each behave | gate |
| 12.11 | Commit phase 12 | git commit |

---

## Phase 13 — Localhost web UI

| # | Task | Artifact |
|---|---|---|
| 13.1 | Write ADR-010 (localhost web UI) | `docs/decisions/ADR-010-localhost-web-ui.md` |
| 13.2 | Write micro-design for phase 13 | `docs/micro-design/phase-13-web-server.md` |
| 13.3 | Refactor `PipelineOrchestrator.__init__` to accept `tts_engine` param | edits |
| 13.4 | Add `flask` to `setup/install.sh` and install into the venv | edits |
| 13.5 | Write `plugin/scripts/python/web_server.py` (WebServer class + main) | new |
| 13.6 | Write `plugin/web/templates/index.html` (UI) | new |
| 13.7 | Write `plugin/scripts/shell/run_server.sh` | new |
| 13.8 | Manual gate: server boots, model pre-warmed, `/api/status` returns | gate |
| 13.9 | Manual gate: Speak → cache miss; Speak again → cache hit | gate |
| 13.10 | Manual gate: Pause/Resume/Seek/Restart/End all work from UI | gate |
| 13.11 | Manual gate: Cache list populates and Replay-from-cache works | gate |
| 13.12 | Commit phase 13 | git commit |

---

## Phase 14 — Web rewriter via Claude CLI

| # | Task | Artifact |
|---|---|---|
| 14.1 | Write ADR-011 (CLI vs API vs local-LM) | `docs/decisions/ADR-011-claude-cli-rewriter.md` |
| 14.2 | Write micro-design for phase 14 | `docs/micro-design/phase-14-claude-cli-rewriter.md` |
| 14.3 | Extract 12-rule prompt to a shared file | `plugin/prompts/audio_rewrite_prompt.txt` |
| 14.4 | Write `claude_cli_rewriter.py` (subprocess wrapper) | `plugin/scripts/python/claude_cli_rewriter.py` |
| 14.5 | Edit `web_server.py`: load rewriter; rewrite branch in `_handle_speak`; cache-key mode suffix | edits |
| 14.6 | Edit `index.html`: add rewrite checkbox; pass flag in POST | edits |
| 14.7 | Smoke test: rewrite-on novel paste → markdown stripped in audio | gate |
| 14.8 | Smoke test: rewrite-on repeat → cache hit, no claude invocation | gate |
| 14.9 | Smoke test: rewrite-off novel paste → distinct cache key, literal speech | gate |
| 14.10 | Smoke test: rewrite-on with `claude` removed from PATH → loud error | gate |
| 14.11 | Commit phase 14 | git commit |

---

## Phase 15 — Autoplay-on-Stop hook

| # | Task | Artifact |
|---|---|---|
| 15.1 | Write ADR-012 (Stop-hook autoplay) | `docs/decisions/ADR-012-stop-hook-autoplay.md` |
| 15.2 | Write micro-design for phase 15 | `docs/micro-design/phase-15-stop-hook-autoplay.md` |
| 15.3 | Add `jq` check to `setup/install.sh` (brew-install if missing) | edits |
| 15.4 | Write `autoplay_hook.sh` (entrypoint, fast checks, spawn worker) | `plugin/scripts/shell/autoplay_hook.sh` |
| 15.5 | Write `autoplay_worker.sh` (detached worker; rewrite + speak) | `plugin/scripts/shell/autoplay_worker.sh` |
| 15.6 | Write `setup/install-hook.sh` (idempotent jq-edit of settings.json) | `setup/install-hook.sh` |
| 15.7 | Write `setup/uninstall-hook.sh` (idempotent removal) | `setup/uninstall-hook.sh` |
| 15.8 | Write `/autoplay-on` and `/autoplay-off` slash commands | `plugin/commands/autoplay-on.md`, `plugin/commands/autoplay-off.md` |
| 15.9 | Update `setup/install-plugin.sh` to symlink the new commands | edits |
| 15.10 | Smoke test: install/uninstall idempotent | gate |
| 15.11 | Smoke test: marker fast-path skips work | gate |
| 15.12 | Smoke test: cache hit triggers playback | gate |
| 15.13 | Smoke test: cache miss triggers `claude -p` + audio | gate |
| 15.14 | Smoke test: staleness check skips superseded worker | gate |
| 15.15 | Commit phase 15 | git commit |

---

## Phase 16 — `/auto-speech-app` slash command launcher

| # | Task | Artifact |
|---|---|---|
| 16.1 | Write ADR-013 (slash-command webapp launcher) | `docs/decisions/ADR-013-slash-command-webapp-launcher.md` |
| 16.2 | Write micro-design for phase 16 | `docs/micro-design/phase-16-slash-command-webapp-launcher.md` |
| 16.3 | Write `start_webapp.sh` (idempotent, double-fork, pidfile, health poll) | `plugin/scripts/shell/start_webapp.sh` |
| 16.4 | Write `/auto-speech-app` slash command | `plugin/commands/auto-speech-app.md` |
| 16.5 | Update `setup/install-plugin.sh` to symlink the new command | edits |
| 16.6 | Smoke test: with server down → spawns + responds | gate |
| 16.7 | Smoke test: with server up → "already running" | gate |
| 16.8 | Smoke test: stale pidfile cleaned + fresh spawn | gate |
| 16.9 | Smoke test: detachment survives shell exit | gate |
| 16.10 | Commit phase 16 | git commit |

---

## Phase 17 — Fire-and-forget `/api/speak`

| # | Task | Artifact |
|---|---|---|
| 17.1 | Write ADR-014 (fire-and-forget speak) | `docs/decisions/ADR-014-fire-and-forget-speak.md` |
| 17.2 | Write micro-design for phase 17 | `docs/micro-design/phase-17-fire-and-forget-speak.md` |
| 17.3 | Write `job_state.py` (Job dataclass + Phase constants) | `plugin/scripts/python/job_state.py` |
| 17.4 | Write `job_tracker.py` (thread-safe single-job holder) | `plugin/scripts/python/job_tracker.py` |
| 17.5 | Refactor `_handle_speak`: cache-hit sync, miss → submit + 202 | edits |
| 17.6 | Add `_run_speak_job` background runner with phase transitions | edits |
| 17.7 | Extend `_handle_status` with `job` field | edits |
| 17.8 | Update `index.html`: 202/409 handling, job phase rendering | edits |
| 17.9 | Smoke test: cache hit returns 200 unchanged | gate |
| 17.10 | Smoke test: cache miss returns 202 with job, then status reports phases | gate |
| 17.11 | Smoke test: concurrent POST returns 409 | gate |
| 17.12 | Smoke test: failure (claude removed from PATH temporarily) → job → failed | gate |
| 17.13 | Commit phase 17 | git commit |
