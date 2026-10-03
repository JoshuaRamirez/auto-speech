# BRIEFING — 2026-10-03T18:42:00Z

## Mission
Investigate and specify the `plugin/scripts/python/speak.py` thin CLI client refactor for Milestone M2 (UNIX socket IPC, argument parsing compatibility, stdin handling, graceful error handling).

## 🔒 My Identity
- Archetype: explorer
- Roles: Teamwork explorer, thin client specialist
- Working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_2
- Original parent: c05df6b8-cecd-49ba-9fb8-8fa47f977488
- Milestone: M2 (Thin Client IPC via UNIX Sockets)

## 🔒 Key Constraints
- Read-only investigation — do NOT implement / modify source code directly
- Write only inside working directory (/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_2)
- Specify backward-compatible argument parsing (--ordinal, --source-hash, --keep-artifacts)
- Specify stdin reading and whitespace filtering
- Specify UNIX domain socket connection to /tmp/auto-speech-daemon.sock
- Specify graceful error handling for daemon down states
- Output analysis.md and handoff.md; communicate via send_message to parent

## Current Parent
- Conversation ID: c05df6b8-cecd-49ba-9fb8-8fa47f977488
- Updated: 2026-10-03T18:42:00Z

## Investigation State
- **Explored paths**:
  - `plugin/scripts/python/speak.py`
  - `plugin/scripts/python/autoplay_worker.py`
  - `plugin/scripts/shell/run_speak.sh`
  - `plugin/commands/auto-speech-speak.md`
  - `tests/e2e/harness.py`, `tests/e2e/test_tier1_features.py`, `tests/e2e/test_tier2_boundaries.py`
  - `tests/e2e/run_e2e.py` (Tier 1 & Tier 2 test runs executed and analyzed)
- **Key findings**:
  - Existing `speak.py` invokes `PipelineOrchestrator`, causing MLX/Kokoro loads (>2.5s) and timeouts in Tier 1 (`test_tier1_r2_speak_cli_transmits_stdin_to_socket`) and Tier 2.
  - Thin client architecture requires zero non-standard dependencies (`argparse`, `os`, `socket`, `sys`).
  - Empty/whitespace stdin exits 0 cleanly immediately without opening a socket.
  - Backward compatibility: `--ordinal`, `--keep-artifacts`, `--source-hash` (validates 64-hex SHA-256 or exits 2), plus optional `--socket-path`.
  - Socket transmission: connects to `AUTO_SPEECH_DAEMON_SOCK` or `/tmp/auto-speech-daemon.sock`, streams UTF-8 payload, issues `sock.shutdown(socket.SHUT_WR)`, and closes socket.
  - Graceful errors: catches `FileNotFoundError`, `ConnectionRefusedError`, `socket.timeout`, `OSError`; prints clean message to `sys.stderr`; returns 1.
- **Unexplored areas**: None within the scope of M2 `speak.py` thin client.

## Key Decisions Made
- Confirmed full backward compatibility for `--ordinal`, `--keep-artifacts`, and `--source-hash` flags to protect caller contracts.
- Confirmed immediate exit 0 on empty/whitespace stdin.
- Validated all 6 core client scenarios in isolated test runner.

## Artifact Index
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_2/DISPATCH.md — Task assignment and instructions
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_2/BRIEFING.md — Working memory and identity
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_2/progress.md — Liveness heartbeat and status
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_2/analysis.md — Comprehensive technical analysis and proposed code
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_2/handoff.md — 5-component self-contained handoff report
