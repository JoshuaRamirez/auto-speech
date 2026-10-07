# Empirical Challenge Handoff: MCP Lifecycle Parity (M2)

**Verdict**: **REQUEST_CHANGES**

---

## 1. Observation

Adversarial stress testing was conducted against `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/mcp_server.py` using Python 3.12.13 and JSON-RPC 2.0 requests over stdio and direct API invocations.

### Test 1: `playback_control` with invalid action (`action="jump"`)
- **JSON-RPC Request**:
  ```json
  {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "playback_control", "arguments": {"action": "jump"}}}
  ```
- **Observed Response**:
  ```json
  {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
      "content": [
        {
          "type": "text",
          "text": "unknown action: 'jump' (expected pause, resume, stop, restart, seek, replay)"
        }
      ],
      "isError": true
    }
  }
  ```
- **File / Lines**: `plugin/scripts/python/mcp_server.py:765-780`.
- **Status**: **PASS**. Returns tool error with defensive diagnostics.

### Test 2: `playback_control` seek without target
- **JSON-RPC Requests**:
  - `{"action": "seek"}`
  - `{"action": "seek", "target": ""}`
  - `{"action": "seek", "target": "   "}`
- **Observed Response**:
  ```json
  {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
      "content": [
        {
          "type": "text",
          "text": "seek requires a target (+N, -N, N, or 'end')"
        }
      ],
      "isError": true
    }
  }
  ```
- **File / Lines**: `plugin/scripts/python/mcp_server.py:856-863`.
- **Status**: **PASS**. Defensive rejection with clear guidance.

### Test 3: `playback_control` seek when mpv socket not alive
- **Condition 3A (Socket file does not exist)**:
  - Request: `{"action": "seek", "target": "+10"}`
  - Response:
    ```json
    {
      "jsonrpc": "2.0",
      "id": 1,
      "result": {
        "content": [
          {"type": "text", "text": "No active playback session."}
        ],
        "isError": true
      }
    }
    ```
- **Condition 3B (Stale socket file on disk, connection refused)**:
  - Response:
    ```json
    {
      "jsonrpc": "2.0",
      "id": 1,
      "result": {
        "content": [
          {"type": "text", "text": "mpv IPC failure: mpv socket unavailable at ...: [Errno 61] Connection refused"}
        ],
        "isError": true
      }
    }
    ```
- **File / Lines**: `plugin/scripts/python/mcp_server.py:907-913`.
- **Status**: **PASS**. Fails cleanly with tool error; no unhandled exceptions or crashes.

### Test 4: `playback_control` replay when cache is empty / out of bounds
- **Condition 4A (Empty cache store)**:
  - Request: `{"action": "replay", "ordinal": 1}`
  - Response:
    ```json
    {
      "jsonrpc": "2.0",
      "id": 1,
      "result": {
        "content": [
          {"type": "text", "text": "No cached audio available for replay (ordinal=1)."}
        ],
        "isError": true
      }
    }
    ```
- **Condition 4B (Ordinal exceeds cache count, e.g. ordinal=5 with 1 entry)**:
  - Response: `No cached audio available for replay (ordinal=5).`, `isError: true`.
- **Condition 4C (Invalid ordinal values: -1, 0, "1", 1.5)**:
  - Response: `ordinal must be an integer >= 1, got ...`, `isError: true`.
- **File / Lines**: `plugin/scripts/python/mcp_server.py:935-953`.
- **Status**: **PASS**.

### Test 5: `session_configure` with invalid action / subsystem
- **Sub-test 5A (`autoplay` / `narrate` / `scope` / `mode` / `size` direct invalid inputs)**:
  - `{"autoplay": "bogus"}` -> `invalid autoplay action: 'bogus' (expected on, off, status)` (`isError: true`).
  - `{"narrate": "bogus"}` -> `invalid narrate action: 'bogus' (expected on, off, status)` (`isError: true`).
  - `{"scope": "bogus"}` -> `invalid scope action: 'bogus' (expected all, solo, status)` (`isError: true`).
  - `{"mode": "bogus"}` -> `invalid mode: 'bogus' (expected summary, verbatim, raw)` (`isError: true`).
  - `{"size": "bogus"}` -> `invalid size: 'bogus' (expected small, medium, large)` (`isError: true`).
  - `{"subsystem": "autoplay", "action": "bogus"}` -> `invalid autoplay action: 'bogus'` (`isError: true`).
  - `{"subsystem": "narrate", "action": "bogus"}` -> `invalid narrate action: 'bogus'` (`isError: true`).
