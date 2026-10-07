# Review Handoff Report: Milestone M2 (Tool Schemas, Backward Compatibility, & Error Handling)

**Author**: `teamwork_preview_reviewer` (`reviewer_m2_2`)  
**Roles**: Reviewer, Adversarial Critic  
**Timestamp**: 2026-10-06T22:28:45Z  
**Target Repository**: `/Users/joshua/Developer/auto-speech-worktree`  
**Parent Agent**: `4db9af0e-498d-49a5-831a-0009fd31326e`  

---

## Review Summary

**Verdict**: **APPROVE**  
**Integrity Status**: **CLEAN (0 integrity violations detected)**  
**Overall Risk Assessment**: **LOW**

The Milestone M2 implementation in `/Users/joshua/Developer/auto-speech-worktree` expands `plugin/scripts/python/mcp_server.py` to a complete 6-tool suite (`speak`, `autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`, `doctor`) with strict schema validation, 100% backward compatibility for `speak`, comprehensive global mute enforcement, and defensive error handling against all tested adversarial inputs.

---

## 1. Observation

1. **Tool Schemas & Advertisement**:
   - Inspected `plugin/scripts/python/mcp_server.py` (lines 72–293). All 6 tool schemas are defined:
     - `speak`: `required: ["text"]`, `additionalProperties: False`, char limit 20,000.
     - `autoplay_turn`: optional arguments (`session_id`, `transcript_path`, `mode` enum `["summary", "verbatim", "raw"]`, `size` enum `["small", "medium", "large"]`, `ordinal` default 1, `bypass_llm`), `additionalProperties: False`.
     - `narrate_event`: `required: ["event"]`, optional `payload`, `session_id`, `cwd`, `additionalProperties: False`.
     - `playback_control`: `required: ["action"]` with enum `["pause", "resume", "stop", "restart", "seek", "replay"]`, optional `target`, `ordinal` default 1, `additionalProperties: False`.
     - `session_configure`: optional `session_id`, `autoplay` enum `["on", "off", "status"]`, `narrate` enum `["on", "off", "status"]`, `mode` enum `["summary", "verbatim", "raw"]`, `size` enum `["small", "medium", "large"]`, `scope` enum `["all", "solo", "status"]`, plus alternative dispatch (`subsystem`, `action`, `value`), `additionalProperties: False`.
     - `doctor`: optional `as_json`, `json_format`, `additionalProperties: False`.
   - Executed JSON-RPC `tools/list`:
     ```bash
     echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | .venv/bin/python plugin/scripts/python/mcp_server.py
     ```
     Returned exit code 0 and valid JSON-RPC response with `"tools"` list matching all 6 defined schemas.

2. **Backward Compatibility of `speak`**:
   - Compared `mcp_server.py` against base commit (`git show HEAD:plugin/scripts/python/mcp_server.py`).
   - The `SPEAK_TOOL` schema, argument requirements (`text`), character cap (`MAX_CHARS = 20000`), error responses, and queuing mechanics remain identical.
   - `McpServer.__init__` retains previous keyword arguments (`spawner`, `gate`, `client`) with defaults, ensuring legacy instantiation patterns remain unbroken.
   - All 16 legacy unit tests in `tests/test_mcp_server.py` pass without modification.

3. **Global Mute Enforcement**:
   - Inspected lines 490 (`speak`), 548 (`autoplay_turn`), 697 (`narrate_event`), 819 (`playback_control` resume), and 926 (`playback_control` replay).
   - In every case, `self._gate.worker_gated_off()` is checked prior to synthesizing, enqueueing, or resuming audio.
   - Probed via programmatic unit test with `~/.claude/auto-speech.disabled`: all 5 operations abort cleanly with informative notices (`isError: True`) and produce 0 audio or subprocess invocations.

4. **Defensive Error Handling & Adversarial Robustness**:
   - Executed 43 distinct adversarial inputs against `McpServer` covering:
     - Malformed JSON-RPC protocol frames (missing methods, invalid params types, unknown tool names).
     - Empty, whitespace-only, non-string, and oversized `text` values in `speak`.
     - Non-existent transcripts, empty turns, sub-minimum character lengths, invalid mode/size enums, and non-positive ordinals in `autoplay_turn`.
     - Malformed events, non-dict payloads, and non-string paths in `narrate_event`.
     - Unknown actions, missing seek targets, non-finite targets (`nan`, `+inf`, `-inf`, float overflows), negative ordinals, and empty cache states in `playback_control`.
     - Invalid action selectors and missing session IDs on required mutations in `session_configure`.
     - Daemon disconnections and rewriter timeouts/crashes.
   - Result: 0 unhandled exceptions or tracebacks. Every condition returned either a structured protocol error or a clean tool error response.

