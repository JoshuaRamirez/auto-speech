# Empirical Challenge Report: MCP Server `autoplay_turn` & `narrate_event`

## 1. Observation

Adversarial stress testing was conducted against `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/mcp_server.py` across two execution paths:
1. **Direct In-Memory Python API**: Instantiating `McpServer` with mocked and unmocked subsystems.
2. **Subprocess Stdio JSON-RPC 2.0 Protocol Pipe**: Spawning `.venv/bin/python plugin/scripts/python/mcp_server.py` as a real OS subprocess exchanging newline-delimited JSON-RPC messages via stdin/stdout.

Baseline test suite execution:
- Command: `bash tests/run_all.sh` (executed in `/Users/joshua/Developer/auto-speech-worktree`)
- Result: Ran 50 test suites, 0 failed (`ran: 50, failed: 0, all tests passed`).
- Code quality: `.venv/bin/ruff check plugin/scripts/python/mcp_server.py` -> 0 violations (`All checks passed!`).

Empirical stress test suite was executed via `.venv/bin/python tests/test_challenger_harness.py`. Below are the verbatim empirical observations for all 7 scenarios and additional stress boundaries:

### Scenario 1: `autoplay_turn` when transcript does not exist
- **JSON-RPC Request**:
  ```json
  {"jsonrpc": "2.0", "id": 101, "method": "tools/call", "params": {"name": "autoplay_turn", "arguments": {"transcript_path": "/tmp/nonexistent_dir_xyz/missing.jsonl"}}}
  ```
- **JSON-RPC Response**:
  ```json
  {"jsonrpc": "2.0", "id": 101, "result": {"content": [{"type": "text", "text": "transcript not found: /tmp/nonexistent_dir_xyz/missing.jsonl"}], "isError": true}}
  ```
- **Result**: PASSED. Returns tool error (`isError: True`) with clear error message.

---

### Scenario 2: `autoplay_turn` with non-existent `session_id`
- **Case 2A: Isolated directory without any Claude project or transcripts**:
  - Request:
    ```json
    {"jsonrpc": "2.0", "id": 201, "method": "tools/call", "params": {"name": "autoplay_turn", "arguments": {"session_id": "nonexistent-session-0000-deadbeef"}}}
    ```
  - Response:
    ```json
    {"jsonrpc": "2.0", "id": 201, "result": {"content": [{"type": "text", "text": "no transcript found for session: no project directory matched cwd /private/var/folders/tf/mzd8mjpn08n70hsj7jd3bk340000gn/T/test-auto-home-c7e4mleq; tried: ... Run Claude Code in this cwd first."}], "isError": true}}
    ```
  - Result: Returns tool error (`isError: True`).

- **Case 2B: Normal workspace directory containing existing Claude project transcripts**:
  - Request:
    ```json
    {"jsonrpc": "2.0", "id": 202, "method": "tools/call", "params": {"name": "autoplay_turn", "arguments": {"session_id": "absolutely-nonexistent-session-xyz-9999"}}}
    ```
  - Server Log (stderr verbatim):
    ```
    [locator] slug_dir=/Users/joshua/.claude/projects/-Users-joshua-Developer-auto-speech-worktree
    [locator] session_id=absolutely-nonexistent-session-xyz-9999 but candidate missing; falling back to newest-jsonl
    [locator] newest jsonl /Users/joshua/.claude/projects/-Users-joshua-Developer-auto-speech-worktree/e20662e4-96a5-4499-bc98-0c23f8c9c507.jsonl
    ```
  - Response (verbatim):
    ```json
    {"jsonrpc": "2.0", "id": 202, "result": {"content": [{"type": "text", "text": "Queued autoplay turn (589 chars, mode=summary, hash=bd61657a0ab59a8b)."}], "isError": false}}
    ```
  - Code observation (`transcript_locator.py:118-122` & `138-140`):
    ```python
    if sid:
        if slug_dir is not None:
            candidate = slug_dir / f"{sid}.jsonl"
            if candidate.exists():
                return candidate
        if self.PROJECTS_ROOT.is_dir():
            matches = list(self.PROJECTS_ROOT.glob(f"*/{sid}.jsonl"))
            if matches:
                return matches[0]
        if slug_dir is not None:
            print(f"[locator] session_id={sid} but candidate missing; falling back to newest-jsonl", file=sys.stderr)
    ...
    newest = candidates[0]
    return newest
    ```
  - **Result**: **FAILED**. When an explicit non-existent `session_id` is passed, `TranscriptLocator` falls back to `newest-jsonl` in the project directory, and `_call_autoplay_turn` synthesizes and speaks audio from a completely unrelated session with `isError: false`. It does NOT return a tool error.

