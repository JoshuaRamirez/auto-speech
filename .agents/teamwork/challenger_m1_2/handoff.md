# Empirical Challenge Handoff Report: Challenger M1.2

## Verdict
**REQUEST_CHANGES**

---

## 1. Observation

Direct empirical tests were executed against `/Users/joshua/Developer/auto-speech-worktree/bin/auto-speech` and its test suite under worktree conditions.

### Test 1: Global Mute Enforcement
- **Command**:
  ```bash
  trap "rm -f ~/.claude/auto-speech.disabled" EXIT INT TERM
  touch ~/.claude/auto-speech.disabled
  /Users/joshua/Developer/auto-speech-worktree/bin/auto-speech speak "should be muted"
  ```
- **Observed Stderr**:
  ```
  auto-speech: globally muted (~/.claude/auto-speech.disabled present); speech suppressed.
  ```
- **Observed Exit Code**: `0`
- **Cleanup**: Marker `~/.claude/auto-speech.disabled` verified removed immediately after test.
- **Result**: **PASS**

### Test 2: Transport Controls Without Active Playback Session
- **Commands & Observations**:
  - `bin/auto-speech pause` -> Stderr: `control: no active playback session (unified daemon uses synchronous audio).` (Exit code: `2`)
  - `bin/auto-speech resume` -> Stderr: `control: no active playback session (unified daemon uses synchronous audio).` (Exit code: `2`)
  - `bin/auto-speech restart` -> Stderr: `control: no active playback session (unified daemon uses synchronous audio).` (Exit code: `2`)
  - `bin/auto-speech stop` -> Stderr: `control: no active playback session (unified daemon uses synchronous audio).` (Exit code: `2`)
  - `bin/auto-speech seek +5` -> Stderr: `control: no active playback session (unified daemon uses synchronous audio).` (Exit code: `2`)
- **Result**: **PASS**

### Test 3: Hook Simulation with Malformed / Non-Dict JSON Payloads
- **Tested Subcommands**: `bin/auto-speech hook stop` and `bin/auto-speech hook narrator`
- **Code Inspected**: `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/cli.py`
  - Lines 621–627 (`handle_hook(stop)`):
    ```python
    payload_raw = sys.stdin.read() if sys.stdin else ""
    try:
        payload = json.loads(payload_raw) if payload_raw.strip() else {}
    except Exception:
        payload = {}

    session_id = payload.get("session_id") or payload.get("conversationId") or ""
    ```
  - Lines 678–688 (`handle_hook(narrator)`):
    ```python
    payload_raw = sys.stdin.read() if sys.stdin else ""
    try:
        payload = json.loads(payload_raw) if payload_raw.strip() else {}
    except Exception:
        payload = {}

    session_id = (
        payload.get("session_id")
        or payload.get("conversationId")
        or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    )
    ```
- **Empirical Execution**:
  When feeding non-dict valid JSON (`[1, 2, 3]`, `42`, `"hello"`, `null`, `true`):
  1. `echo "[1, 2, 3]" | bin/auto-speech hook stop`:
     ```
     Traceback (most recent call last):
       File "/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/cli.py", line 1048, in <module>
         sys.exit(main())
                  ^^^^^^
       File "/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/cli.py", line 1034, in main
         return handle_hook(args)
                ^^^^^^^^^^^^^^^^^
       File "/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/cli.py", line 627, in handle_hook
         session_id = payload.get("session_id") or payload.get("conversationId") or ""
                      ^^^^^^^^^^^
     AttributeError: 'list' object has no attribute 'get'
     Exit Code: 1
     ```
  2. `echo "42" | bin/auto-speech hook stop`:
     ```
     AttributeError: 'int' object has no attribute 'get'
     Exit Code: 1
     ```
  3. `echo '"hello"' | bin/auto-speech hook stop`:
     ```
     AttributeError: 'str' object has no attribute 'get'
     Exit Code: 1
     ```
  4. `echo "null" | bin/auto-speech hook stop`:
     ```
     AttributeError: 'NoneType' object has no attribute 'get'
     Exit Code: 1
     ```
  5. `echo "[1, 2, 3]" | bin/auto-speech hook narrator`:
     ```
     AttributeError: 'list' object has no attribute 'get'
     Exit Code: 1
     ```
  6. `echo "42" | bin/auto-speech hook narrator`:
     ```
     AttributeError: 'int' object has no attribute 'get'
     Exit Code: 1
     ```
  7. `echo "null" | bin/auto-speech hook narrator`:
     ```
     AttributeError: 'NoneType' object has no attribute 'get'
     Exit Code: 1
     ```
- **Result**: **FAIL** (Direct violation of requirement: "Verify defensive handling (no unhandled tracebacks)").

### Test 4: Full Test Suite Execution
- **Command**: `cd /Users/joshua/Developer/auto-speech-worktree && bash tests/run_all.sh`
- **Output**:
  ```
  ran:    50
  failed: 0
  all tests passed
  ```
- **Result**: **PASS**

### Test 5: Symlink Trampoline Resolution
- Single symlink (`/tmp/.../auto-speech -> bin/auto-speech`): Exit code `0`, resolved correctly.
- Chained symlinks (3 hops): Exit code `0`, resolved correctly.
- **Result**: **PASS**

---

## 2. Logic Chain

