# Handoff Report: Milestone M2 MCP Tools (`playback_control`, `session_configure`, `doctor`)

**Agent**: `explorer_m2_2`  
**Role**: Teamwork Explorer (Investigator & Synthesizer)  
**Date**: 2026-10-06  
**Target Milestone**: M2 (Expanded MCP Server Tool Suite)  
**Target Files**: `plugin/scripts/python/mcp_server.py`, `tests/test_mcp_server.py`  
**Working Directory**: `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_2`  
**Worktree Directory**: `/Users/joshua/Developer/auto-speech-worktree`  

---

## 1. Observation

1. **Current `mcp_server.py` Scope**:
   In `plugin/scripts/python/mcp_server.py` (lines 41–63, 102–112, 147–150):
   - Only `SPEAK_TOOL` is currently advertised in `tools/list` (line 147): `return _ok(req_id, {"tools": [SPEAK_TOOL]})`.
   - `_tools_call` only recognizes `"speak"` (line 170): `if name != "speak": return _error(req_id, INVALID_PARAMS, f"unknown tool: {name}")`.
   - `McpServer.__init__` accepts only `spawner=None, gate: AutoplayGate | None = None, client: DaemonClient | None = None`.
2. **Existing Implementation of Transport & Replay in `cli.py`**:
   In `plugin/scripts/python/cli.py` (lines 54–88, 200–406):
   - Mpv IPC socket path is `Path("/tmp/auto-speech-mpv.sock")` (line 38).
   - `_send_mpv_command(cmd)` checks `_is_mpv_socket_alive()` and dispatches via `MpvIpc.send(cmd, MPV_SOCK_PATH)` (lines 75–87).
   - `handle_pause` sends `["set_property", "pause", True]`.
   - `handle_resume` sends `["set_property", "pause", False]`.
   - `handle_restart` sends `["seek", 0, "absolute"]`.
   - `handle_seek` parses `target` (`"end"` -> 999999 absolute, `"+N"` -> relative, `"-N"` -> relative, `"N"` -> absolute) (lines 310–360).
   - `handle_stop` invokes `MpvIpc.send(["stop"])`, `DaemonClient.interrupt()`, kills mpv PID if present in `/tmp/auto-speech-mpv.pid`, and unlinks the PID file (lines 363–406).
   - `handle_replay` checks `AutoplayGate().worker_gated_off()`, queries `CacheStore.list_by_recency()`, and invokes `DaemonClient.play_cache(entry.source_hash)` (lines 200–281).
3. **Existing Session Markers & Configuration**:
   - `SessionEnrollment` (`plugin/scripts/python/autoplay_enrollment.py` lines 40–94) manages `~/.claude/auto-speech-autoplay-enabled/<session_id>`.
   - `SoloScope` (`plugin/scripts/python/autoplay_scope.py` lines 29–86) manages `~/.claude/auto-speech-autoplay-solo`.
   - Narration marker directory is `~/.claude/auto-speech-narrate-sessions/<session_id>` (`cli.py` line 527, `narrator_service.py` line 732).
   - Autoplay TOML config is written to `~/.config/auto-speech/autoplay.toml` (`cli.py` lines 477–481).
4. **Existing Diagnostics Infrastructure**:
   - `Doctor` (`plugin/scripts/python/doctor.py` lines 60–113) runs 10 system probes and returns a `HealthReport`.
   - `HealthReport` (`plugin/scripts/python/health_report.py` lines 29–62) exposes `.healthy` (boolean), `.to_text()`, and `.to_json()`.
5. **Baseline Test Harness State**:
   Executing `bash tests/run_all.sh` in `/Users/joshua/Developer/auto-speech-worktree` ran 50 tests with 0 failures:
   ```
   ====================
   ran:    50
   failed: 0
   all tests passed
   ```

---

## 2. Logic Chain

1. **R2 Requirements Mapping**:
   `ORIGINAL_REQUEST.md` §R2 explicitly requires the MCP server to provide programmatic parity with shell lifecycle hooks, specifically advertising `playback_control`, `session_configure`, and `doctor`.