---

### Scenario 3: `autoplay_turn` with global mute enabled (`~/.claude/auto-speech.disabled`)
- **JSON-RPC Request**:
  ```json
  {"jsonrpc": "2.0", "id": 301, "method": "tools/call", "params": {"name": "autoplay_turn", "arguments": {}}}
  ```
- **JSON-RPC Response**:
  ```json
  {"jsonrpc": "2.0", "id": 301, "result": {"content": [{"type": "text", "text": "auto-speech is globally muted (~/.claude/auto-speech.disabled); nothing was spoken"}], "isError": true}}
  ```
- **Result**: PASSED. Returns tool error (`isError: True`) with clear notice. Audio synthesis and worker spawning are suppressed (`spawner.assert_not_called()`).

---

### Scenario 4: `autoplay_turn` with `mode="raw"` and `bypass_llm=True`
- **JSON-RPC Request**:
  ```json
  {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "autoplay_turn", "arguments": {"transcript_path": "/var/folders/.../session.jsonl", "mode": "raw", "bypass_llm": true}}}
  ```
- **JSON-RPC Response**:
  ```json
  {"jsonrpc": "2.0", "id": 4, "result": {"content": [{"type": "text", "text": "Queued autoplay turn (81 chars, mode=raw, hash=947ae59e0570c6e6)."}], "isError": false}}
  ```
- **Verification**: `mock_rewriter.rewrite` was verified via assertions to NOT be called (`mock_rewriter.rewrite.assert_not_called()`). The exact raw assistant text (81 characters) was passed to the speech queue.
- **Additional Verification**:
  - `mode="summary"` with `bypass_llm=True`: rewriter not called.
  - `mode="raw"` with `bypass_llm=False`: rewriter not called (`mode="raw"` forces `bypass = True` per line 625-626).
- **Result**: PASSED.

---

### Scenario 5: `narrate_event` when session is unenrolled
- **JSON-RPC Request**:
  ```json
  {"jsonrpc": "2.0", "id": 501, "method": "tools/call", "params": {"name": "narrate_event", "arguments": {"event": "PreToolUse", "session_id": "session-unenrolled-42", "payload": {"tool_name": "run_command"}}}}
  ```
- **JSON-RPC Response**:
  ```json
  {"jsonrpc": "2.0", "id": 501, "result": {"content": [{"type": "text", "text": "Session 'session-unenrolled-42' is not enrolled in narration; event ignored."}], "isError": false}}
  ```
- **Verification**: Verified that `/tmp/.../events.jsonl` was NOT created and no event record was appended.
- **Result**: PASSED. Clean notice returned without error (`isError: False`).

---

### Scenario 6: `narrate_event` missing `event` parameter
- **Case 6A (arguments is empty `{}` or `event` omitted)**:
  - Request:
    ```json
    {"jsonrpc": "2.0", "id": 601, "method": "tools/call", "params": {"name": "narrate_event", "arguments": {}}}
    ```
  - Response:
    ```json
    {"jsonrpc": "2.0", "id": 601, "result": {"content": [{"type": "text", "text": "event must be a non-empty string"}], "isError": true}}
    ```
- **Case 6B (`event` is empty string `""` or whitespace)**:
  - Request:
    ```json
    {"jsonrpc": "2.0", "id": 602, "method": "tools/call", "params": {"name": "narrate_event", "arguments": {"event": ""}}}
    ```
  - Response:
    ```json
    {"jsonrpc": "2.0", "id": 602, "result": {"content": [{"type": "text", "text": "event must be a non-empty string"}], "isError": true}}
    ```
