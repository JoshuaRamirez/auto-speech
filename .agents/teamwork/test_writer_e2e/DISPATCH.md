# Task Assignment: E2E Test Writer

You are test_writer_e2e (teamwork_preview_test_writer).
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/test_writer_e2e
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md

## Objective
Read ORIGINAL_REQUEST.md and PROJECT.md first.
You are responsible for the E2E Testing Track. Build a comprehensive, opaque-box E2E test suite derived strictly from user requirements and acceptance criteria.

### Requirements to cover:
- R1: In-Process TTSEngine and Blocking AudioSink (boots successfully, loads model in memory, plays sequentially via mpv without time.sleep, no detached mpv processes remaining).
- R2: Thin Client IPC via UNIX Sockets (echo "test" | python3 plugin/scripts/python/speak.py sends text to /tmp/auto-speech-daemon.sock, synthesizes and plays sequentially without overlapping).
- R3: Removal of Dead Architectural Sprawl (run_speak.sh, PipelineOrchestrator, ShortPathStrategy, MpvController, SessionDir removed; no broken imports).

### Methodology:
Follow the 4-tier test case methodology:
- Tier 1: Feature Coverage (>=5 tests per feature)
- Tier 2: Boundary & Corner Cases (>=5 tests per feature: empty input, special characters/emojis, socket disconnect, large payloads, daemon not running)
- Tier 3: Cross-Feature Combinations (pairwise interactions: socket client while events streaming, backpressure queue cap, rapid successive utterances)
- Tier 4: Real-World Scenarios (complete user session workflow: start daemon -> stream tool events -> run speak.py -> clean shutdown -> socket cleanup)

### Deliverables:
1. Create `TEST_INFRA.md` at project root documenting test architecture, runner, and feature matrix.
2. Implement executable tests under `tests/e2e/` (e.g. `tests/e2e/test_unified_daemon_e2e.py` and runner script).
3. Verify tests can be run via `.venv/bin/python` or `pytest`. (Note: tests requiring full daemon socket will pass once daemon is implemented).
4. Create `TEST_READY.md` at project root with runner command, tier breakdown, and feature checklist.
5. Write your handoff report to `handoff.md` and send a message when done.


## 2026-10-03T18:00:30Z
You are test_writer_e2e.
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/test_writer_e2e
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md

Read ORIGINAL_REQUEST.md and PROJECT.md first. Read your task assignment in /Users/joshua/Developer/auto-speech/.agents/teamwork/test_writer_e2e/DISPATCH.md.
You own the E2E Testing Track. Design and implement the opaque-box E2E test suite covering R1, R2, R3 across Tiers 1-4.
Deliverables:
1. TEST_INFRA.md at project root.
2. Executable tests in tests/e2e/.
3. TEST_READY.md at project root.
4. handoff.md in your working directory.
Send a message when done.
