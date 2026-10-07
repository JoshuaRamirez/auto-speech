## 2026-10-06T22:05:43Z

You are teamwork_preview_spec_miner (spec_miner_m2).
Your dedicated working directory is: /Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m2

MANDATORY FIRST STEP: Read the user request verbatim in:
/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md (under header `## 2026-10-06T20:47:50Z`).
Also read:
/Users/joshua/Developer/auto-speech/PROJECT.md
and reference:
/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_2/analysis.md
and examine the existing test suite:
/Users/joshua/Developer/auto-speech-worktree/tests/test_mcp_server.py.

Objective:
Mine the exhaustive test specification and assertion suite for the expanded 6-tool MCP server (`plugin/scripts/python/mcp_server.py`) to be implemented in `tests/test_mcp_server.py`.

Investigate and specify:
1. Updating `test_tools_list`: verify `tools/list` advertises all 6 tools (`speak`, `autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`, `doctor`) with exact schemas and required fields.
2. Backward compatibility of `speak` tool: verify all existing `speak` tests remain completely passing.
3. Test cases for `autoplay_turn`:
   - Happy path with mock message selector and daemon client.
   - Global mute honors `~/.claude/auto-speech.disabled` returning tool error.
   - Missing transcript returns tool error.
   - Turn text below min length returns tool error.
   - `mode="raw"` and `bypass_llm=True` bypass rewriter.
   - Connection error falls back cleanly.
4. Test cases for `narrate_event`:
   - Global mute check.
   - Unenrolled session returns clean notice without error.
   - Enrolled session appends event to jsonl file and invokes `ensure_daemon_running`.
   - Missing `event` returns parameter error.
5. Test cases for `playback_control`:
   - `action="stop"` invokes `DaemonClient.interrupt()`.
   - `action="pause"`, `resume`, `restart`, `seek` when mpv socket missing vs alive.
   - `action="replay"` with ordinal 1, empty cache error, global mute check.
   - Invalid action or invalid seek target.
6. Test cases for `session_configure`:
   - `autoplay="on"` / `"off"` / `"status"`.
   - `narrate="on"` / `"off"` / `"status"`.
   - `scope="solo"` / `"all"` / `"status"`.
7. Test cases for `doctor`:
   - `as_json=False` returns text report.
   - `as_json=True` returns valid JSON report.
   - Unhealthy report sets `isError=True`.
8. Hermetic design: ensure all tests use temporary directories, mocks/stubs, and avoid touching real user audio, real daemons, or user home directory.

Do NOT implement tests directly.
Write your specification report to:
/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m2/analysis.md
and handoff report to:
/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m2/handoff.md
Send a completion message back to caller when done.