- **Case 6C (`params` missing `arguments` entirely)**:
  - Request:
    ```json
    {"jsonrpc": "2.0", "id": 603, "method": "tools/call", "params": {"name": "narrate_event"}}
    ```
  - Response:
    ```json
    {"jsonrpc": "2.0", "id": 603, "result": {"content": [{"type": "text", "text": "event must be a non-empty string"}], "isError": true}}
    ```
- **Case 6D (`arguments` is not an object / malformed structure)**:
  - Request:
    ```json
    {"jsonrpc": "2.0", "id": 604, "method": "tools/call", "params": {"name": "narrate_event", "arguments": "invalid"}}
    ```
  - Response:
    ```json
    {"jsonrpc": "2.0", "id": 604, "error": {"code": -32602, "message": "arguments must be an object"}}
    ```
- **Result**:
  - The implementation treats missing `event` inside `arguments` as a **tool execution error** (`isError: True`), consistent with MCP tool error architecture across `speak` and `playback_control`.
  - Protocol error `-32602 (INVALID_PARAMS)` is reserved for structural protocol errors (malformed `arguments` or unknown tool).
  - Note: This diverges from the literal prompt phrasing (`verify returns protocol error code -32602`), but matches the existing test suite (`tests/test_mcp_server.py:533-538`) which explicitly asserts `assert res["result"]["isError"] is True`.

---

### Scenario 7: `narrate_event` with malformed `payload`
- Tested payloads with enrolled session:
  - `payload: None` -> Defensively defaulted to `{}`. Result: `isError: False`, `"Narrator event 'PreToolUse' ingested for session 'test-sess-1'."`.
  - `payload: {}` -> Result: `isError: False`, `"Narrator event 'PreToolUse' ingested for session 'test-sess-1'."`.
  - `payload: "a string"` -> Result: `isError: True`, `"payload must be an object"`.
  - `payload: [1, 2, 3]` -> Result: `isError: True`, `"payload must be an object"`.
  - `payload: 12345` -> Result: `isError: True`, `"payload must be an object"`.
  - `payload: true` -> Result: `isError: True`, `"payload must be an object"`.
- Server handled all malformed types defensively without raising unhandled exceptions or crashing.
- **Result**: PASSED.

---

### Additional Adversarial Stress Tests
- `autoplay_turn` invalid ordinal type (`"first"`, `0`): returns `isError: True` (`"ordinal must be an integer >= 1"`).
- `autoplay_turn` invalid mode (`"alien_voice"`): returns `isError: True` (`"mode must be 'summary', 'verbatim', or 'raw'"`).
- `autoplay_turn` non-boolean bypass_llm (`"true"`): returns `isError: True` (`"bypass_llm must be a boolean"`).
- `narrate_event` non-string cwd (`12345`): returns `isError: True` (`"cwd must be a string"`).
- Invalid JSON line (`"INVALID JSON {"`): returns protocol error `-32700 (PARSE_ERROR)`.
- Request missing `jsonrpc: "2.0"`: returns protocol error `-32600 (INVALID_REQUEST)`.
- Unknown method (`"nonexistent_method"`): returns protocol error `-32601 (METHOD_NOT_FOUND)`.
- Unknown tool in `tools/call` (`"ghost_tool"`): returns protocol error `-32602 (INVALID_PARAMS)` with message `"unknown tool: ghost_tool"`.

---

## 2. Logic Chain

1. **Premise 1**: The dispatch specification explicitly mandates:
   > "2. `autoplay_turn` with non-existent session_id -> verify returns tool error gracefully."