2. **Standardized Tool Contracts**:
   - `playback_control` requires a unified JSON-RPC handler mapping action strings (`pause`, `resume`, `stop`, `restart`, `seek`, `replay`) to either the synchronous daemon interrupt/cache-playback or the live mpv UNIX IPC socket.
   - For `resume` and `replay`, global mute (`AutoplayGate.worker_gated_off()`) must suppress audio and return an informative error. For `pause`, `stop`, `restart`, and `seek`, operations proceed even when muted so the user can always halt or reposition playback.
   - For `session_configure`, incoming parameters may configure autoplay enrollment (`SessionEnrollment`), narration sessions (`auto-speech-narrate-sessions`), solo scoping (`SoloScope`), and rewrite configurations (`autoplay.toml`). If no mutating parameters are supplied, it returns a consolidated multi-line status report.
   - For `doctor`, it invokes `Doctor().run()`. If `not report.healthy`, the response envelope must flag `isError: True` while delivering the full diagnostic report (text or JSON) in `content[0].text`.
3. **Testability & Hermetic Isolation**:
   Because `test_mcp_server.py` runs without background daemons, running processes, or live audio output, all external touchpoints must be injectable in `McpServer.__init__`:
   - `home`: isolates `~/.claude/` and `~/.config/` into a temporary directory.
   - `client`: allows mock `DaemonClient` asserting `.interrupt()` and `.play_cache()`.
   - `mpv_ipc_sender`: intercepts mpv commands without requiring socket binding.
   - `mpv_sock_path`: redirects socket alive checks to test locations.
   - `cache_store`: provides synthetic cache entries for replay.
   - `doctor`: provides deterministic health reports for testing.

---

## 3. Caveats

1. **Multi-Agent Coordination on `mcp_server.py`**:
   Milestone M2 also includes `autoplay_turn` and `narrate_event` (assigned to sibling explorer/worker `explorer_m2_1`). When implementing in `mcp_server.py`, all tool schemas and dispatches should merge seamlessly into `ALL_TOOLS` and `_tools_call`.
2. **Mpv Process Ownership**:
   The unified daemon server owns audio playback via `NativeAudioSink`. However, `mpv` might occasionally run detached during offline fallbacks. The `stop` handler defends against both cases by invoking `client.interrupt()` and terminating `/tmp/auto-speech-mpv.pid` if present.
3. **No Third-Party Package Dependencies**:
   `mcp_server.py` must strictly adhere to Python standard library dependencies (`json`, `math`, `os`, `subprocess`, `sys`, `pathlib`) and local project sibling modules. No external packages may be imported.

---

## 4. Conclusion

The implementation strategy for `playback_control`, `session_configure`, `doctor`, and the injectable collaborator pattern is fully specified and validated against the existing codebase:
- Complete schemas, argument validators, error envelopes, and dispatch flows have been defined in `analysis.md`.
- All logic directly reuses verified components (`MpvIpc`, `DaemonClient`, `CacheStore`, `SessionEnrollment`, `SoloScope`, `Doctor`).
- The worker agent can implement these changes in `plugin/scripts/python/mcp_server.py` and add dedicated test coverage in `tests/test_mcp_server.py` inside the isolated worktree without regression risk.

---

## 5. Verification Method

To independently verify the implementation:
1. **Worktree Directory**: Ensure execution is in `/Users/joshua/Developer/auto-speech-worktree`.
2. **Execute Unit Tests**:
   ```bash
   .venv/bin/python tests/test_mcp_server.py
   ```
   Asserts that all tests for `tools/list`, `speak`, `playback_control`, `session_configure`, and `doctor` pass cleanly.
3. **Execute Full Test Suite**:
   ```bash
   bash tests/run_all.sh
   ```
   Must pass all tests (50+ tests green, 0 failures).
4. **Code Quality & Lint**:
   ```bash
   .venv/bin/ruff check plugin/scripts/python/mcp_server.py tests/test_mcp_server.py
   ```
   Must report 0 errors.
5. **Invalidation Conditions**:
   - Any test failure in `bash tests/run_all.sh`.
   - `tools/list` failing to advertise `playback_control`, `session_configure`, or `doctor`.
   - `playback_control` replay or resume playing when `~/.claude/auto-speech.disabled` is present.
   - `doctor` failing to set `isError: True` on an unhealthy report.
