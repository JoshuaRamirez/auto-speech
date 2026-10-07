# Review Handoff Report: Milestone M2 Tool Schemas & Error Handling

**Reviewer**: `teamwork_preview_reviewer` (`reviewer_m2_retry_2`)  
**Target Milestone**: M2 (Expanded MCP Server Tool Suite)  
**Worktree Directory**: `/Users/joshua/Developer/auto-speech-worktree`  
**Verdict**: **APPROVE**  

---

## 1. Observation

### 1.1 Empirical Command Executions in `/Users/joshua/Developer/auto-speech-worktree`

1. **Unit Test Suite Execution**:
   - Command: `.venv/bin/python tests/test_mcp_server.py`
   - Result: Exited 0.
   - Verbatim output:
     ```
     ok  test_initialize_echoes_supported_protocol
     ok  test_initialize_unknown_protocol_falls_back_to_latest
     ok  test_notification_gets_no_response
     ok  test_ping
     ok  test_tools_list_advertises_all_six_tools
     ok  test_speak_queues_text_verbatim
     ok  test_speak_rejects_bad_text
     ok  test_speak_rejects_oversize_text
     ok  test_speak_honors_global_mute
     ok  test_speak_spawn_failure_is_tool_error
     ok  test_protocol_errors
     ok  test_response_messages_are_ignored
     ok  test_serve_writes_one_line_per_request
     ok  test_plugin_mcp_manifest_points_at_the_launcher
     ok  test_speak_invokes_daemon_client_directly
     ok  test_speak_falls_back_to_spawn_say_worker_on_connection_error
     ok  test_speak_falls_back_to_spawn_say_worker_on_daemon_error_status
     ok  test_autoplay_turn_happy_path
     ok  test_autoplay_turn_honors_global_mute
     ok  test_autoplay_turn_missing_transcript_returns_tool_error
     ok  test_autoplay_turn_no_qualifying_turn_returns_tool_error
     ok  test_autoplay_turn_text_below_min_len_returns_tool_error
     ok  test_autoplay_turn_mode_raw_bypasses_rewriter
     ok  test_autoplay_turn_bypass_llm_flag_bypasses_rewriter
     ok  test_autoplay_turn_rewriter_fallback_on_failure
     ok  test_autoplay_turn_connection_error_falls_back_cleanly
     ok  test_autoplay_turn_nonexistent_session_id_returns_tool_error
     ok  test_autoplay_turn_matching_session_id_succeeds
     ok  test_autoplay_turn_invalid_session_id_validation
     ok  test_autoplay_turn_nonexistent_session_id_with_real_locator_returns_tool_error
     ok  test_narrate_event_missing_event_returns_parameter_error
     ok  test_narrate_event_honors_global_mute
     ok  test_narrate_event_unenrolled_session_returns_clean_notice
     ok  test_narrate_event_enrolled_session_appends_jsonl_and_ensures_daemon
     ok  test_narrate_event_antigravity_conversation_id_resolution
     ok  test_narrate_event_default_cwd_fallback
     ok  test_playback_control_invalid_action_returns_error
     ok  test_playback_control_stop_invokes_interrupt
     ok  test_playback_control_pause_resume_restart_seek_when_socket_missing
     ok  test_playback_control_pause_resume_restart_when_socket_alive
     ok  test_playback_control_resume_honors_global_mute
     ok  test_playback_control_seek_targets
     ok  test_playback_control_seek_invalid_target_returns_error
     ok  test_playback_control_replay_cached_audio
     ok  test_playback_control_replay_empty_cache_and_bounds
     ok  test_playback_control_replay_honors_global_mute
     ok  test_session_configure_autoplay_on_and_off
     ok  test_session_configure_autoplay_off_without_session_sets_global_mute
     ok  test_session_configure_narrate_on_and_off
     ok  test_session_configure_scope_solo_and_all
     ok  test_session_configure_scope_solo_without_session_returns_error
     ok  test_session_configure_mode_and_size
     ok  test_session_configure_status_reports
     ok  test_session_configure_subsystem_and_action_validation
     ok  test_session_configure_alternative_dispatch_routing
     ok  test_doctor_text_report_healthy
     ok  test_doctor_json_report_healthy
     ok  test_doctor_unhealthy_sets_is_error_true
     mcp_server: 58 tests passed
     ```