1. **Premise 1**: Shell lifecycle hooks (such as Claude Code's `Stop`, `PreToolUse`, `PostToolUse` and Antigravity hooks) receive payloads via standard input and execute in non-interactive background environments.
2. **Premise 2**: Hook handlers must fail-safe (defensive handling without unhandled tracebacks or non-zero exit codes that break caller workflows when unexpected input is delivered).
3. **Observation Reference**: In `plugin/scripts/python/cli.py`, `json.loads(payload_raw)` successfully parses non-dict JSON formats (`list`, `int`, `str`, `bool`, `None`). Because the `except Exception:` block only catches parse errors, `payload` retains its non-dict type.
4. **Observation Reference**: Subsequent statements call `payload.get("session_id")` on line 627 and line 685 without validating `isinstance(payload, dict)`.
5. **Deduction**: Whenever an external hook caller passes non-object JSON, `payload.get` throws `AttributeError`, causing `auto-speech` to terminate with an unhandled traceback and exit code `1`.
6. **Conclusion**: The requirement "Verify defensive handling (no unhandled tracebacks)" is empirically violated. Changes must be requested to safeguard payload parsing.

---

## 3. Challenge Summary

**Overall risk assessment**: **HIGH**

### Challenges

#### [High] Challenge 1: Unhandled Traceback on Non-Dict JSON Payloads in `hook stop` and `hook narrator`
- **Assumption challenged**: The assumption that `json.loads()` on valid JSON always yields a dictionary mapping keys to values, or throws an exception.
- **Attack scenario**: An upstream agent or script pipes a non-object JSON payload (e.g. `[]`, `""`, `null`, `42`) into `auto-speech hook stop` or `auto-speech hook narrator`.
- **Blast radius**: The hook crashes with an unhandled Python traceback and non-zero exit code (`1`), which can cause lifecycle hook runners to log errors, fail builds, or abort tool operations.
- **Mitigation**:
  In `plugin/scripts/python/cli.py`:
  Immediately after `payload = json.loads(...)`:
  ```python
  if not isinstance(payload, dict):
      payload = {}
  ```
  Add unit tests in `tests/test_cli_dispatcher.py` verifying that payloads of `[]`, `42`, `"string"`, and `null` bail cleanly with exit code `0` and no tracebacks.

---

## 4. Stress Test Results

| Scenario | Expected Behavior | Actual Behavior | Result |
|---|---|---|---|
| Global Mute (`~/.claude/auto-speech.disabled`) | Exit 0, stderr notice, no synthesis | Exit 0, clean stderr notice | **PASS** |
| `pause` with no session | Exit 2, error message | Exit 2, clean message | **PASS** |
| `resume` with no session | Exit 2, error message | Exit 2, clean message | **PASS** |
| `restart` with no session | Exit 2, error message | Exit 2, clean message | **PASS** |
| `stop` with no session | Exit 2, error message | Exit 2, clean message | **PASS** |
| `seek +5` with no session | Exit 2, error message | Exit 2, clean message | **PASS** |
| `hook stop` with broken JSON syntax | Exit 0, defensive bail | Exit 0, defensive bail | **PASS** |
| `hook stop` with JSON array `[1, 2]` | Exit 0, defensive bail | Exit 1, `AttributeError` traceback | **FAIL** |
| `hook stop` with JSON int `42` | Exit 0, defensive bail | Exit 1, `AttributeError` traceback | **FAIL** |
| `hook stop` with JSON null `null` | Exit 0, defensive bail | Exit 1, `AttributeError` traceback | **FAIL** |
| `hook stop` with JSON string `"hello"` | Exit 0, defensive bail | Exit 1, `AttributeError` traceback | **FAIL** |
| `hook narrator` with JSON array `[1, 2]` | Exit 0, defensive bail | Exit 1, `AttributeError` traceback | **FAIL** |
| `hook narrator` with JSON int `42` | Exit 0, defensive bail | Exit 1, `AttributeError` traceback | **FAIL** |
| `hook narrator` with JSON null `null` | Exit 0, defensive bail | Exit 1, `AttributeError` traceback | **FAIL** |
| `hook narrator` with JSON string `"hello"`| Exit 0, defensive bail | Exit 1, `AttributeError` traceback | **FAIL** |
| Full Test Suite (`bash tests/run_all.sh`) | 50/50 test suites pass | 50/50 passed | **PASS** |
| Symlink trampoline traversal (multi-hop) | Exit 0, version printed | Exit 0, `auto-speech 0.2.0` | **PASS** |

---

## 5. Caveats

1. **Daemon Synthesis Concurrency**: Tests were performed against the active local narrator daemon socket. Physical audio synthesis for active sessions was not driven concurrently during the tests to prevent acoustic interference.
2. **Review-Only Constraint**: In accordance with the Challenger role constraints, no production files were modified. The reproduction was performed purely empirically via CLI invocations.

---

## 6. Conclusion

The unified CLI trampoline, global mute gating, missing session handling for transport controls, and full test suite (50/50 tests) are operating properly. However, an unhandled exception vulnerability was empirically reproduced in `plugin/scripts/python/cli.py` where `hook stop` and `hook narrator` crash with `AttributeError` and exit code `1` when given non-dict JSON payloads.

**Final Verdict**: **REQUEST_CHANGES**

---

## 7. Verification Method

To independently verify the bug and confirm remediation:
1. Run:
   ```bash
   echo "[1, 2, 3]" | /Users/joshua/Developer/auto-speech-worktree/bin/auto-speech hook stop
   echo "42" | /Users/joshua/Developer/auto-speech-worktree/bin/auto-speech hook stop
   echo "null" | /Users/joshua/Developer/auto-speech-worktree/bin/auto-speech hook narrator
   ```
   *Expected without fix*: Unhandled `AttributeError` traceback, exit code 1.
   *Expected with fix*: Clean exit code 0, no output.
2. Run full regression suite:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree && bash tests/run_all.sh
   ```