5. **Empirical Test Suite & Lint Execution**:
   - Command: `.venv/bin/python tests/test_mcp_server.py`
     - Output: `mcp_server: 52 tests passed` (exit code 0).
   - Command: `bash tests/run_all.sh`
     - Output: `ran: 50, failed: 0, all tests passed` (exit code 0).
   - Command: `.venv/bin/ruff check plugin/scripts/python/mcp_server.py tests/test_mcp_server.py`
     - Output: `All checks passed!` (exit code 0).

---

## 2. Logic Chain

1. **Tool Schema Compliance (Obs. 1)**:
   The user request and `PROJECT.md` require an expanded 6-tool MCP suite providing parity with Claude Code and Antigravity lifecycle hooks. The `tools/list` response directly advertises these 6 tools with strictly typed JSON schemas, `additionalProperties: False`, and exact required properties.
2. **Backward Compatibility Preservation (Obs. 2)**:
   Existing consumers and tests calling `speak` require unchanged signatures and semantics. Because the tool name, description, schema, parameter validation, and `McpServer` default signature are preserved, existing callers experience zero behavioral breakage or schema divergence.
3. **Mute Invariant Guarantees (Obs. 3)**:
   The system invariant requires that `~/.claude/auto-speech.disabled` mutes all speech generation. Verifying that each audio-producing tool checks `AutoplayGate().worker_gated_off()` proves that no speech bypasses user muting.
4. **Fault Tolerance & System Stability (Obs. 4)**:
   AI agents using MCP can provide unpredictable or malformed tool arguments. By validating argument types before invocation, catching subprocess/IPC errors, providing offline fallbacks (`spawn_say_worker` and raw text fallback), and validating numeric inputs with `math.isfinite`, `McpServer` ensures continuous server availability without unhandled crashes.
5. **Quality & Regression Freedom (Obs. 5)**:
   The 100% green execution of the 52 unit tests, the 50 project test suites, and ruff linting confirms the implementation is clean, resilient, and regression-free.

---

## 3. Caveats

- Milestone M3 will address shell script hooks (`autoplay_hook.sh`, `narrator_hook.sh`) and slash command delegations (`plugin/commands/`); their updates were not in M2's implementation scope.
- No caveats regarding tool schema correctness, backward compatibility, global mute enforcement, or defensive error handling.

---

## 4. Conclusion

Milestone M2 meets all architectural and quality criteria specified in `PROJECT.md` and `ORIGINAL_REQUEST.md`. No integrity violations, facade shortcuts, or unhandled exceptions exist.

**Final Verdict**: **APPROVE**

---

## 5. Verification Method

To independently reproduce and verify this review in `/Users/joshua/Developer/auto-speech-worktree`:

1. **Verify MCP Unit Test Suite**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/python tests/test_mcp_server.py
   ```
   *Expected*: `mcp_server: 52 tests passed` and exit code 0.

2. **Verify Tool Advertisement via JSON-RPC**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | .venv/bin/python plugin/scripts/python/mcp_server.py
   ```
   *Expected*: JSON response with 6 tools: `speak`, `autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`, `doctor`.

3. **Verify Global Mute Enforcement Across All Tools**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/python -c '
   import json, tempfile
   from pathlib import Path
   import sys
   sys.path.insert(0, "plugin/scripts/python")
   from mcp_server import McpServer
   from autoplay_gate import AutoplayGate

   tmp = Path(tempfile.mkdtemp())
   (tmp / ".claude").mkdir()
   (tmp / ".claude" / "auto-speech.disabled").touch()
   srv = McpServer(home=tmp, gate=AutoplayGate(home=tmp))

   for name, args in [("speak", {"text": "hi"}), ("autoplay_turn", {}), ("narrate_event", {"event": "Stop"}), ("playback_control", {"action": "resume"}), ("playback_control", {"action": "replay"})]:
       res = srv.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}))
       assert res["result"]["isError"] is True
       assert "globally muted" in res["result"]["content"][0]["text"]
   print("Global mute verified.")
   '
   ```
   *Expected*: `Global mute verified.` and exit code 0.

4. **Verify Lint & Full Test Suite**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/ruff check plugin/scripts/python/mcp_server.py tests/test_mcp_server.py
   bash tests/run_all.sh
   ```
   *Expected*: `All checks passed!` and `ran: 50, failed: 0, all tests passed`.
