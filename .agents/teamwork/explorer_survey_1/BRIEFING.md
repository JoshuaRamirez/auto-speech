# BRIEFING — 2026-10-06T21:02:00Z

## Mission
Survey existing CLI scripts, command handlers, and entry points in auto-speech to map requirements and architecture for R1: Unified auto-speech CLI Dispatcher.

## 🔒 My Identity
- Archetype: explorer
- Roles: explorer, analyst, synthesist
- Working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_1
- Original parent: 4db9af0e-498d-49a5-831a-0009fd31326e
- Milestone: R1 Survey & Architecture Specification

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- Do NOT modify tracked production code files
- Write all findings to analysis.md and handoff.md in working directory
- Communicate with parent agent via send_message

## Current Parent
- Conversation ID: 4db9af0e-498d-49a5-831a-0009fd31326e
- Updated: 2026-10-06T21:02:00Z

## Investigation State
- **Explored paths**:
  - `plugin/scripts/python/` (70 Python scripts, including `speak.py`, `replay.py`, `control.py`, `daemon_client.py`, `native_audio_sink.py`, `narrator_service.py`, `autoplay_worker.py`, `autoplay_config.py`, `autoplay_scope.py`, `autoplay_enrollment.py`, `autoplay_gate.py`, `doctor.py`, `self_update.py`, `mcp_server.py`)
  - `plugin/scripts/shell/` (16 shell scripts, including `autoplay_hook.sh`, `autoplay_status.sh`, `narrator_hook.sh`, `narrator_service_start.sh`, `narrator_service_stop.sh`, `narrator_service_status.sh`, `run_control.sh`, `run_replay.sh`, `run_mcp.sh`)
  - `plugin/commands/` (8 curated slash commands) and `plugin/commands-extra/` (13 extra slash commands)
  - `setup/` (14 scripts including `install.sh`, `install-plugin.sh`, `uninstall.sh`, `bootstrap.sh`, `install-hook.sh`, `install-narrator-hooks.sh`, `install-bootstrap-hook.sh`)
  - External hooks & PATH: `~/.local/bin/agent-event-router`, `~/.claude/settings.json`, `~/.local/bin`
  - Test suites: `tests/run_all.sh` (49 suites verified green), `tests/test_narrator_service.py`, `tests/test_replay_control.py`, `tests/test_install_plugin.sh`
- **Key findings**:
  - All transport controls (`pause`, `resume`, `stop`, `restart`, `seek`) previously lived in `control.py` / `run_control.sh` with specific exit code contracts (`0` = success, `2` = no active playback session, `4` = bad target argument).
  - Synchronous `NativeAudioSink` manages `mpv` execution. To support interactive transport while playing, `mpv` can expose IPC socket `/tmp/auto-speech-mpv.sock` allowing `MpvIpc.send()` commands.
  - Dispatcher requires a two-tier architecture: `bin/auto-speech` bash trampoline to guarantee `$PROJECT_ROOT/.venv/bin/python` execution across macOS environments, delegating to `plugin/scripts/python/cli.py`.
  - Full subcommand hierarchy designed per R1: `speak`, `replay`, `pause`, `resume`, `stop`, `restart`, `seek`, `autoplay on|off|status|mode|scope`, `narrate on|off|status|stop|config`, `hook stop|narrator|bootstrap`, `doctor`, `update`.
  - Preserves 100% portability invariant (no hardcoded `/Users/joshua` paths).
- **Unexplored areas**:
  - None within the scope of R1 CLI dispatcher survey.

## Key Decisions Made
- Confirmed design of unified dispatcher with `bin/auto-speech` trampoline and `plugin/scripts/python/cli.py`.
- Formulated exact input resolution rules for CLI args vs stdin for `speak` and `hook`.
- Preserved strict exit code contracts (0, 1, 2, 3, 4, 130).

## Artifact Index
- `.agents/teamwork/explorer_survey_1/DISPATCH.md` — Incoming dispatch messages
- `.agents/teamwork/explorer_survey_1/BRIEFING.md` — Persistent agent briefing and memory
- `.agents/teamwork/explorer_survey_1/progress.md` — Live heartbeat and execution checklist
- `.agents/teamwork/explorer_survey_1/analysis.md` — Comprehensive survey and architecture specification
- `.agents/teamwork/explorer_survey_1/handoff.md` — 5-component handoff report for team