- **Sub-test 5B (DEFECT 1: Unrecognized `subsystem` or unmapped `action` fails to reject)**:
  - Request 1: `{"subsystem": "bogus", "action": "on"}`
  - Response 1:
    ```json
    {
      "jsonrpc": "2.0",
      "id": 1,
      "result": {
        "content": [
          {
            "type": "text",
            "text": "autoplay: OFF — no session id to enroll\nautoplay scope: ALL — every enrolled session reads (default)\nmode=summary summary_size=small"
          }
        ],
        "isError": false
      }
    }
    ```
  - Request 2: `{"subsystem": "bogus"}` -> `isError: false` (Returns status report text).
  - Request 3: `{"action": "bogus"}` -> `isError: false` (Returns status report text).
  - Request 4: `{"subsystem": "bogus", "action": "bogus"}` -> `isError: false` (Returns status report text).
  - **File / Lines**: `plugin/scripts/python/mcp_server.py:986-997` and `1157-1163`.
  - **Finding**: When an invalid `subsystem` or unmapped `action` is passed, `mcp_server.py` does NOT validate `subsystem`. It falls through to the "no args" fallback, returning `isError: false` and pretending the command succeeded by printing system status.
- **Sub-test 5C (DEFECT 2: Collision on documented `subsystem="autoplay"` with `action="mode"` or `"scope"`)**:
  - Request 1: `{"subsystem": "autoplay", "action": "mode", "value": "raw"}`
  - Observed Response 1:
    ```json
    {
      "jsonrpc": "2.0",
      "id": 1,
      "result": {
        "content": [
          {
            "type": "text",
            "text": "invalid autoplay action: 'mode' (expected on, off, status)"
          }
        ],
        "isError": true
      }
    }
    ```
  - Request 2: `{"subsystem": "autoplay", "action": "scope", "value": "solo", "session_id": "test-1"}`
  - Observed Response 2:
    ```json
    {
      "jsonrpc": "2.0",
      "id": 2,
      "result": {
        "content": [
          {
            "type": "text",
            "text": "invalid autoplay action: 'scope' (expected on, off, status)"
          }
        ],
        "isError": true
      }
    }
    ```
  - **File / Lines**: `plugin/scripts/python/mcp_server.py:986-997`:
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
  - **Finding**: In lines 989-990, `if subsystem == "autoplay" and action:` assigns `args["autoplay"] = action`. When `action` is `"mode"` or `"scope"` (which are legitimate subcommands under `autoplay` per PROJECT.md §Interface Contracts), `args["autoplay"]` receives `"mode"` or `"scope"`. Subsequently, line 1002 evaluates `if "autoplay" in args:`, sees `ap_act not in ("on", "off", "status")`, and erroneously returns an error. This prevents agents from configuring autoplay mode or scope using the `subsystem`/`action` interface.
- **Status**: **FAIL**.

### Test 6: `doctor` with `as_json=True`
- **JSON-RPC Request**: `{"name": "doctor", "arguments": {"as_json": true}}` (and `{"json_format": true}`)
- **Observed Response**: `isError: false`
- **Parsed JSON Content**:
  - Keys: `['healthy', 'checks']`
  - `healthy`: `True`
  - `checks`: 13 probes (`mpv`, `uv`, `jq`, `venv`, `model_cache`, `sound_effects`, `headroom`, `permissions`, `cache_size`, `log_sizes`, `daemon`, `queue_depths`, `scopes`) matching `HealthReport` dataclass.
- **Status**: **PASS**. Parseable JSON adhering strictly to `HealthReport` schema.

### Test 7: `doctor` with `as_json=False`
- **JSON-RPC Request**: `{"name": "doctor", "arguments": {"as_json": false}}`
- **Observed Response**: `isError: false`
- **Text Content**:
  ```
  auto-speech doctor — HEALTHY
    [OK  ] mpv: found (playback available)
    [OK  ] uv: found
    [OK  ] jq: found (session id parsing)
    [OK  ] venv: python present at /Users/joshua/Developer/auto-speech-worktree/.venv/bin/python
    ...
  ```
- **Status**: **PASS**. Formatted diagnostic text.

### Test 8: Full regression suite
- **Command**: `cd /Users/joshua/Developer/auto-speech-worktree && bash tests/run_all.sh`
- **Result**:
  ```
  ran:    50
  failed: 0
  all tests passed
  ```
