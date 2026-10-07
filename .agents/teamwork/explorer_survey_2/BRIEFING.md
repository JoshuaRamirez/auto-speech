# BRIEFING — 2026-10-06T21:00:00Z

## Mission
Survey MCP server implementation and lifecycle hook parity requirements in auto-speech for R2: Full Lifecycle Hook Parity via MCP Server.

## 🔒 My Identity
- Archetype: explorer
- Roles: teamwork_preview_explorer
- Working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_2
- Original parent: c05df6b8-cecd-49ba-9fb8-8fa47f977488
- Milestone: MLX Kokoro TTS Engine & TTSEngine Investigation
- Milestone addition (2026-10-06): Survey R2 Full Lifecycle Hook Parity via MCP Server

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- Write only to /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_2/
- Produce analysis.md and handoff.md

## Current Parent
- Conversation ID: 4db9af0e-498d-49a5-831a-0009fd31326e
- Updated: 2026-10-06T20:50:56Z

## Investigation State
- **Explored paths**:
  - `ORIGINAL_REQUEST.md` (read verbatim R2 requirements and acceptance criteria)
  - `plugin/scripts/python/mcp_server.py` (JSON-RPC protocol, speak tool, `spawn_say_worker` fallback)
  - `plugin/scripts/python/daemon_client.py` (Priority enum P1-P4, socket IPC, `play_cache`, `interrupt`)
  - `plugin/scripts/python/autoplay_worker.py` (extraction, hashing, rewrite flow, speak dispatch)
  - `plugin/scripts/python/transcript_locator.py` & `message_selector.py` (transcript selection for Claude Code and Antigravity)
  - `plugin/scripts/python/autoplay_config.py` & `claude_cli_rewriter.py` (prompts and rewrite options)
  - `plugin/scripts/shell/autoplay_hook.sh` & `narrator_hook.sh` & `agent-event-router` (lifecycle shell hooks)
  - `plugin/scripts/python/narrator_service.py` (event tailing, classifier, watermark, priority queue)
  - `plugin/scripts/python/autoplay_enrollment.py` & `autoplay_scope.py` (enrollment and solo scope markers)
  - `plugin/scripts/python/doctor.py` & `health_report.py` (diagnostic probes)
  - `plugin/scripts/python/native_audio_sink.py` & `control.py` & `mpv_ipc.py` (playback transport controls)
  - `tests/test_mcp_server.py` & `tests/run_all.sh` (unit test verification)
- **Key findings**:
  - `mcp_server.py` is pure standard library and can cleanly host all 6 tools without adding heavy dependencies.
  - Subsystems (`TranscriptLocator`, `MessageSelector`, `ClaudeCliRewriter`, `DaemonClient`, `SessionEnrollment`, `SoloScope`, `Doctor`, `CacheStore`, `MpvIpc`) are fully modular and ready for in-process integration.
  - Global mute (`~/.claude/auto-speech.disabled`) is hermetically checkable via `AutoplayGate().worker_gated_off()`.
  - All speech-producing tools (`speak`, `autoplay_turn`, `narrate_event`, `playback_control` on `replay`/`resume`) enforce the global mute.
- **Unexplored areas**: None. Investigation complete.

## Key Decisions Made
- Fully specified input schemas, execution pipelines, error states, and priority alignments for the 6 MCP tools: `speak`, `autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`, `doctor`.
- Documented findings in `analysis.md` and synthesized 5-component report in `handoff.md`.

## Artifact Index
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_2/BRIEFING.md — Persistent state and working memory
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_2/progress.md — Liveness heartbeat and task tracker
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_2/analysis.md — Comprehensive technical analysis
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_2/handoff.md — 5-component handoff report
