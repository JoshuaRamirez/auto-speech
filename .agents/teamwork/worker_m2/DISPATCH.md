## 2026-10-06T22:12:36Z

You are teamwork_preview_worker (worker_m2).
Your dedicated working directory is: /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m2

MANDATORY FIRST STEP: Read the user request verbatim in:
/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md (under header `## 2026-10-06T20:47:50Z`).

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

CRITICAL WORKTREE LOCATION:
All code changes and test runs MUST be executed inside the isolated git worktree:
/Users/joshua/Developer/auto-speech-worktree

Reference Material to Read First:
1. /Users/joshua/Developer/auto-speech/PROJECT.md
2. /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_1/analysis.md (Architecture of autoplay_turn & narrate_event)
3. /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_2/analysis.md (Architecture of playback_control, session_configure, doctor)
4. /Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m2/analysis.md (Exhaustive test specifications for test_mcp_server.py)

Task Scope: Implement Milestone M2 (Expanded MCP Server Tool Suite).
1. In `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/mcp_server.py`:
   - Expand `tools/list` to advertise all 6 tools with complete JSON-RPC 2.0 schemas:
     * `speak`: positional text synthesis (existing, retained with 100% backward compatibility).
     * `autoplay_turn`: extract turn from transcript (Claude Code or Antigravity formats), rewrite via template/ClaudeCliRewriter or raw fallback, compute SHA-256 source hash, queue to `DaemonClient.speak(priority=Priority.AUTOPLAY)` with fallback to `spawn_say_worker`.
     * `narrate_event`: verify session enrollment marker (`~/.claude/auto-speech-narrate-sessions/<session_id>`), check daemon liveness (`DaemonClient.ensure_daemon_running()`), append atomic JSONL line to `/tmp/auto-speech-narrator-events.jsonl`.
     * `playback_control`: transport operations (`pause`, `resume`, `stop`, `restart`, `seek`, `replay`) via `DaemonClient.interrupt()`, `MpvIpc` over `/tmp/auto-speech-mpv.sock`, and `CacheStore`/`DaemonClient.play_cache()`.
     * `session_configure`: manage `SessionEnrollment` markers, narration markers, `SoloScope` marker, and `autoplay.toml` config.
     * `doctor`: execute `Doctor().run()`, returning text or structured JSON, setting `isError=True` if unhealthy.
   - Enforce global mute check (`AutoplayGate().worker_gated_off()`) on all speech-producing operations (`speak`, `autoplay_turn`, `narrate_event`, and `playback_control` replay/resume).
   - Ensure standard-library-only implementation in `mcp_server.py` with injectable collaborators in `McpServer.__init__` for hermetic testing.
2. In `/Users/joshua/Developer/auto-speech-worktree/tests/test_mcp_server.py`:
   - Update `test_tools_list_has_only_speak` -> `test_tools_list_advertises_all_six_tools` and verify all 6 tools and required fields.
   - Preserve all existing `speak` test cases (100% backward compatibility).
   - Implement comprehensive hermetic test suites for all 5 new tools (`autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`, `doctor`) covering happy paths, error branches, missing files, global mute gating, and rewriter fallbacks using temporary directories and mock collaborators as specified by `spec_miner_m2`.
3. Verification inside `/Users/joshua/Developer/auto-speech-worktree`:
   - Run: `.venv/bin/python tests/test_mcp_server.py` (all tests pass)
   - Run: `bash tests/run_all.sh` (all 50+ test suites must pass 100% green!)
   - Run: `.venv/bin/ruff check plugin/scripts/python/mcp_server.py tests/test_mcp_server.py` (0 errors)

Output:
Write full handoff report documenting all changes, files created, and test execution results to:
/Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m2/handoff.md
Send a completion message back to caller when finished.