- **Status**: **PASS**. All 50 existing test suites pass.

---

## 2. Logic Chain

1. **Premise**: Per PROJECT.md §Interface Contracts line 104 and ORIGINAL_REQUEST.md §R2, `session_configure` must provide complete programmatic lifecycle control and defensively reject invalid arguments. The dispatch instructions explicitly state: *"session_configure with invalid action / subsystem -> verify defensive rejection."*
2. **Observation**: Passing `subsystem="bogus"` or `action="bogus"` without a known subsystem in `session_configure` returns `isError: false` with a status report instead of an error message.
3. **Inference**: Missing validation of the `subsystem` parameter and lack of check for unrouted `action` allows illegal inputs to silently succeed (silent fake success violation).
4. **Observation**: Calling `session_configure` with `subsystem="autoplay"`, `action="mode"`, `value="raw"` (or `action="scope"`) sets `args["autoplay"] = "mode"` in line 990, which is immediately rejected by line 1004 (`invalid autoplay action: 'mode'`).
5. **Inference**: Valid, documented alternative dispatch invocations for `mode` and `scope` are broken due to premature assignment of `action` to `args["autoplay"]`.
6. **Conclusion**: `playback_control`, `doctor`, and the regression suite are robust, but `session_configure` requires remediation before Milestone 2 can be approved.

---

## 3. Caveats

- Real macOS audio output hardware was not asserted aloud to prevent acoustic disturbance; audio pipe execution was verified via `DaemonClient` priority queuing, `MpvIpc` mock sockets, and unit test harnesses.
- Other tools (`speak`, `autoplay_turn`, `narrate_event`) were not part of the primary adversarial scope for this check, though their baseline regression tests passed 50/50.

---

## 4. Conclusion

**Verdict: REQUEST_CHANGES**

The worker must apply the following targeted fixes to `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/mcp_server.py` in `_call_session_configure`:

1. **Defensive parameter validation for `subsystem`**:
   ```python
   if subsystem is not None and subsystem not in ("autoplay", "narrate"):
       return _ok(
           req_id,
           _tool_result(
               f"invalid subsystem: {subsystem!r} (expected autoplay, narrate)",
               is_error=True,
           ),
       )
   ```

2. **Fix alternative dispatch routing for `action`**:
   Ensure `action="mode"` and `action="scope"` route to `mode` and `scope` without polluting `args["autoplay"]`:
   ```python
   if subsystem == "autoplay":
       if action in ("on", "off", "status"):
           args.setdefault("autoplay", action)
       elif action == "mode":
           if not value:
               return _ok(req_id, _tool_result("action 'mode' requires 'value'", is_error=True))
           args.setdefault("mode", value)
       elif action == "scope":
           if not value:
               return _ok(req_id, _tool_result("action 'scope' requires 'value'", is_error=True))
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
       if action == "mode" and value:
           args.setdefault("mode", value)
       elif action == "scope" and value:
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

3. **Add unit test cases in `tests/test_mcp_server.py`** covering:
   - `subsystem="bogus"` -> `isError: true`
   - `subsystem="autoplay", action="mode", value="raw"` -> `isError: false`
   - `subsystem="autoplay", action="scope", value="solo", session_id="s1"` -> `isError: false`
   - `subsystem="bogus", action="on"` -> `isError: true`

---

## 5. Verification Method

To verify the defects:
```bash
cd /Users/joshua/Developer/auto-speech-worktree
.venv/bin/python -c '
import json, tempfile
from pathlib import Path
import sys
sys.path.insert(0, "plugin/scripts/python")
from mcp_server import McpServer

srv = McpServer(home=Path(tempfile.mkdtemp()))

# Defect 1: Should be isError=True, currently returns isError=False
r1 = srv.handle_line(json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
    "params": {"name": "session_configure", "arguments": {"subsystem": "bogus", "action": "on"}}
}))
print("Defect 1 (invalid subsystem): isError =", r1["result"]["isError"])

# Defect 2: Should be isError=False, currently returns isError=True
r2 = srv.handle_line(json.dumps({
    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
    "params": {"name": "session_configure", "arguments": {"subsystem": "autoplay", "action": "mode", "value": "raw"}}
}))
print("Defect 2 (autoplay mode): isError =", r2["result"]["isError"], r2["result"]["content"][0]["text"])
'
```

Expected after fix:
- Defect 1 output: `isError = True`
- Defect 2 output: `isError = False`
- Full regression suite passes: `bash tests/run_all.sh`
