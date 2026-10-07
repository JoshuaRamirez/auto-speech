# Handoff Report: Investigation and Remediation Strategy for `session_configure` (M2)

**Agent**: `explorer_m2_retry_2`  
**Milestone**: M2  
**Target Repository / Worktree**: `/Users/joshua/Developer/auto-speech-worktree`  
**Target File**: `plugin/scripts/python/mcp_server.py` (`_call_session_configure`, lines 980–997)  
**Test Suite File**: `tests/test_mcp_server.py`  
**Analysis File**: `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_retry_2/analysis.md`  

---

## 1. Observation

Adversarial testing and empirical reproduction against `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/mcp_server.py` revealed two distinct flaws in `_call_session_configure`:

### 1.1 Defect 1: Silent Fake Success on Invalid `subsystem` or Unmapped `action`
- **Current Code (`mcp_server.py:986-997`)**:
  ```python
  subsystem = args.get("subsystem")
  action = args.get("action")
  value = args.get("value")
  if subsystem == "autoplay" and action:
      args.setdefault("autoplay", action)
  elif subsystem == "narrate" and action:
      args.setdefault("narrate", action)
  if action == "mode" and value:
      args.setdefault("mode", value)
  elif action == "scope" and value:
      args.setdefault("scope", value)
  ```
- **Reproduction Command**:
  ```bash
  .venv/bin/python -c '
  import json, tempfile, sys
  from pathlib import Path
  sys.path.insert(0, "plugin/scripts/python")
  from mcp_server import McpServer

  srv = McpServer(home=Path(tempfile.mkdtemp()))
  r = srv.handle_line(json.dumps({
      "jsonrpc": "2.0", "id": 1, "method": "tools/call",
      "params": {"name": "session_configure", "arguments": {"subsystem": "bogus", "action": "on"}}
  }))
  print("isError =", r["result"]["isError"], "text =", r["result"]["content"][0]["text"][:60])
  '
  ```
- **Observed Result**:
  ```
  isError = False text = autoplay: OFF — no session id to enroll
  autoplay scope: ALL 
  ```
- **Finding**: Calling `session_configure` with `subsystem="bogus"` bypasses both `if subsystem == "autoplay"` and `elif subsystem == "narrate"`. No keys are added to `args`, `results` remains `[]`, and execution falls through to lines 1157–1163 (the fallback status summary), returning `isError: False`. The tool returns a silent fake success for illegal arguments.

### 1.2 Defect 2: Parameter Collision on Documented `subsystem="autoplay"` with `action="mode"` or `"scope"`
- **Current Code (`mcp_server.py:989-990`)**:
  ```python
  if subsystem == "autoplay" and action:
      args.setdefault("autoplay", action)
  ```
- **Reproduction Command**:
  ```bash
  .venv/bin/python -c '
  import json, tempfile, sys
  from pathlib import Path
  sys.path.insert(0, "plugin/scripts/python")
  from mcp_server import McpServer

  srv = McpServer(home=Path(tempfile.mkdtemp()))
  r = srv.handle_line(json.dumps({
      "jsonrpc": "2.0", "id": 2, "method": "tools/call",
      "params": {"name": "session_configure", "arguments": {"subsystem": "autoplay", "action": "mode", "value": "raw"}}
  }))
  print("isError =", r["result"]["isError"], "text =", r["result"]["content"][0]["text"])
  '
  ```
- **Observed Result**:
  ```
  isError = True text = invalid autoplay action: 'mode' (expected on, off, status)
  ```
- **Finding**: When `subsystem="autoplay"` and `action="mode"`, `args.setdefault("autoplay", "mode")` sets `args["autoplay"] = "mode"`. At line 1003, `ap_act not in ("on", "off", "status")` triggers, rejecting the call with `invalid autoplay action: 'mode'`. The same defect occurs when `action="scope"`. Valid calls per `PROJECT.md` §Interface Contracts are rejected.

### 1.3 Baseline Test Suite Status
- **Command**: `cd /Users/joshua/Developer/auto-speech-worktree && bash tests/run_all.sh`
- **Observed Result**:
  ```
  ran:    50
  failed: 0
  all tests passed
  ```
