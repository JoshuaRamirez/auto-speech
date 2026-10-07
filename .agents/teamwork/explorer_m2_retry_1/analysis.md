# Technical Analysis: Cross-Session Isolation Defect in `_call_autoplay_turn`

## 1. Executive Summary

In Milestone M2, adversarial challenge testing (`challenger_m2_1`) identified a critical cross-session privacy and isolation defect in `mcp_server.py` (`_call_autoplay_turn`):
When an MCP tool caller passes an explicit `session_id` that does not exist, `TranscriptLocator.locate()` falls back to `newest-jsonl` in the workspace's Claude project directory. As a result, `_call_autoplay_turn` returns `isError: False` and synthesizes audio containing conversational content from an unrelated session, rather than returning a tool execution error.

This document presents the forensic analysis of the defect, evaluates architectural trade-offs, and specifies the exact code modifications and unit test suite required to enforce strict session isolation.

---

## 2. Forensic Analysis & Root Cause

### 2.1 The Code Path in `_call_autoplay_turn`
In `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/mcp_server.py`:
```python
516:     def _call_autoplay_turn(self, req_id, args: dict) -> dict:
517:         session_id = args.get("session_id")
...
568:         else:
569:             locator = self._locator or TranscriptLocator()
570:             try:
571:                 t_path = locator.locate(cwd=Path.cwd(), session_id=session_id)
572:             except Exception as exc:
573:                 return _ok(
574:                     req_id,
575:                     _tool_result(f"no transcript found for session: {exc}", is_error=True),
576:                 )
```

