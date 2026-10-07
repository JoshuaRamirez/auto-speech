## 2026-10-06T23:06:03Z
You are teamwork_preview_spec_miner (spec_miner_m3).
Your dedicated working directory is: /Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m3

MANDATORY FIRST STEP: Read the user request verbatim in:
/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md (under header `## 2026-10-06T20:47:50Z`).
Also read:
/Users/joshua/Developer/auto-speech/PROJECT.md.

Note: Target worktree is `/Users/joshua/Developer/auto-speech-worktree`.

Objective:
Mine the exhaustive test specification and verification harness requirements for Milestone M3 (Hook Reconcile & Architectural Coherence).

Investigate and specify:
1. Test specifications for shell hooks (`autoplay_hook.sh`, `narrator_hook.sh`):
   - Fast-path global mute check (<50ms exit 0).
   - Claude Code session payload: `{"session_id": "test-sess-1"}`.
   - Antigravity conversation payload: `{"conversationId": "test-conv-1"}`.
   - Dual payload: `{"session_id": "s1", "conversationId": "c1"}` (precedence check).
   - Non-dict / malformed JSON payload handling (exit 0, no stderr noise).
2. Test specifications for slash commands and installer:
   - Ensure `bash tests/test_install_plugin.sh` passes 12/12 assertions.
   - Verify commands delegate to `auto-speech` without hardcoded absolute paths.
3. Identify existing test files:
   - `tests/test_autoplay_hook.py` (or shell tests)
   - `tests/test_narrator_hook.py`
   - `tests/test_install_plugin.sh`
   - Determine what new tests or assertions should be added to guarantee zero regressions.

Do NOT implement tests directly.
Write your specification report to `/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m3/analysis.md`
and handoff report to `/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m3/handoff.md`.
Send a completion message back to caller when done.
