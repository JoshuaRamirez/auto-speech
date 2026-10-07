# Empirical Challenge Report: MCP Server `autoplay_turn` Session Isolation (Milestone 2 Retry)

## 1. Observation

Adversarial stress testing and verification was conducted against the updated implementation of `plugin/scripts/python/mcp_server.py` in `/Users/joshua/Developer/auto-speech-worktree`.

### Baseline Test Suite & Static Quality
- Command: `bash tests/run_all.sh` (executed in `/Users/joshua/Developer/auto-speech-worktree`)
- Result: Ran 50 test suites, 0 failed (`ran: 50, failed: 0, all tests passed`).
- Code quality: `.venv/bin/ruff check plugin/scripts/python/mcp_server.py` -> 0 violations (`All checks passed!`).

### Code Inspection of Remediation
In `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/mcp_server.py`:
- **Input Type Validation (lines 517–523)**:
  ```python
  session_id = args.get("session_id")
  if session_id is not None and not isinstance(session_id, str):
      return _ok(req_id, _tool_result("session_id must be a string", is_error=True))
  if session_id is not None and not session_id.strip():
      return _ok(
          req_id, _tool_result("session_id must be a non-empty string", is_error=True)
      )
  ```
- **Strict Session Isolation & Guard against Fallback Leaks (lines 581–588)**:
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

### Empirical Execution Results

An adversarial test harness (`tests/test_challenger_m2_retry.py`) was executed using `.venv/bin/python tests/test_challenger_m2_retry.py`. Below are the verbatim empirical inputs, JSON-RPC requests, and responses:

#### Scenario 1: Non-existent `session_id` in a workspace containing multiple other Claude session transcripts
- **Environment**: A project directory `.claude/projects/-Users-joshua-Developer-auto-speech-worktree/` populated with multiple active session transcripts (`other-session-1.jsonl`, `other-session-2.jsonl`, `other-session-3.jsonl`).
- **1A: Direct Python API (`McpServer.handle_line`)**:
  - Request:
    ```json
    {"jsonrpc": "2.0", "id": 1001, "method": "tools/call", "params": {"name": "autoplay_turn", "arguments": {"session_id": "nonexistent-session-xyz"}}}
    ```
  - Server Log (stderr verbatim):
    ```
    [locator] slug_dir=/var/folders/tf/mzd8mjpn08n70hsj7jd3bk340000gn/T/auto-speech-test-home-t1-vdccad9p/.claude/projects/-Users-joshua-Developer-auto-speech-worktree
    [locator] session_id=nonexistent-session-xyz but candidate missing; falling back to newest-jsonl
    [locator] newest jsonl /var/folders/tf/mzd8mjpn08n70hsj7jd3bk340000gn/T/auto-speech-test-home-t1-vdccad9p/.claude/projects/-Users-joshua-Developer-auto-speech-worktree/other-session-3.jsonl
    ```
  - Response:
    ```json
    {
      "jsonrpc": "2.0",
      "id": 1001,
      "result": {
        "content": [
          {
            "type": "text",
            "text": "session transcript not found for session_id: nonexistent-session-xyz"
          }
        ],
        "isError": true
      }
    }
    ```
  - Audio Isolation Check: `mock_client.speak.call_count == 0`. No audio synthesized or spoken.
  - Result: **PASSED**.
- **1B: Live Subprocess Stdio JSON-RPC Pipe (`python plugin/scripts/python/mcp_server.py`)**:
  - Request:
    ```json
    {"jsonrpc": "2.0", "id": 1002, "method": "tools/call", "params": {"name": "autoplay_turn", "arguments": {"session_id": "nonexistent-session-xyz"}}}
    ```
  - Response:
    ```json
    {
      "jsonrpc": "2.0",
      "id": 1002,
      "result": {
        "content": [
          {
            "type": "text",
            "text": "session transcript not found for session_id: nonexistent-session-xyz"
          }
        ],
        "isError": true
      }
    }
    ```
  - Result: **PASSED**.