- **Finding**: The baseline worktree code is clean and passes all 50 existing test suites.

---

## 2. Logic Chain

1. **Requirement Premise**: Per `PROJECT.md` line 104 and `ORIGINAL_REQUEST.md` §R2, `session_configure` accepts `{"subsystem": "autoplay"|"narrate", "action": "on"|"off"|"mode"|"scope", "session_id": string (optional), "value": string (optional)}`. Defensive engineering principles strictly forbid silent fake successes when invalid arguments are provided.
2. **Defect 1 Root Cause**: Lines 986–997 do not validate that `subsystem` is an allowed member of `("autoplay", "narrate")`, nor do they validate that an unadorned `action` belongs to a known subsystem. Because Section 5 treats empty `results` as an empty query requesting a status dump, illegal inputs fall through and print the status summary with `isError: False`.
3. **Defect 1 Invalidation**: Enforcing `if subsystem is not None and subsystem not in ("autoplay", "narrate"): return _ok(req_id, _tool_result(..., is_error=True))` guarantees that invalid subsystems are rejected immediately before any fall-through.
4. **Defect 2 Root Cause**: `if subsystem == "autoplay" and action:` unconditionally assigns `args["autoplay"] = action`. But under `autoplay`, valid actions include `("on", "off", "status", "mode", "scope")`. Only `("on", "off", "status")` are enrollment actions belonging to `args["autoplay"]`. `action == "mode"` and `action == "scope"` are configuration commands belonging to `args["mode"]` and `args["scope"]`. Storing `"mode"` or `"scope"` into `args["autoplay"]` corrupts the dictionary and trips the `autoplay` enrollment validator.
5. **Defect 2 Invalidation**: Segregating routing so that:
   - `action in ("on", "off", "status")` sets `args["autoplay"]`
   - `action == "mode"` validates that `value` is present, then sets `args["mode"]` (mapping `"small"`, `"medium"`, `"large"` to `mode="summary", size=value`), without touching `args["autoplay"]`
   - `action == "scope"` validates that `value` is present, then sets `args["scope"]`, without touching `args["autoplay"]`
   - Unrecognized actions return `is_error=True` with descriptive diagnostics
   completely eliminates parameter pollution and allows all valid alternative dispatch calls to succeed.
6. **Backward Compatibility**: Calls using direct arguments (`{"autoplay": "on"}`, `{"scope": "solo"}`, `{"mode": "summary"}`) do not specify `subsystem` or `action`, so they bypass the alternative dispatch block and execute their dedicated blocks unchanged. Calls passing `{}` (empty dict) fall through to Section 5, maintaining status reporting functionality.

---

## 3. Caveats

- **No Caveats**: The issue is fully isolated to `_call_session_configure` in `plugin/scripts/python/mcp_server.py`. The proposed changes are purely localized to argument parsing and validation. No audio synthesis, daemon IPC, or socket transport logic is affected.

---

## 4. Conclusion

The implementer must apply the following remediation to `/Users/joshua/Developer/auto-speech-worktree`:

### 4.1 Production Code Edit: `plugin/scripts/python/mcp_server.py`
Replace lines 986–997 with:

```python
        subsystem = args.get("subsystem")
        action = args.get("action")
        value = args.get("value")

        if subsystem is not None and subsystem not in ("autoplay", "narrate"):
            return _ok(
                req_id,
                _tool_result(
                    f"invalid subsystem: {subsystem!r} (expected autoplay, narrate)",
                    is_error=True,
                ),
            )

        if subsystem == "autoplay":
            if action in ("on", "off", "status"):
                args.setdefault("autoplay", action)
            elif action == "mode":
                if not value or not str(value).strip():
                    return _ok(
                        req_id,
                        _tool_result("action 'mode' requires 'value'", is_error=True),
                    )
                if value in ("small", "medium", "large"):
                    args.setdefault("mode", "summary")
                    args.setdefault("size", value)
                else:
                    args.setdefault("mode", value)
            elif action == "scope":
                if not value or not str(value).strip():
                    return _ok(
                        req_id,
                        _tool_result("action 'scope' requires 'value'", is_error=True),
                    )
                args.setdefault("scope", value)
            elif action is not None:
                return _ok(
                    req_id,
                    _tool_result(
                        f"invalid autoplay action: {action!r} (expected on, off, status, mode, scope)",
                        is_error=True,
                    ),
                )
        elif subsystem == "narrate":
            if action in ("on", "off", "status"):
                args.setdefault("narrate", action)
            elif action is not None:
                return _ok(
                    req_id,
                    _tool_result(
                        f"invalid narrate action: {action!r} (expected on, off, status)",
                        is_error=True,
                    ),
                )
        elif subsystem is None and action is not None:
            if action == "mode":
                if not value or not str(value).strip():
                    return _ok(
                        req_id,
                        _tool_result("action 'mode' requires 'value'", is_error=True),
                    )
                if value in ("small", "medium", "large"):
                    args.setdefault("mode", "summary")
                    args.setdefault("size", value)
                else:
                    args.setdefault("mode", value)
            elif action == "scope":
                if not value or not str(value).strip():
                    return _ok(
                        req_id,
                        _tool_result("action 'scope' requires 'value'", is_error=True),
                    )
                args.setdefault("scope", value)
            else:
                return _ok(
                    req_id,
                    _tool_result(
                        f"unhandled action {action!r} without valid subsystem",
                        is_error=True,
                    ),
                )
```

### 4.2 Test Additions: `tests/test_mcp_server.py`
Add the following unit test functions under Group 6:

```python
def test_session_configure_subsystem_and_action_validation() -> None:
    server, _ = _server()

    # Invalid subsystem
    res = _call_tool(server, "session_configure", {"subsystem": "bogus"})
    assert res["result"]["isError"] is True
    assert "invalid subsystem: 'bogus'" in res["result"]["content"][0]["text"]

    res = _call_tool(server, "session_configure", {"subsystem": "bogus", "action": "on"})
    assert res["result"]["isError"] is True
    assert "invalid subsystem: 'bogus'" in res["result"]["content"][0]["text"]

    # Unhandled action without subsystem
    res = _call_tool(server, "session_configure", {"action": "bogus"})
    assert res["result"]["isError"] is True
    assert "unhandled action 'bogus' without valid subsystem" in res["result"]["content"][0]["text"]

    res = _call_tool(server, "session_configure", {"action": "on", "session_id": "sess-test"})
    assert res["result"]["isError"] is True
    assert "unhandled action 'on' without valid subsystem" in res["result"]["content"][0]["text"]

    # Invalid action under subsystem
    res = _call_tool(server, "session_configure", {"subsystem": "autoplay", "action": "bogus"})
    assert res["result"]["isError"] is True
    assert "invalid autoplay action: 'bogus'" in res["result"]["content"][0]["text"]

    res = _call_tool(server, "session_configure", {"subsystem": "narrate", "action": "bogus"})
    assert res["result"]["isError"] is True
    assert "invalid narrate action: 'bogus'" in res["result"]["content"][0]["text"]

    res = _call_tool(server, "session_configure", {"subsystem": "narrate", "action": "mode"})
    assert res["result"]["isError"] is True
    assert "invalid narrate action: 'mode'" in res["result"]["content"][0]["text"]

    # Mode / scope missing value
    res = _call_tool(server, "session_configure", {"subsystem": "autoplay", "action": "mode"})
    assert res["result"]["isError"] is True
    assert "action 'mode' requires 'value'" in res["result"]["content"][0]["text"]

    res = _call_tool(server, "session_configure", {"subsystem": "autoplay", "action": "scope"})
    assert res["result"]["isError"] is True
    assert "action 'scope' requires 'value'" in res["result"]["content"][0]["text"]

    res = _call_tool(server, "session_configure", {"action": "mode"})
    assert res["result"]["isError"] is True
    assert "action 'mode' requires 'value'" in res["result"]["content"][0]["text"]

    res = _call_tool(server, "session_configure", {"action": "scope"})
    assert res["result"]["isError"] is True
    assert "action 'scope' requires 'value'" in res["result"]["content"][0]["text"]


def test_session_configure_alternative_dispatch_routing() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-cfg-alt-"))
    server = McpServer(home=tmp)

    # 1. Autoplay on via alternative dispatch
    res = _call_tool(
        server,
        "session_configure",
        {"subsystem": "autoplay", "action": "on", "session_id": "sess-alt-1"},
    )
    assert res["result"]["isError"] is False
    assert (tmp / ".claude" / "auto-speech-autoplay-enabled" / "sess-alt-1").exists()

    # 2. Autoplay mode via alternative dispatch (mode="raw")
    res = _call_tool(
        server,
        "session_configure",
        {"subsystem": "autoplay", "action": "mode", "value": "raw"},
    )
    assert res["result"]["isError"] is False
    cfg_file = tmp / ".config" / "auto-speech" / "autoplay.toml"
    assert cfg_file.is_file()
    assert 'mode = "raw"' in cfg_file.read_text(encoding="utf-8")

    # 3. Autoplay scope via alternative dispatch (scope="solo")
    res = _call_tool(
        server,
        "session_configure",
        {"subsystem": "autoplay", "action": "scope", "value": "solo", "session_id": "sess-alt-1"},
    )
    assert res["result"]["isError"] is False
    assert SoloScope(home=tmp).current() == "sess-alt-1"

    # 4. Autoplay scope via alternative dispatch (scope="all")
    res = _call_tool(
        server,
        "session_configure",
        {"subsystem": "autoplay", "action": "scope", "value": "all"},
    )
    assert res["result"]["isError"] is False
    assert SoloScope(home=tmp).current() is None

    # 5. Narrate on via alternative dispatch
    res = _call_tool(
        server,
        "session_configure",
        {"subsystem": "narrate", "action": "on", "session_id": "sess-alt-2"},
    )
    assert res["result"]["isError"] is False
    assert (tmp / ".claude" / "auto-speech-narrate-sessions" / "sess-alt-2").exists()

    # 6. Standalone action mode and scope
    res = _call_tool(
        server,
        "session_configure",
        {"action": "mode", "value": "verbatim"},
    )
    assert res["result"]["isError"] is False
    assert 'mode = "verbatim"' in cfg_file.read_text(encoding="utf-8")

    res = _call_tool(
        server,
        "session_configure",
        {"action": "scope", "value": "solo", "session_id": "sess-alt-3"},
    )
    assert res["result"]["isError"] is False
    assert SoloScope(home=tmp).current() == "sess-alt-3"
```