2. **Observation 2**: In `plugin/scripts/python/mcp_server.py:569-577`, `_call_autoplay_turn` delegates transcript resolution to `TranscriptLocator().locate(cwd=Path.cwd(), session_id=session_id)`.
3. **Observation 3**: In `plugin/scripts/python/transcript_locator.py:118-140`, if `session_id` does not match any `.jsonl` file in `slug_dir` or `PROJECTS_ROOT`, but `slug_dir` contains any other `.jsonl` files, `locate()` prints a warning (`session_id=... but candidate missing; falling back to newest-jsonl`) and returns the newest transcript in the project.
4. **Inference 4**: Consequently, when an AI agent or caller invokes `autoplay_turn` with a non-existent or stale `session_id` in any standard workspace, `_call_autoplay_turn` does NOT fail. It succeeds (`isError: False`), extracts turn text from a different session, and plays it out loud.
5. **Impact 5**:
   - This violates Requirement 2.
   - This creates a critical privacy and audio isolation violation: requesting playback for session A when session A does not exist results in audio playback of confidential content from session B.
6. **Observation 6**: In Scenario 6, the dispatch asked to verify `-32602` for missing `event`. Line 684 of `mcp_server.py` returns `_ok(req_id, _tool_result("event must be a non-empty string", is_error=True))`. While returning a tool error is standard MCP practice for invalid tool input, it constitutes a contract deviation from the dispatch expectation.

---

## 3. Caveats

- In a completely empty test directory where `~/.claude/projects/` contains no entry for the cwd, `TranscriptLocator.locate()` raises `TranscriptNotFoundError`, which `_call_autoplay_turn` catches and returns as `isError: True`. The defect only manifests when the current workspace has prior transcripts in `~/.claude/projects/` (which is the standard working environment for any real developer).
- The behavior of `TranscriptLocator` falling back to `newest-jsonl` was originally designed for shell hooks (`autoplay_hook.sh`) where `session_id` might be passed before Claude Code flushed the new transcript to disk. However, when exposed via MCP tool `autoplay_turn(session_id=...)`, callers expect explicit session targeting.

---

## 4. Conclusion

**Verdict: REQUEST_CHANGES**

The MCP implementation fails Requirement 2 of the dispatch:
- **Defect**: `autoplay_turn` with a non-existent `session_id` silently falls back to and synthesizes the latest transcript in the workspace project directory with `isError: False`, instead of returning a tool error.
- **Required Fix**:
  In `plugin/scripts/python/mcp_server.py`, line 571: when `session_id` is explicitly passed in `args`, verify that the located transcript matches the requested `session_id`. If `t_path.stem != session_id` and `t_path.name != f"{session_id}.jsonl"`, return a tool error:
  ```python
  if session_id and t_path.name != f"{session_id}.jsonl":
      return _ok(
          req_id,
          _tool_result(f"session transcript not found for session_id: {session_id}", is_error=True),
      )
  ```
  Or pass a parameter to `TranscriptLocator(strict_session=True)` to disable the `newest-jsonl` fallback when an explicit `session_id` is requested.
- **Recommendation for Requirement 6**:
  Clarify whether `narrate_event` missing `event` should remain a tool error (`isError: True`, which is standard MCP style and matches `test_mcp_server.py:537`) or be upgraded to protocol error `-32602`.

---

## 5. Verification Method

To independently verify all findings and reproduce the defect:

1. Run the empirical test harness in the worktree:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/python tests/test_challenger_harness.py
   ```
2. Observe output for Scenario 2:
   ```bash
   # Direct reproduction of Scenario 2 defect via python one-liner:
   .venv/bin/python -c '
   from mcp_server import McpServer
   import json
   server = McpServer()
   res = server.handle_line(json.dumps({
       "jsonrpc": "2.0", "id": 1, "method": "tools/call",
       "params": {"name": "autoplay_turn", "arguments": {"session_id": "nonexistent-session-xyz"}}
   }))
   print("isError:", res["result"].get("isError"))
   print("Content:", res["result"]["content"][0]["text"])
   assert res["result"].get("isError") is True, "FAILED: Expected isError: True for nonexistent session_id"
   '
   ```
   **Expected**: AssertionError (actual `isError: False` and `"Queued autoplay turn ..."`).

3. To verify the entire existing test suite:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   bash tests/run_all.sh
   ```
