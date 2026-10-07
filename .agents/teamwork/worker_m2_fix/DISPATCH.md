## 2026-10-06T22:43:20Z
You are teamwork_preview_worker (worker_m2_fix).
Your dedicated working directory is: /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m2_fix

MANDATORY FIRST STEP: Read the user request verbatim in:
/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md (under header `## 2026-10-06T20:47:50Z`).

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

CRITICAL WORKTREE LOCATION:
All changes and tests MUST be executed inside:
/Users/joshua/Developer/auto-speech-worktree

Reference Material to Read First:
1. /Users/joshua/Developer/auto-speech/PROJECT.md
2. /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_retry_1/handoff.md (Fix for autoplay_turn session_id mismatch)
3. /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_retry_2/handoff.md (Fix for session_configure subsystem & action routing)

Task: Apply the fixes to `plugin/scripts/python/mcp_server.py` and expand `tests/test_mcp_server.py`:
1. In `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/mcp_server.py`:
   - `_call_autoplay_turn`: When `session_id` is passed in arguments (or resolved non-empty), after `t_path = locator.locate(cwd=..., session_id=session_id)`, verify that `t_path.name == f"{session_id}.jsonl"` or `t_path.stem == session_id`. If not matching (i.e. locator fell back to newest-jsonl), return tool error immediately:
     `return _ok(req_id, _tool_result(f"session transcript not found for session_id: {session_id}", is_error=True))`
   - `_call_session_configure`:
     * Validate `subsystem`: `if subsystem is not None and subsystem not in ("autoplay", "narrate"): return _ok(req_id, _tool_result(f"invalid subsystem: {subsystem!r} (expected autoplay, narrate)", is_error=True))`
     * Fix alternative dispatch routing for `action`:
       - If `subsystem == "autoplay"`:
         * if `action in ("on", "off", "status")`: `args.setdefault("autoplay", action)`
         * elif `action == "mode"`: if not value: return tool error, else `args.setdefault("mode", value)`
         * elif `action == "scope"`: if not value: return tool error, else `args.setdefault("scope", value)`
         * elif `action is not None`: return tool error (`f"invalid autoplay action: {action!r} (expected on, off, status, mode, scope)"`)
       - Elif `subsystem == "narrate"`:
         * if `action in ("on", "off", "status")`: `args.setdefault("narrate", action)`
         * elif `action is not None`: return tool error (`f"invalid narrate action: {action!r} (expected on, off, status)"`)
       - Elif `subsystem is None and action is not None`:
         * if `action == "mode"` and value: `args.setdefault("mode", value)`
         * elif `action == "scope"` and value: `args.setdefault("scope", value)`
         * else: return tool error (`f"unhandled action {action!r} without valid subsystem"`)
2. In `/Users/joshua/Developer/auto-speech-worktree/tests/test_mcp_server.py`:
   - Add test verifying `autoplay_turn` returns tool error when `session_id` does not match the located transcript.
   - Add test verifying `session_configure` rejects invalid subsystem with tool error.
   - Add test verifying `session_configure` with `subsystem="autoplay", action="mode", value="raw"` succeeds without error.
   - Add test verifying `session_configure` with `subsystem="autoplay", action="scope", value="solo", session_id="s1"` succeeds without error.
3. Verification in `/Users/joshua/Developer/auto-speech-worktree`:
   - `.venv/bin/python tests/test_mcp_server.py` (all tests pass)
   - `bash tests/run_all.sh` (all 50 test suites pass)
   - `.venv/bin/ruff check plugin/scripts/python/mcp_server.py tests/test_mcp_server.py` (0 errors)

Write your report to:
/Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m2_fix/handoff.md
Send a completion message when finished.
