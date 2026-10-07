# Handoff Report: Specification Mining for 6-Tool MCP Server (M2)

**Agent**: `teamwork_preview_spec_miner` (`spec_miner_m2`)  
**Workspace**: `/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m2`  
**Handoff Type**: Hard Handoff  
**Target Milestone**: M2 (`plugin/scripts/python/mcp_server.py` & `tests/test_mcp_server.py`)  

---

## 1. Observation

1. **Original Request Contract**:
   `ORIGINAL_REQUEST.md` (header `## 2026-10-06T20:47:50Z`, lines 132–140, 169–173) mandates:
   > "R2. Full Lifecycle Hook Parity via MCP Server: Extend the auto-speech Model Context Protocol (MCP) server so that any action currently driven by shell hooks (Stop, PreToolUse, PostToolUse, UserPromptSubmit) or slash commands can be executed programmatically by an AI agent via MCP tool calls: speak, autoplay_turn, narrate_event, playback_control, session_configure, doctor."
   > "tools/list on the MCP server advertises the expanded tool suite: speak, autoplay_turn, narrate_event, playback_control, session_configure, and doctor."
   > "All speech-producing MCP tools honor the global mute (~/.claude/auto-speech.disabled)."

2. **Existing MCP Server Baseline**:
   `tests/test_mcp_server.py` (lines 84–89) verifies only a single tool:
   ```python
   def test_tools_list_has_only_speak() -> None:
       s, _ = _server()
       tools = s.handle_line(_req("tools/list"))["result"]["tools"]
       assert [t["name"] for t in tools] == ["speak"]
       assert tools[0]["inputSchema"]["required"] == ["text"]
   ```
   And `plugin/scripts/python/mcp_server.py` (lines 41–63, 168–172) handles only `speak`:
   ```python
   def _tools_call(self, req_id, params: dict) -> dict:
       name = params.get("name")
       if name != "speak":
           return _error(req_id, INVALID_PARAMS, f"unknown tool: {name}")
   ```

3. **Subsystem Logic in Existing Codebase**:
   - `cli.py` lines 95–102, 283–406, 408–511, 513–630, 765–774 contains established implementations for transport control (`pause`, `resume`, `stop`, `restart`, `seek`), replay, autoplay enrollment/scoping, narration session tracking, and doctor execution.
   - `autoplay_gate.py` lines 54–60 exposes `worker_gated_off()`, checking `self._home / ".claude" / "auto-speech.disabled"`.
   - `daemon_client.py` lines 24–30, 108–156 exposes `Priority.USER_INTERRUPT` (1), `Priority.EXPLICIT_MCP` (2), `Priority.AUTOPLAY` (3), `Priority.TOOL_NARRATION` (4), `speak()`, `play_cache()`, and `interrupt()`.
   - `message_selector.py` lines 31–108 extracts assistant turns, raising `NoSuchAssistantTurn` when fewer turns exist.
   - `doctor.py` lines 60–274 and `health_report.py` lines 29–62 expose `Doctor().run()` returning `HealthReport`, where `healthy == false` drives non-zero exit codes.

4. **Explorer Survey Blueprint**:
   `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_2/analysis.md` lines 74–398 specifies JSON schemas, execution paths, and error modes for all 6 tools.

---

## 2. Logic Chain

1. **Interface Completeness**:
   Per Observation 1, the MCP server must support all 6 tools with standard JSON-RPC 2.0 framing.
   Therefore, `tools/list` must advertise 6 tool descriptors whose `inputSchema` matches the definitions mined in `analysis.md` §5.1, and `_tools_call` must route to all 6 tools.

2. **Backward Compatibility Preservation**:
   Observation 2 demonstrates 17 existing tests in `tests/test_mcp_server.py`.
   All 12 existing `speak` tests (verbatim queueing, bad text rejection, oversize rejection, global mute rejection, daemon client invocation, worker fallback on connection error and error status) must remain unaltered and passing. Only `test_tools_list_has_only_speak` must be upgraded to `test_tools_list_advertises_all_six_tools`.

3. **Hermetic Test Isolation**:
   Observation 3 shows that all subsystem components (`AutoplayGate`, `SessionEnrollment`, `SoloScope`, `Doctor`) accept an overridable `home: Path | None` and `tmp: Path | None` parameter.
   By injecting temporary directories and mock instances (`DaemonClient`, `MpvIpc`, `CacheStore`), tests can thoroughly verify all 6 tools without touching `/tmp/auto-speech-daemon.sock`, real speakers, or user dotfiles (`~/.claude`).

4. **Speech Safety Invariant**:
   Observation 1 and Observation 3 establish that `worker_gated_off()` must silence all speech-producing tools.
   Therefore, `speak`, `autoplay_turn`, `narrate_event`, `playback_control(action="replay")`, and `playback_control(action="resume")` must each have dedicated tests asserting that active global mute returns `isError: true` and blocks daemon/worker execution. Non-speech tools (`stop`, `pause`, `session_configure`, `doctor`) must proceed normally when muted.

5. **Test Matrix Formulation**:
   Connecting all requirements yields an exhaustive suite of 47 tests organized into 7 functional groups in `analysis.md` §7, completely specifying the assertion suite for Milestone M2.

---

## 3. Caveats

1. **Antigravity Transcript Format**:
   While Claude Code produces JSONL transcripts with `type == "assistant"`, Antigravity produces `type == "PLANNER_RESPONSE"`. `MessageSelector` handles both formats natively, but test cases should explicitly verify that `autoplay_turn` succeeds with both schemas.
2. **Narration Event Pipeline Buffer**:
   In `narrate_event`, events are appended to `/tmp/auto-speech-narrator-events.jsonl` where `narrator_service.py` tails them. Tests should verify atomic line appending and ensure mock client `ensure_daemon_running()` is triggered.
3. **No Code Implementation**:
   Per the miner's mandate, zero production code or test implementation was written to git tracked files; all findings and assertion specifications reside in `analysis.md`.

---

## 4. Conclusion

Milestone M2's test specification is completely mined and verified against the authoritative codebase and documentation.
- All 6 tools (`speak`, `autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`, `doctor`) have exact schemas, required properties, error modes, and edge cases defined.
- A concrete catalog of 47 unit test functions with exact assertion statements has been specified in `/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m2/analysis.md`.
- Full backward compatibility with the existing 17 `test_mcp_server.py` tests is guaranteed.
- The test harness is designed to be 100% hermetic using standard library temporary directories and mock objects.

---

## 5. Verification Method

1. **Inspect Specification Report**:
   ```bash
   view_file /Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m2/analysis.md
   ```
2. **Verify Baseline Test Suite Passes in Worktree**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/python tests/test_mcp_server.py
   ```
   (Outputs `mcp_server: 17 tests passed`)
3. **Verify Implementation Target Against Test Specification**:
   Once implemented by the executor agent, run:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/python tests/test_mcp_server.py
   bash tests/run_all.sh
   .venv/bin/ruff check tests/test_mcp_server.py plugin/scripts/python/mcp_server.py
   ```
   Pass condition: 47/47 tests pass in `test_mcp_server.py`, all 42+ suites pass in `run_all.sh`, and 0 ruff lint errors.