2. **Tool Advertisement Contract (`tools/list`)**:
   - Command: `echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | .venv/bin/python plugin/scripts/python/mcp_server.py`
   - Result: Exited 0.
   - Verbatim output contains all 6 advertised tools:
     `speak`, `autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`, `doctor`.
   - Each tool schema has `additionalProperties: false`, accurate `description`, and appropriate `required` attributes:
     - `speak`: `required: ["text"]`
     - `autoplay_turn`: optional parameters (`session_id`, `transcript_path`, `mode`, `size`, `ordinal`, `bypass_llm`)
     - `narrate_event`: `required: ["event"]`
     - `playback_control`: `required: ["action"]`
     - `session_configure`: optional parameters (`session_id`, `autoplay`, `narrate`, `mode`, `size`, `scope`, `subsystem`, `action`, `value`)
     - `doctor`: optional flags (`as_json`, `json_format`)

3. **Full Regression Suite**:
   - Command: `bash tests/run_all.sh`
   - Result: Exited 0 across all 50 test suites:
     `ran: 50, failed: 0, all tests passed`

4. **Code Quality / Lint Verification**:
   - Command: `.venv/bin/ruff check plugin/scripts/python/mcp_server.py tests/test_mcp_server.py`
   - Result: Exited 0 with `All checks passed!`.

### 1.2 Code Inspection Observations

1. **JSON-RPC 2.0 & MCP Tool Result Protocol Compliance**:
   - In `plugin/scripts/python/mcp_server.py`:
     - Line 324-325:
       ```python
       def _tool_result(message: str, *, is_error: bool = False) -> dict:
           return {"content": [{"type": "text", "text": message}], "isError": is_error}
       ```
     - Line 1263-1264:
       ```python
       def _ok(req_id, result: dict) -> dict:
           return {"jsonrpc": "2.0", "id": req_id, "result": result}
       ```
     - Tool validation and runtime errors return `_ok(req_id, _tool_result(..., is_error=True))`. This strictly aligns with the Model Context Protocol (MCP) tool execution specification, where tool failures are wrapped in standard success responses with `result.isError = true`.
     - Protocol-level failures (invalid JSON, unknown method, non-dict params) correctly return JSON-RPC 2.0 top-level `error` envelopes (Parse error `-32700`, Invalid Request `-32600`, Method not found `-32601`, Invalid Params `-32602`).

2. **Session Isolation in `_call_autoplay_turn`**:
   - Lines 581-588:
     ```python
     if session_id and (t_path.name != f"{session_id}.jsonl" and t_path.stem != session_id):
         return _ok(
             req_id,
             _tool_result(
                 f"session transcript not found for session_id: {session_id}",
                 is_error=True,
             ),
         )
     ```
     When a caller requests a specific `session_id` and the transcript locator falls back to the newest session in the workspace, `mcp_server.py` catches the mismatch, halts turn extraction, and returns `isError: True`. Cross-session speech leakage is prevented.

3. **Subsystem and Action Routing in `_call_session_configure`**:
   - Lines 1002-1009 validate `subsystem` against `("autoplay", "narrate")`, preventing silent fallthrough on invalid subsystems.
   - Lines 1011-1078 segregate action handling:
     - `autoplay`: routes `on`, `off`, `status`; routes `mode` (mapping `small`/`medium`/`large` to size) and `scope` requiring non-empty `value`; rejects invalid actions.
     - `narrate`: routes `on`, `off`, `status`; rejects invalid actions (such as `mode`).
     - Standalone action dispatch without subsystem routes `mode` and `scope` with required `value`; rejects unknown actions with `unhandled action ... without valid subsystem`.
     - No collisions occur between `subsystem` and action names in `args`.

