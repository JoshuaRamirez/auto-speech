# Handoff Report: Cross-Session Isolation Defect in `_call_autoplay_turn`

## 1. Observation

### 1.1 Verbatim Defect Reproduction
Adversarial stress testing against `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/mcp_server.py` was executed directly in `.venv/bin/python`:
```bash
PYTHONPATH=plugin/scripts/python .venv/bin/python -c '
from mcp_server import McpServer
import json
server = McpServer()
res = server.handle_line(json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
    "params": {"name": "autoplay_turn", "arguments": {"session_id": "nonexistent-session-xyz"}}
}))
print("isError:", res["result"].get("isError"))
print("Content:", res["result"]["content"][0]["text"])
'
```
**Observed Output**:
```
[locator] slug_dir=/Users/joshua/.claude/projects/-Users-joshua-Developer-auto-speech-worktree
[locator] session_id=nonexistent-session-xyz but candidate missing; falling back to newest-jsonl
[locator] newest jsonl /Users/joshua/.claude/projects/-Users-joshua-Developer-auto-speech-worktree/a5dd287b-9cf4-4033-99d3-f111895e23a7.jsonl
isError: False
Content: Queued autoplay turn (604 chars, mode=summary, hash=bd61657a0ab59a8b).
```

### 1.2 Underlying Code Locations
1. In `plugin/scripts/python/mcp_server.py:568-577`:
   ```python
   else:
       locator = self._locator or TranscriptLocator()
       try:
           t_path = locator.locate(cwd=Path.cwd(), session_id=session_id)
       except Exception as exc:
           return _ok(
               req_id,
               _tool_result(f"no transcript found for session: {exc}", is_error=True),
           )
   ```
2. In `plugin/scripts/python/transcript_locator.py:118-140`:
   ```python
   if slug_dir is not None:
       print(
           f"[locator] session_id={sid} but candidate missing; falling back to newest-jsonl",
           file=sys.stderr,
       )
   ...
   newest = candidates[0]
   return newest
   ```
3. In `tests/test_transcript_locator.py:162-173`:
   ```python
   loc = _patched_locator(project_root)
   os.environ["CLAUDE_SESSION_ID"] = "missing-session"
   try:
       result = loc.locate(cwd)
   finally:
       ...
   assert result == new, f"expected newest fallback; got {result}"
   ```
4. In `tests/test_mcp_server.py`:
   - No existing test exercises `autoplay_turn` with an explicit `session_id`. All current tests either pass `transcript_path` or rely on default empty arguments `{}`.

### 1.3 Baseline Suite Status
- Running `bash tests/run_all.sh` in `/Users/joshua/Developer/auto-speech-worktree`: 50 test suites ran, 0 failed (`ran: 50, failed: 0, all tests passed`).
- Running `.venv/bin/ruff check plugin/scripts/python/mcp_server.py tests/test_mcp_server.py`: 0 violations (`All checks passed!`).

---

## 2. Logic Chain

1. **Premise 1 (Observation 1.1 & 1.2)**: When `session_id="nonexistent-session-xyz"` is supplied to `_call_autoplay_turn`, `TranscriptLocator.locate()` finds the Claude project directory for the current working directory. The candidate file `nonexistent-session-xyz.jsonl` does not exist. `TranscriptLocator` falls back to the newest file on disk (`a5dd287b-9cf4-4033-99d3-f111895e23a7.jsonl`) and returns it without raising an exception.
2. **Premise 2 (Observation 1.2)**: `_call_autoplay_turn` in `mcp_server.py` assumes that any `Path` returned by `locator.locate()` without raising an exception represents the requested session. It never compares the resolved file name against `session_id`.
3. **Inference 3**: As a result, `_call_autoplay_turn` extracts turn text from `a5dd287b-9cf4-4033-99d3-f111895e23a7.jsonl` (an unrelated conversation), sends it to `DaemonClient.speak()`, and returns `isError: False` to the MCP client. This creates a critical privacy violation by speaking unrelated confidential transcripts.
4. **Premise 4 (Observation 1.2 - Point 3)**: Modifying `TranscriptLocator` itself to raise an exception on missing `session_id` would break `tests/test_transcript_locator.py:173`, which explicitly requires fallback behavior for shell hooks and legacy callers.
5. **Deduction 5**: Therefore, the correct architectural boundary for enforcement is `plugin/scripts/python/mcp_server.py` in `_call_autoplay_turn`. Validating that `t_path.name == f"{session_id}.jsonl"` or `t_path.stem == session_id` when `session_id` is supplied ensures strict session isolation on the MCP API surface while leaving legacy hook semantics in `TranscriptLocator` intact.

