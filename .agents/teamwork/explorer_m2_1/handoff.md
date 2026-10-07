# Handoff Report: Milestone M2 MCP Server Parity (`autoplay_turn` & `narrate_event`)

**Author**: `explorer_m2_1` (Teamwork Explorer)  
**Date**: 2026-10-06  
**Status**: Complete (Hard Handoff)  
**Analysis Reference**: `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_1/analysis.md`

---

## 1. Observation

1. **Current MCP Server Scope**:
   In `/Users/joshua/Developer/auto-speech/plugin/scripts/python/mcp_server.py`:
   - Line 41: Defines single tool schema `SPEAK_TOOL` (`"name": "speak"`).
   - Lines 102–111: `McpServer.__init__` accepts injectable `spawner`, `gate`, and `client`.
   - Lines 168–210: `_tools_call` checks `if name != "speak": return _error(req_id, INVALID_PARAMS, f"unknown tool: {name}")`.
   - Lines 185–193: Checks global mute:
     ```python
     if self._gate.worker_gated_off():
         return _ok(
             req_id,
             _tool_result(
                 "auto-speech is globally muted (~/.claude/auto-speech.disabled); nothing was spoken",
                 is_error=True,
             ),
         )
     ```

2. **Autoplay Turn Processing & Cache Hash Calculation**:
   In `/Users/joshua/Developer/auto-speech/plugin/scripts/python/autoplay_worker.py`:
   - Lines 337–346: Resolves transcript via `TranscriptLocator().locate(session_id=...)` and extracts turn via `MessageSelector().select(t_path, 1)`.
   - Lines 357–370: Computes SHA-256 source hash against raw assistant message bytes:
     ```python
     source_bytes = src.encode("utf-8")
     key_input = source_bytes + b"\x00" + f"{voice_id}:{speed}".encode("utf-8")
     return 0, hashlib.sha256(key_input).hexdigest()
     ```
   - Lines 451–486: If bypass mode is active, speaks `src` directly. Otherwise, executes `ClaudeCliRewriter` and falls back to `src` (raw message) if rewriting fails.
   - Lines 525–537: Dispatches via `client.speak(text, source_hash=source_hash, session_id=session_id, priority=Priority.AUTOPLAY)`.

3. **Transcript Resolution & Message Selector**:
   - In `transcript_locator.py`: `TranscriptLocator().locate(cwd=..., session_id=...)` resolves project slug candidate directories under `~/.claude/projects/` and finds `{session_id}.jsonl` or newest `.jsonl`.
   - In `message_selector.py` lines 51–82: `MessageSelector.select(path, n)` natively supports both Claude Code format (`record["type"] == "assistant"`) and Antigravity format (`record["type"] == "PLANNER_RESPONSE"`).

4. **Narrator Event Pipeline & Session Enrollment**:
   - In `plugin/scripts/shell/narrator_hook.sh` line 46: Checks session marker `~/.claude/auto-speech-narrate-sessions/$SESSION_ID`.
   - In `narrator_hook.sh` lines 87–97: Assembles event record with `ts`, `cwd`, `pid`, `event`, `payload`.
   - In `narrator_service.py` line 59: Tails `EVENTS_LOG = Path("/tmp/auto-speech-narrator-events.jsonl")`.
   - In `narrator_service.py` lines 728–734: Enforces per-session marker recheck at runtime:
     ```python
     session_marker = Path.home() / ".claude" / "auto-speech-narrate-sessions" / session_id
     if not session_marker.exists():
         continue
     ```

5. **Daemon Liveness & Priority Arbitration**:
   - In `daemon_client.py` lines 24–30:
     `Priority.USER_INTERRUPT = 1`, `Priority.EXPLICIT_MCP = 2`, `Priority.AUTOPLAY = 3`, `Priority.TOOL_NARRATION = 4`.
   - Lines 78–106: `DaemonClient.ensure_daemon_running()` automatically boots `narrator_service_start.sh` if daemon socket is dead.

6. **Current Test Baseline**:
   - Running `bash tests/run_all.sh` in `/Users/joshua/Developer/auto-speech-worktree`:
     `ran: 50, failed: 0, all tests passed`.

---

## 2. Logic Chain