### 2.2 The Fallback Mechanism in `TranscriptLocator`
In `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/transcript_locator.py` lines 105–140:
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
                print(
                    f"[locator] session_id={sid} but candidate missing; falling back to newest-jsonl",
                    file=sys.stderr,
                )

        if slug_dir is None:
            raise TranscriptNotFoundError(...)

        candidates = sorted(slug_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            raise TranscriptNotFoundError(f"no .jsonl files in {slug_dir}")
        newest = candidates[0]
        return newest
```

### 2.3 Why the Defect Occurs
1. `TranscriptLocator` was originally engineered for shell hooks (`autoplay_hook.sh`) where a hook might be triggered before Claude Code created or flushed the new session JSONL to disk. In that environment, falling back to the newest file was a recovery mechanism to prevent silent silence.
2. In the MCP server surface, however, `autoplay_turn` is invoked by AI agents (e.g. Claude Code or Antigravity) with explicit targeting parameters (`session_id`).
3. If an agent supplies `session_id="nonexistent-xyz"`:
   - In an empty workspace with no project directory, `locate()` raises `TranscriptNotFoundError`, which lines 572–576 catch and return as `isError: True`.
   - But in any real workspace where previous sessions exist in `~/.claude/projects/<slug>/`, `slug_dir` is found. `candidate.exists()` is False. The locator logs a fallback notice to stderr and returns `newest` (`<prior_session_uuid>.jsonl`).
4. `_call_autoplay_turn` never checks whether the returned `t_path` actually corresponds to the requested `session_id`. It proceeds directly to `MessageSelector().select(t_path)`, extracts text from the prior session, synthesizes audio, and returns `isError: False`.

### 2.4 Empirical Reproduction
Verified empirically in the isolated worktree (`/Users/joshua/Developer/auto-speech-worktree`):
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
Output:
```
[locator] slug_dir=/Users/joshua/.claude/projects/-Users-joshua-Developer-auto-speech-worktree
[locator] session_id=nonexistent-session-xyz but candidate missing; falling back to newest-jsonl
[locator] newest jsonl /Users/joshua/.claude/projects/-Users-joshua-Developer-auto-speech-worktree/a5dd287b-9cf4-4033-99d3-f111895e23a7.jsonl
isError: False
Content: Queued autoplay turn (604 chars, mode=summary, hash=bd61657a0ab59a8b).
```

---

## 3. Architectural Design Options

### Option A: Modify `TranscriptLocator` (Rejected)
- Change `TranscriptLocator.locate()` to raise `TranscriptNotFoundError` whenever `session_id` is supplied but not found.
- **Why Rejected**: `tests/test_transcript_locator.py` line 164 (`test_env_var_missing_session_uses_newest_jsonl`) explicitly asserts that `CLAUDE_SESSION_ID="missing-session"` falls back to `new.jsonl`. Modifying `TranscriptLocator` alters legacy hook semantics and breaks existing regression test invariants.

### Option B: Intercept and Validate in `_call_autoplay_turn` (Recommended)
- Enforce session identity verification at the MCP boundary in `plugin/scripts/python/mcp_server.py`.
- When `session_id` is passed in `args`:
  1. Validate that `session_id` is a non-empty string.
  2. After `locator.locate(...)`, verify that `t_path.name == f"{session_id}.jsonl"` or `t_path.stem == session_id`.
  3. If they do not match, immediately return:
     ```python
     return _ok(
         req_id,
         _tool_result(
             f"session transcript not found for session_id: {session_id}",
             is_error=True,
         ),
     )
     ```
- **Advantages**:
  - Preserves 100% backward compatibility for all existing tests in `test_transcript_locator.py`.
  - Guarantees complete cross-session isolation and privacy for MCP callers.
  - Zero side effects on legacy shell hooks or CLI invocations that don't pass through the MCP server.

---

## 4. Proposed Code Changes

### Target File: `plugin/scripts/python/mcp_server.py`

#### Edit 1: Parameter Validation (lines 517–520)
Add validation for empty or whitespace-only `session_id`:
```python
<<<<
        session_id = args.get("session_id")
        if session_id is not None and not isinstance(session_id, str):
            return _ok(req_id, _tool_result("session_id must be a string", is_error=True))
====
        session_id = args.get("session_id")
        if session_id is not None and not isinstance(session_id, str):
            return _ok(req_id, _tool_result("session_id must be a string", is_error=True))
        if session_id is not None and not session_id.strip():
            return _ok(
                req_id, _tool_result("session_id must be a non-empty string", is_error=True)
            )
>>>>
```

#### Edit 2: Session Identity Guard (lines 568–577)
Enforce that the located transcript matches the requested `session_id`:
```python
<<<<
        else:
            locator = self._locator or TranscriptLocator()
            try:
                t_path = locator.locate(cwd=Path.cwd(), session_id=session_id)
            except Exception as exc:
                return _ok(
                    req_id,
                    _tool_result(f"no transcript found for session: {exc}", is_error=True),
                )
====
        else:
            locator = self._locator or TranscriptLocator()
            try:
                t_path = locator.locate(cwd=Path.cwd(), session_id=session_id)
            except Exception as exc:
                return _ok(
                    req_id,
                    _tool_result(f"no transcript found for session: {exc}", is_error=True),
                )
            if session_id and (t_path.name != f"{session_id}.jsonl" and t_path.stem != session_id):
                return _ok(
                    req_id,
                    _tool_result(
                        f"session transcript not found for session_id: {session_id}",
                        is_error=True,
                    ),
                )
>>>>
```

---

## 5. Specification of Unit Tests

### Target File: `tests/test_mcp_server.py`

Four new test functions must be added under `# Group 3: autoplay_turn`:

### 5.1 Test 1: Non-Existent `session_id` Rejection (Mocked Locator)
```python
def test_autoplay_turn_nonexistent_session_id_returns_tool_error() -> None:
    """When session_id is explicitly passed and that session does not exist,
    the server must return a tool error rather than falling back to an unrelated transcript."""
    mock_locator = MagicMock()
    # Simulate TranscriptLocator falling back to another session's transcript
    mock_locator.locate.return_value = Path("/tmp/unrelated-session-uuid.jsonl")
    mock_client = MagicMock()

    server = McpServer(client=mock_client, locator=mock_locator)
    res = _call_tool(
        server,
        "autoplay_turn",
        {"session_id": "missing-session-12345"},
    )
    assert res["result"]["isError"] is True
    assert (
        res["result"]["content"][0]["text"]
        == "session transcript not found for session_id: missing-session-12345"
    )
    mock_client.speak.assert_not_called()
```

### 5.2 Test 2: Real Disk Fallback Interception (Real `TranscriptLocator`)
```python
def test_autoplay_turn_nonexistent_session_id_with_real_locator_returns_tool_error() -> None:
    """End-to-end locator test: ensure TranscriptLocator's newest-jsonl fallback
    does not cause _call_autoplay_turn to speak audio from an unrelated session."""
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-ap-sess-fallback-"))
    project_slug = "-test-workspace"
    slug_dir = tmp / ".claude" / "projects" / project_slug
    slug_dir.mkdir(parents=True, exist_ok=True)
    unrelated_file = slug_dir / "unrelated-prior-session.jsonl"
    unrelated_file.write_text('{"type": "message", "role": "assistant", "content": "secret content"}\n', encoding="utf-8")

    locator = TranscriptLocator()
    locator.PROJECTS_ROOT = tmp / ".claude" / "projects"

    mock_client = MagicMock()
    server = McpServer(client=mock_client, locator=locator, home=tmp)

    with patch("pathlib.Path.cwd", return_value=Path("/test/workspace")):
        res = _call_tool(
            server,
            "autoplay_turn",
            {"session_id": "nonexistent-target-session-999"},
        )
    assert res["result"]["isError"] is True
    assert (
        res["result"]["content"][0]["text"]
        == "session transcript not found for session_id: nonexistent-target-session-999"
    )
    mock_client.speak.assert_not_called()
```

### 5.3 Test 3: Matching `session_id` Resolution Success
```python
def test_autoplay_turn_matching_session_id_succeeds() -> None:
    """When session_id matches the resolved transcript, synthesis proceeds normally."""
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-ap-match-"))
    session_id = "target-session-abc"
    transcript = tmp / f"{session_id}.jsonl"
    transcript.write_text("dummy\n", encoding="utf-8")

    turn_text = "Here is the valid assistant message text that exceeds the twenty character minimum."
    mock_selector = MagicMock()
    mock_selector.select.return_value = AssistantMessage(
        turn_index=1,
        ordinal_from_end=1,
        timestamp="2026-10-06T12:00:00Z",
        text=turn_text,
    )

    mock_locator = MagicMock()
    mock_locator.locate.return_value = transcript

    mock_client = MagicMock()
    mock_client.speak.return_value = DaemonResponse(status="ok", action="speak")

    server = McpServer(
        client=mock_client,
        locator=mock_locator,
        selector=mock_selector,
        home=tmp,
    )

    res = _call_tool(
        server,
        "autoplay_turn",
        {"session_id": session_id, "mode": "raw"},
    )
    assert res["result"]["isError"] is False
    assert "Queued autoplay turn" in res["result"]["content"][0]["text"]
    mock_client.speak.assert_called_once()
    assert mock_client.speak.call_args[0][0] == turn_text
```

### 5.4 Test 4: `session_id` Type and Empty String Validation
```python
def test_autoplay_turn_invalid_session_id_validation() -> None:
    """Non-string and empty string session_id arguments are rejected with clear tool errors."""
    server, _ = _server()
    for bad_id in (12345, True, ["sess-1"], {"id": "sess-1"}):
        res = _call_tool(server, "autoplay_turn", {"session_id": bad_id})
        assert res["result"]["isError"] is True
        assert "session_id must be a string" in res["result"]["content"][0]["text"]

    for empty_id in ("", "   ", "\t\n"):
        res = _call_tool(server, "autoplay_turn", {"session_id": empty_id})
        assert res["result"]["isError"] is True
        assert "session_id must be a non-empty string" in res["result"]["content"][0]["text"]
```

### 5.5 Test Registration in `main()`
In `tests/test_mcp_server.py`, the `main()` function contains an explicit test array. These four tests must be added to the `tests = [...]` list:
```python
        # Group 3: autoplay_turn
        test_autoplay_turn_happy_path,
        test_autoplay_turn_honors_global_mute,
        test_autoplay_turn_missing_transcript_returns_tool_error,
        test_autoplay_turn_nonexistent_session_id_returns_tool_error,
        test_autoplay_turn_nonexistent_session_id_with_real_locator_returns_tool_error,
        test_autoplay_turn_matching_session_id_succeeds,
        test_autoplay_turn_invalid_session_id_validation,
        test_autoplay_turn_no_qualifying_turn_returns_tool_error,
```

---

## 6. Verification and Regression Invariants

- **Full Suite Integrity**: Executing `bash tests/run_all.sh` in the worktree must pass all 50+ test suites.
- **Ruff Compliance**: Executing `.venv/bin/ruff check plugin/scripts/python/mcp_server.py tests/test_mcp_server.py` must produce 0 lint violations.
- **Zero Impact on Existing Invariants**:
  - `test_transcript_locator.py` continues to pass without alterations.
  - `autoplay_hook.sh` and shell hooks retain their fallback mechanism.
  - Calling `autoplay_turn` without `session_id` continues to resolve the newest active transcript.