---

## 3. Caveats

- **Omitted `session_id` Behavior**: When `session_id` is omitted (`session_id=None`), `_call_autoplay_turn` deliberately falls back to the newest session in the active workspace. This is the intended behavior for general "speak the latest turn" queries without session specificity.
- **Both Arguments Specified**: If a caller simultaneously provides both `transcript_path` and `session_id`, placing the check inside the `else:` branch treats `transcript_path` as an explicit path override. If desired, a check can also verify that `transcript_path.name == f"{session_id}.jsonl"` if both are present, but in practice MCP callers use one or the other.
- **Empty / Whitespace Session IDs**: If a caller supplies `session_id=""` or `"   "`, an upfront parameter check `not session_id.strip()` should reject it with `"session_id must be a non-empty string"`.

---

## 4. Conclusion

The cross-session isolation defect is fully understood, empirically reproducible, and bounded.

### Exact Implementation Requirements:
1. **Modify `plugin/scripts/python/mcp_server.py`**:
   - In `_call_autoplay_turn`, add validation for empty/whitespace string:
     ```python
     if session_id is not None and not session_id.strip():
         return _ok(
             req_id, _tool_result("session_id must be a non-empty string", is_error=True)
         )
     ```
   - In `_call_autoplay_turn`, immediately following `t_path = locator.locate(cwd=Path.cwd(), session_id=session_id)`:
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
2. **Add Unit Tests in `tests/test_mcp_server.py`**:
   - `test_autoplay_turn_nonexistent_session_id_returns_tool_error`: Verifies that a mocked locator returning a fallback file results in `isError: True` with the exact message and zero audio queued.
   - `test_autoplay_turn_nonexistent_session_id_with_real_locator_returns_tool_error`: End-to-end integration test with real on-disk directory structure proving that fallback to `newest-jsonl` is caught and returned as a tool error.
   - `test_autoplay_turn_matching_session_id_succeeds`: Verifies that a valid matching session succeeds, returns `isError: False`, and queues audio.
   - `test_autoplay_turn_invalid_session_id_validation`: Verifies rejection of non-string and empty string `session_id` arguments.
   - Register all 4 tests in the `tests = [...]` runner array in `main()`.

---

## 5. Verification Method

To independently verify the fix after implementation:

1. **Direct Empirical Reproduction Test**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   PYTHONPATH=plugin/scripts/python .venv/bin/python -c '
   from mcp_server import McpServer
   import json
   server = McpServer()
   res = server.handle_line(json.dumps({
       "jsonrpc": "2.0", "id": 1, "method": "tools/call",
       "params": {"name": "autoplay_turn", "arguments": {"session_id": "nonexistent-session-xyz"}}
   }))
   assert res["result"]["isError"] is True, f"Expected isError: True, got {res}"
   assert "session transcript not found for session_id: nonexistent-session-xyz" in res["result"]["content"][0]["text"]
   print("PASSED: nonexistent session_id correctly returned tool error")
   '
   ```
2. **Run MCP Server Unit Tests**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/python tests/test_mcp_server.py
   ```
   Must pass 56 tests (52 baseline + 4 new tests).
3. **Run Full Test Suite**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   bash tests/run_all.sh
   ```
   Must report `ran: 50, failed: 0, all tests passed`.
4. **Code Quality & Lint**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/ruff check plugin/scripts/python/mcp_server.py tests/test_mcp_server.py
   ```
   Must report `All checks passed!`.

**Invalidation Conditions**:
- If `res["result"]["isError"]` is `False` when `session_id` does not match the resolved file.
- If `test_transcript_locator.py` fails due to changes in `TranscriptLocator`.
- If calling `autoplay_turn` without `session_id` errors out instead of resolving the newest session.