1. **Parity Requirement without Shell Hooks**:
   From [Observation 1], the current MCP server only allows verbatim speech via `speak`. However, automated agents running inside Claude Code or Antigravity cannot trigger shell hooks (`Stop`, `PreToolUse`) directly. Adding `autoplay_turn` and `narrate_event` provides programmatic parity.

2. **Autoplay Flow Coherence**:
   From [Observation 2 & 3], `TranscriptLocator` and `MessageSelector` are pure Python standard library modules that extract assistant turns across both Claude Code and Antigravity formats. By extracting the message, computing SHA-256 over raw text + `b"\x00"` + `voice:speed`, applying `ClaudeCliRewriter` (with default template fallback on missing prompt and raw text fallback on rewriter error), and queueing via `DaemonClient(priority=Priority.AUTOPLAY)`, `autoplay_turn` replicates `AutoplayWorker` exactly without subprocess sprawl.

3. **Narrator Pipeline Ingestion & Clean Gating**:
   From [Observation 4], the narrator daemon relies on `~/.claude/auto-speech-narrate-sessions/<session_id>` to filter events. If a session is unenrolled, returning a fatal tool error (`isError=True`) would disrupt AI agent flows. Therefore, `narrate_event` returns a clean notice with `isError=False` (`"Session '<sid>' is not enrolled in narration; event ignored."`). When enrolled, appending JSON records to `/tmp/auto-speech-narrator-events.jsonl` provides atomic ingestion.

4. **Standard Library & Test Isolation**:
   All collaborators (`pathlib`, `json`, `os`, `hashlib`, `time`, `tomllib`) are standard library modules. By accepting optional `locator`, `selector`, `rewriter`, `narrator_events_file`, `narrator_sessions_dir`, `voice_calibration_path`, and `client` in `McpServer.__init__`, unit tests in `tests/test_mcp_server.py` can test all validation and execution branches without touching `~/.claude`, `/tmp`, external binaries, or live daemons.

---

## 3. Caveats

1. **Other M2 Tools**:
   This report specifically focuses on `autoplay_turn` and `narrate_event`. The remaining tools for Milestone M2 (`playback_control`, `session_configure`, `doctor`) should follow the identical pattern of standard library purity and injectable collaborators.
2. **Claude Binary Dependency**:
   When `mode != "raw"` and `bypass_llm=False`, `ClaudeCliRewriter` invokes `claude -p`. In environments where `claude` is not on `PATH` (such as headless CI containers), the rewriter raises `ClaudeCliUnavailable`. The handler is specified to gracefully fall back to speaking the raw turn text so speech playback remains functional.

---

## 4. Conclusion

The concrete implementation strategy for `autoplay_turn` and `narrate_event` is fully formulated and ready for execution by `worker_m2`:
- Schemas: Full JSON-RPC 2.0 schemas defined in `analysis.md` §2.1 and §3.1.
- Handler Blueprints: Complete, copy-paste-ready implementation code specified in `analysis.md` §6.1 and §6.2.
- Error Semantics: Mute errors return `isError=True`; unenrolled narration returns `isError=False` with clean notice.
- Test Strategy: 10 discrete test cases specified in `analysis.md` §7 to ensure 100% coverage and zero regressions across the 50 baseline tests.

---

## 5. Verification Method

To verify the implementation once applied by `worker_m2`:

1. **Unit Test Execution**:
   Run the dedicated MCP test suite:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/python tests/test_mcp_server.py
   ```
   *Expected*: All existing and new tests pass with exit code 0.

2. **Full Regression Suite**:
   Run the global test runner:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   bash tests/run_all.sh
   ```
   *Expected*: All 50+ test suites pass with 0 failures.

3. **Code Style & Linting**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/ruff check plugin/scripts/python/mcp_server.py tests/test_mcp_server.py
   ```
   *Expected*: 0 violations.

4. **Invalidation Conditions**:
   - Any dependency on non-standard-library modules inside `mcp_server.py`.
   - Failure to check `AutoplayGate().worker_gated_off()`.
   - `narrate_event` returning `isError=True` when a session is simply unenrolled.
   - Cache key calculation differing from `sha256(raw_bytes + b"\x00" + f"{voice}:{speed}".encode())`.