#### Scenario 2: Existing, matching `session_id`
- **Environment**: A matching session transcript `matching-valid-session-12345.jsonl` exists in the workspace.
- **Request**:
  ```json
  {"jsonrpc": "2.0", "id": 2001, "method": "tools/call", "params": {"name": "autoplay_turn", "arguments": {"session_id": "matching-valid-session-12345", "mode": "raw"}}}
  ```
- **Server Log (stderr verbatim)**:
  ```
  [locator] slug_dir=/var/folders/tf/mzd8mjpn08n70hsj7jd3bk340000gn/T/auto-speech-test-home-t2-j9vsv_10/.claude/projects/-Users-joshua-Developer-auto-speech-worktree
  [locator] using session_id file /var/folders/tf/mzd8mjpn08n70hsj7jd3bk340000gn/T/auto-speech-test-home-t2-j9vsv_10/.claude/projects/-Users-joshua-Developer-auto-speech-worktree/matching-valid-session-12345.jsonl
  ```
- **Response**:
  ```json
  {
    "jsonrpc": "2.0",
    "id": 2001,
    "result": {
      "content": [
        {
          "type": "text",
          "text": "Queued autoplay turn (78 chars, mode=raw, hash=f2b53c8a277b5e51)."
        }
      ],
      "isError": false
    }
  }
  ```
- **Verification**: `mock_client.speak.call_count == 1`. Spoke exact text: `"This is a legitimate assistant turn response that is long enough to be spoken."`.
- **Result**: **PASSED**.

#### Scenario 3: Invalid `session_id` types
Tested across both direct API and live subprocess stdio JSON-RPC:
- `session_id: ""` (empty string):
  - Response: `{"isError": true, "content": [{"type": "text", "text": "session_id must be a non-empty string"}]}`
- `session_id: "   "` (whitespace only):
  - Response: `{"isError": true, "content": [{"type": "text", "text": "session_id must be a non-empty string"}]}`
- `session_id: 123` (integer):
  - Response: `{"isError": true, "content": [{"type": "text", "text": "session_id must be a string"}]}`
- `session_id: True` (boolean):
  - Response: `{"isError": true, "content": [{"type": "text", "text": "session_id must be a string"}]}`
- `session_id: ["sess"]` (array):
  - Response: `{"isError": true, "content": [{"type": "text", "text": "session_id must be a string"}]}`
- `session_id: {"id": "sess"}` (object):
  - Response: `{"isError": true, "content": [{"type": "text", "text": "session_id must be a string"}]}`
- Stdio subprocess verification:
  - Request ID 3101 (`session_id: ""`): returns `isError: True`, text `"session_id must be a non-empty string"`
  - Request ID 3102 (`session_id: 123`): returns `isError: True`, text `"session_id must be a string"`
- **Result**: **PASSED**.

#### Scenario 4: Adversarial Boundary & Path Traversal Stress Tests
- `session_id: "../other"`: safely rejected (`isError: True`, text `"session transcript not found for session_id: ../other"`, 0 audio calls).
- `session_id: "../../../etc/passwd"`: safely rejected (`isError: True`, text `"session transcript not found for session_id: ../../../etc/passwd"`, 0 audio calls).
- `session_id: "secret-financial-session-uuid.jsonl"` (when file stem is `secret-financial-session-uuid`): safely rejected (`isError: True`, 0 audio calls).
- `session_id: "secret-financial"` (prefix of secret UUID): safely rejected (`isError: True`, 0 audio calls).
- Omitted `session_id` (`{}` / `None`): successfully targets newest transcript in workspace when intentional (`isError: False`, queued turn).
- **Result**: **PASSED**.

---

## 2. Logic Chain

1. **Premise 1**: In the prior milestone review (`challenger_m2_1/handoff.md`), the core defect identified was that when callers passed a non-existent `session_id`, `TranscriptLocator` fell back to `newest-jsonl` in the workspace, causing `autoplay_turn` to synthesize and speak audio from an unrelated session with `isError: False`.
2. **Observation 2**: In `plugin/scripts/python/mcp_server.py` lines 581–588, explicit verification was added:
   `if session_id and (t_path.name != f"{session_id}.jsonl" and t_path.stem != session_id): return _ok(req_id, _tool_result(f"session transcript not found for session_id: {session_id}", is_error=True))`
