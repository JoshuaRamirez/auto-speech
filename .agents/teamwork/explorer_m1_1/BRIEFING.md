# BRIEFING — 2026-10-06T21:16:00Z

## Mission
Formulate concrete implementation strategy for the core Python CLI dispatcher (`plugin/scripts/python/cli.py`) for Milestone M1.

## 🔒 My Identity
- Archetype: explorer
- Roles: explorer, synthesis
- Working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_1
- Original parent: 4db9af0e-498d-49a5-831a-0009fd31326e
- Milestone: M1

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- All code changes for this milestone will be made inside the isolated git worktree at `/Users/joshua/Developer/auto-speech-worktree`
- Formulate concrete, detailed implementation strategy for the core Python CLI dispatcher (`plugin/scripts/python/cli.py`) for Milestone M1
- Write analysis to `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_1/analysis.md`
- Write handoff to `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_1/handoff.md`
- Send completion message to parent (4db9af0e-498d-49a5-831a-0009fd31326e)

## Current Parent
- Conversation ID: 4db9af0e-498d-49a5-831a-0009fd31326e
- Updated: not yet

## Investigation State
- **Explored paths**:
  - `ORIGINAL_REQUEST.md` (verbatim requirements §R1-R5)
  - `PROJECT.md` (CLI dispatcher architecture, exit codes, milestones)
  - `explorer_survey_1/analysis.md`, `explorer_survey_2/analysis.md`, `explorer_survey_3/analysis.md`
  - Existing Python scripts: `speak.py`, `replay.py`, `control.py`, `daemon_client.py`, `native_audio_sink.py`, `mpv_ipc.py`, `doctor.py`, `self_update.py`, `autoplay_*.py`, `narrator_*.py`
  - Existing Shell scripts: `autoplay_hook.sh`, `narrator_hook.sh`, `narrator_service_*.sh`, `bootstrap.sh`, `autoplay_status.sh`
  - Existing Tests: `tests/run_all.sh` (verified baseline 49/49 green), `tests/test_replay_control.py`, `tests/test_cli_rewrite.py`, etc.
- **Key findings**:
  - Complete mapping of 12 subcommands: `speak`, `replay`, `pause`, `resume`, `stop`, `restart`, `seek`, `autoplay`, `narrate`, `hook`, `doctor`, `update`.
  - Nested sub-subcommand structures for `autoplay`, `narrate`, `hook`.
  - Exact exit codes specified: 0 (OK/muted), 1 (failure), 2 (no session/invalid input), 3 (IPC fail), 4 (bad arg), 6 (playback fail), 130 (interrupted).
  - Defined standard error handling with descriptive messages on `sys.stderr`.
  - Comprehensive test plan mapped for `tests/test_cli_dispatcher.py`.
- **Unexplored areas**: None for M1 CLI strategy.

## Key Decisions Made
- `cli.py` will use standard library `argparse` with nested subparsers for zero-dependency hermetic execution.
- `bin/auto-speech` bash trampoline resolves symlinks and locates `.venv/bin/python` to avoid system Python 3.9 issues.
- Detailed implementation strategy written to `analysis.md` and handoff report to `handoff.md`.

## Artifact Index
- `DISPATCH.md` — record of incoming dispatch instructions
- `BRIEFING.md` — persistent situational awareness
- `progress.md` — liveness heartbeat
- `analysis.md` — comprehensive M1 CLI dispatcher implementation strategy
- `handoff.md` — 5-component handoff report for M1