4. **Integrity Audit**:
   - No hardcoded test responses or expected values detected in production code.
   - No mock/dummy facades substituting real functionality; all 6 tools interact with their concrete underlying modules (`TranscriptLocator`, `MessageSelector`, `ClaudeCliRewriter`, `SessionEnrollment`, `SoloScope`, `DaemonClient`, `Doctor`, `MpvIpc`).
   - No bypass shortcuts detected.

---

## 2. Logic Chain

1. **Schema Integrity**:
   - Observation 1.1 #2 confirms that running `tools/list` on `mcp_server.py` produces a valid JSON-RPC 2.0 response advertising all 6 tools defined in `PROJECT.md` §Interface Contracts and `ORIGINAL_REQUEST.md` §R2.
   - All tool schemas strictly disallow unmodeled properties (`additionalProperties: false`) and require only their necessary inputs.

2. **Contract Consistency**:
   - Per Observation 1.2 #1, MCP tools report operational failures (e.g., non-existent session, invalid subsystem, unconfigured parameter) as `{"result": {"isError": true, "content": [{"type": "text", "text": "..."}]}}`.
   - The newly introduced error checks in `autoplay_turn` (Observation 1.2 #2) and `session_configure` (Observation 1.2 #3) strictly adhere to this format.

3. **Backward Compatibility**:
   - All 18 baseline tests for `speak` pass cleanly (Observation 1.1 #1).
   - `speak` continues to support verbatim synthesis, character limit enforcement, global mute honoring, and fallback to `spawn_say_worker` on daemon failure.
   - The full regression suite across all 50 test files in `tests/run_all.sh` passes with 0 failures (Observation 1.1 #3).

4. **Adversarial Robustness**:
   - Adversarial stress tests (malformed JSON, unknown tools, negative ordinals, bad mode values, unsupported actions under `narrate`, missing required values) were independently executed and confirmed to fail defensively without crashing or emitting unhandled exceptions.

---

## 3. Caveats

- Milestone M3 (Hook Reconcile & Architectural Coherence) and Milestone M4 (Comprehensive Test Suite & Regression Verification) remain planned for downstream phases.
- Legacy test files in `tests/` contain pre-existing `sys.path.insert` import ordering warnings under global `ruff check .`, but all M2 production code (`mcp_server.py`) and M2 test code (`tests/test_mcp_server.py`) have 0 ruff lint errors.

---

## 4. Conclusion

Milestone M2 implementation in `/Users/joshua/Developer/auto-speech-worktree` cleanly fulfills all requirements:
1. `tools/list` advertises all 6 tools with rigorous JSON schemas.
2. `speak` backward compatibility and existing tests are 100% preserved.
3. Defect fixes for `autoplay_turn` (session transcript isolation) and `session_configure` (subsystem validation and action routing) are fully verified and strictly adhere to JSON-RPC 2.0 and MCP tool result contracts.
4. All 58 unit tests in `tests/test_mcp_server.py` pass; all 50 test suites in `tests/run_all.sh` pass.

**Verdict**: **APPROVE**

---

## 5. Verification Method

To independently reproduce verification in `/Users/joshua/Developer/auto-speech-worktree`:

1. **Verify MCP Tool Advertisement**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | .venv/bin/python plugin/scripts/python/mcp_server.py
   ```
   *Expected*: JSON-RPC 2.0 response with 6 tools: `speak`, `autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`, `doctor`.

2. **Verify MCP Unit Test Suite**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/python tests/test_mcp_server.py
   ```
   *Expected*: `mcp_server: 58 tests passed` (exit 0).

3. **Verify Full Regression Suite**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   bash tests/run_all.sh
   ```
   *Expected*: `ran: 50, failed: 0, all tests passed` (exit 0).

4. **Verify Lint Compliance**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/ruff check plugin/scripts/python/mcp_server.py tests/test_mcp_server.py
   ```
   *Expected*: `All checks passed!` (exit 0).