3. **Observation 3**: In `plugin/scripts/python/mcp_server.py` lines 517–523, explicit type and emptiness checks were added:
   Non-string values return `"session_id must be a string"` with `is_error=True`.
   Empty strings or whitespace return `"session_id must be a non-empty string"` with `is_error=True`.
4. **Empirical Verification 4**:
   - In Test 1, when calling `autoplay_turn(session_id="nonexistent-session-xyz")` in a workspace with multiple active transcripts, the tool immediately returned `isError: True` with verbatim message `"session transcript not found for session_id: nonexistent-session-xyz"`.
   - The test verified `mock_client.speak.call_count == 0`, confirming that no audio was queued, synthesized, or played.
   - In Test 2, passing an existing valid session ID successfully resolved the transcript, queued the turn text, and returned `isError: False`.
   - In Test 3, all invalid types (`""`, `"   "`, `123`, `True`, list, dict) were rejected cleanly with `isError: True` across both direct Python API and live subprocess stdio JSON-RPC.
5. **Regression Verification 5**:
   - `bash tests/run_all.sh` executed 50 test suites with 0 failures.
   - `.venv/bin/ruff check plugin/scripts/python/mcp_server.py` passed with 0 errors.
6. **Inference 6**: The session isolation vulnerability is fully resolved, all contract invariants are upheld, and no regressions were introduced.

---

## 3. Caveats

- When `session_id` is omitted entirely (the default behavior when no session is targeted), `TranscriptLocator` intentionally falls back to the newest transcript in the project directory. This preserves backward compatibility for standalone CLI calls and single-session environments.

---

## 4. Conclusion

**Verdict: APPROVE**

The implementation in `/Users/joshua/Developer/auto-speech-worktree` completely satisfies all requirements:
1. Strict session isolation in `autoplay_turn` is enforced: non-existent session IDs reliably return `isError: True` with the exact expected error message and never speak foreign session transcripts.
2. Matching existing session IDs succeed with `isError: False`.
3. Invalid session ID types (`""`, `123`, booleans, collections) are rejected with `isError: True`.
4. All 50 existing test suites pass cleanly with 0 lint violations.

---

## 5. Verification Method

To independently reproduce and verify this assessment:

1. **Run the empirical challenger test harness**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/python tests/test_challenger_m2_retry.py
   ```
   **Expected**: Exits code 0 with `ALL EMPIRICAL TESTS PASSED SUCCESSFULLY!`.

2. **Run direct Python verification one-liner**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/python -c '
   import json
   from mcp_server import McpServer
   server = McpServer()
   # 1. Nonexistent session_id test
   res1 = server.handle_line(json.dumps({
       "jsonrpc": "2.0", "id": 1, "method": "tools/call",
       "params": {"name": "autoplay_turn", "arguments": {"session_id": "nonexistent-session-xyz"}}
   }))
   assert res1["result"]["isError"] is True
   assert "session transcript not found for session_id: nonexistent-session-xyz" in res1["result"]["content"][0]["text"]

   # 2. Invalid session_id types test
   res2 = server.handle_line(json.dumps({
       "jsonrpc": "2.0", "id": 2, "method": "tools/call",
       "params": {"name": "autoplay_turn", "arguments": {"session_id": ""}}
   }))
   assert res2["result"]["isError"] is True
   assert "session_id must be a non-empty string" in res2["result"]["content"][0]["text"]

   res3 = server.handle_line(json.dumps({
       "jsonrpc": "2.0", "id": 3, "method": "tools/call",
       "params": {"name": "autoplay_turn", "arguments": {"session_id": 123}}
   }))
   assert res3["result"]["isError"] is True
   assert "session_id must be a string" in res3["result"]["content"][0]["text"]
   print("ALL INVARIANTS EMPIRICALLY VERIFIED!")
   '
   ```

3. **Run the full test suite and linter**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   bash tests/run_all.sh
   .venv/bin/python -m ruff check plugin/scripts/python/mcp_server.py
   ```
   **Expected**: 50/50 test suites pass, 0 ruff errors.
