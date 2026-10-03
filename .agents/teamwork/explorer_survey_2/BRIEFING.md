# BRIEFING — 2026-10-03T17:54:00Z

## Mission
Investigate MLX Kokoro TTS engine and TTSEngine implementation for in-process integration into narrator_service.py.

## 🔒 My Identity
- Archetype: explorer
- Roles: teamwork_preview_explorer
- Working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_2
- Original parent: c05df6b8-cecd-49ba-9fb8-8fa47f977488
- Milestone: MLX Kokoro TTS Engine & TTSEngine Investigation

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- Write only to /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_2/
- Produce analysis.md and handoff.md

## Current Parent
- Conversation ID: c05df6b8-cecd-49ba-9fb8-8fa47f977488
- Updated: 2026-10-03T17:47:36Z

## Investigation State
- **Explored paths**:
  - `plugin/scripts/python/tts_engine.py` (TTSEngine definition, synthesis generator, WAV writing)
  - `plugin/scripts/python/resilient_synthesizer.py` (fault tolerance wrapper over TTSEngine)
  - `plugin/scripts/python/narrator_service.py` (daemon architecture, current `_speak` subprocess and sleep hacks)
  - `plugin/scripts/python/web_server.py` (in-process TTSEngine usage and MLX per-thread constraint documentation)
  - `plugin/scripts/python/mpv_controller.py` & `session_dir.py` (sprawl targeted for deletion in R3)
  - `plugin/scripts/shell/run_speak.sh` & `speak.py` (subprocess wrappers targeted for thin client refactor)
  - `pyproject.toml` & `.venv` (dependencies: mlx-audio, misaki, numpy, flask)
- **Key findings**:
  - `TTSEngine` at `plugin/scripts/python/tts_engine.py:48-121` can be directly instantiated in-process.
  - MLX compute streams are per-thread; binding `TTSEngine` to `narrator_service.py`'s dedicated `_tts_worker` thread satisfies thread-affinity natively.
  - `NativeAudioSink` using synchronous `subprocess.run(["mpv", "--no-video", "--really-quiet", ...])` completely eliminates detached mpv, duration guessing, `time.sleep`, and `SIGKILL`.
- **Unexplored areas**: None. Investigation complete.

## Key Decisions Made
- Confirmed direct in-process instantiation of `TTSEngine` wrapped in `ResilientSynthesizer` on the `_tts_worker` thread is optimal.
- Produced comprehensive `analysis.md` and 5-component `handoff.md`.

## Artifact Index
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_2/BRIEFING.md — Persistent state and working memory
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_2/progress.md — Liveness heartbeat and task tracker
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_2/analysis.md — Comprehensive technical analysis
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_2/handoff.md — 5-component handoff report
