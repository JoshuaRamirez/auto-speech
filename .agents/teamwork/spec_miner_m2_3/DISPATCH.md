# Task Assignment: Spec Miner M2.3 (M2 Test Specification)

You are spec_miner_m2_3 (teamwork_preview_spec_miner).
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m2_3
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md

## Objective
Read ORIGINAL_REQUEST.md and PROJECT.md first.
You are investigating Milestone M2 test requirements:
1. Examine existing tests in `tests/e2e/test_tier1_features.py` (class `TestTier1R2ThinClientIPC`) and `tests/e2e/test_tier2_boundaries.py` (class `TestTier2R2Boundaries`).
2. Catalog all test cases that will verify M2:
   - CLI transmits stdin to socket
   - Socket server enqueues to queue
   - Wire protocol streaming
   - Graceful failure when daemon down
   - Socket file cleanup on shutdown
   - Backward compatible CLI args
   - Empty stdin ignored
   - Special characters & multiline text
   - Large socket payloads
   - Abrupt client disconnects
   - Stale socket cleanup on startup
3. Design standalone unit tests for `speak.py` in `tests/test_speak_client.py` using standard unittest and mock socket/server.

Write your findings to `/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m2_3/analysis.md` and `handoff.md`. Send a message when done.

## 2026-10-03T18:36:44Z
You are spec_miner_m2_3.
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m2_3
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md

Read ORIGINAL_REQUEST.md and PROJECT.md first. Read your task assignment in /Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m2_3/DISPATCH.md.
Analyze M2 test requirements across tests/e2e/test_tier1_features.py (TestTier1R2ThinClientIPC) and test_tier2_boundaries.py (TestTier2R2Boundaries), and design standalone unit tests in tests/test_speak_client.py.
Write findings to analysis.md and handoff.md. Send a message when done.