And register both in `tests/test_mcp_server.py:main()` under Group 6:
```python
        test_session_configure_subsystem_and_action_validation,
        test_session_configure_alternative_dispatch_routing,
```

---

## 5. Verification Method

To independently verify the fix:
1. Apply the replacement block to `plugin/scripts/python/mcp_server.py`.
2. Execute the verification script:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/python -c '
   import json, tempfile, sys
   from pathlib import Path
   sys.path.insert(0, "plugin/scripts/python")
   from mcp_server import McpServer

   srv = McpServer(home=Path(tempfile.mkdtemp()))

   # Check Defect 1: Should be isError=True
   r1 = srv.handle_line(json.dumps({
       "jsonrpc": "2.0", "id": 1, "method": "tools/call",
       "params": {"name": "session_configure", "arguments": {"subsystem": "bogus", "action": "on"}}
   }))
   assert r1["result"]["isError"] is True, f"Expected isError=True, got {r1}"

   # Check Defect 2: Should be isError=False
   r2 = srv.handle_line(json.dumps({
       "jsonrpc": "2.0", "id": 2, "method": "tools/call",
       "params": {"name": "session_configure", "arguments": {"subsystem": "autoplay", "action": "mode", "value": "raw"}}
   }))
   assert r2["result"]["isError"] is False, f"Expected isError=False, got {r2}"
   print("Independent verification PASSED: Both defects resolved.")
   '
   ```
3. Run the complete test suite:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree && bash tests/run_all.sh
   ```
   Must report `failed: 0, all tests passed` across all suites (50+).
