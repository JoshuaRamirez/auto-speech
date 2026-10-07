# Investigation & Remediation Handoff Report: Explorer M1 Retry 3

## Summary
In `handle_hook` (`plugin/scripts/python/cli.py`), both `hook stop` (lines 621–631) and `hook narrator` (lines 678–688) fail to verify that `payload` is an instance of `dict` after parsing standard input with `json.loads(payload_raw)`. Valid JSON primitives and containers that are not objects (such as `[1, 2, 3]`, `42`, `"hello"`, `null`, `true`) parse without raising JSON parse exceptions, causing immediate unhandled `AttributeError: '<type>' object has no attribute 'get'` crashes and non-zero exit code (`1`). 

This report provides the exact empirical observations, root cause analysis, drop-in replacement code for `cli.py`, and 5 complete unit/integration test cases for `tests/test_cli_dispatcher.py`.

---

## 1. Observation

### 1.1 Code Inspection of Vulnerable Sites
In `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/cli.py`:

**Site 1: `hook stop` (lines 621–631)**
```python
        payload_raw = sys.stdin.read() if sys.stdin else ""
        try:
            payload = json.loads(payload_raw) if payload_raw.strip() else {}
        except Exception:
            payload = {}

        session_id = payload.get("session_id") or payload.get("conversationId") or ""
        transcript_path = (
            payload.get("transcript_path") or payload.get("transcriptPath") or ""
        )
```

**Site 2: `hook narrator` (lines 678–688)**
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

### 1.2 Verbatim Empirical Reproduction Commands & Output

Running empirical commands directly against `/Users/joshua/Developer/auto-speech-worktree/bin/auto-speech`:

1. **Array Payload to `hook stop`**:
   - Command: `echo "[1, 2, 3]" | bin/auto-speech hook stop`
   - Exit Code: `1`
   - Stderr:
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
     ```

2. **Array Payload to `hook narrator`**:
   - Command: `echo "[1, 2, 3]" | bin/auto-speech hook narrator`
   - Exit Code: `1`
   - Stderr:
     ```
     Traceback (most recent call last):
       File "/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/cli.py", line 1048, in <module>
         sys.exit(main())
                  ^^^^^^
       File "/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/cli.py", line 1034, in main
         return handle_hook(args)
                ^^^^^^^^^^^^^^^^^
       File "/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/cli.py", line 685, in handle_hook
         payload.get("session_id")
         ^^^^^^^^^^^
     AttributeError: 'list' object has no attribute 'get'
     ```

3. **Integer, Null, String, and Boolean Payloads**:
   - `echo "42" | bin/auto-speech hook stop` -> `AttributeError: 'int' object has no attribute 'get'`, Exit `1`
   - `echo "null" | bin/auto-speech hook stop` -> `AttributeError: 'NoneType' object has no attribute 'get'`, Exit `1`
   - `echo '"hello"' | bin/auto-speech hook stop` -> `AttributeError: 'str' object has no attribute 'get'`, Exit `1`
   - `echo "true" | bin/auto-speech hook stop` -> `AttributeError: 'bool' object has no attribute 'get'`, Exit `1`

### 1.3 Downstream Type Sensitivity Inspection
1. In `autoplay_enrollment.py` line 50:
   ```python
   def marker_path(self, session_id: str) -> Path:
       return self.dir_path() / session_id
   ```
   If a valid dictionary contains a non-string value such as `{"session_id": 123}`, in Python `Path / 123` raises `TypeError: unsupported operand type(s) for /: 'PosixPath' and 'int'`.
2. In `cli.py` line 692:
   ```python
   marker = Path.home() / ".claude" / "auto-speech-narrate-sessions" / session_id
   ```
   Similarly, `Path / session_id` requires `session_id` to be a `str`.

---

## 2. Logic Chain

1. **Premise 1**: Shell lifecycle hooks (`Stop`, `PreToolUse`, `PostToolUse`, `UserPromptSubmit`) communicate by writing JSON to standard input in automated, unattended environments.
2. **Premise 2**: Hook entrypoints must be defensively hardened to never crash or emit unhandled tracebacks on malformed, unanticipated, or non-object payloads; when payload structure is invalid or unidentifiable, they must fail safe and exit `0` cleanly.
3. **Step 1 (Observation 1.1 & 1.2)**: `json.loads()` successfully parses valid non-dict JSON representations (`list`, `int`, `str`, `bool`, `None`). Because the `except Exception:` block only catches parse failures, `payload` is assigned the non-dict parsed object without triggering the fallback `{}` assignment.
4. **Step 2 (Observation 1.1 & 1.2)**: Subsequent lines call `.get(...)` on `payload`. Non-dict objects in Python lack a `.get` method, producing an unhandled `AttributeError` traceback and terminating the CLI process with exit code `1`.
5. **Step 3 (Observation 1.3)**: Even if `payload` is a dict, values within the dict may be non-string types. Coercing session and transcript paths to strings ensures downstream path manipulations (`Path / session_id`) cannot trigger `TypeError`.
6. **Step 4**: Checking `if not isinstance(payload, dict): payload = {}` immediately after `json.loads` guarantees that `payload` is always a dictionary.
7. **Step 5**: With `payload` guaranteed to be a dictionary, `.get("key")` is safe. When keys are missing or payload is empty:
   - In `hook stop`: `session_id` defaults to `""`. `SessionEnrollment().is_enrolled("")` returns `False`, so it bails immediately and returns `EXIT_OK` (0).
   - In `hook narrator`: `session_id` defaults to `""` (assuming `CLAUDE_CODE_SESSION_ID` is unset or empty), and `if not session_id: return EXIT_OK` returns `EXIT_OK` (0).
8. **Conclusion**: Adding `if not isinstance(payload, dict): payload = {}` and defensive string coercion eliminates all tracebacks, guarantees clean exit `0`, and satisfies all contractual requirements.

---

## 3. Recommended Fix Implementation

### 3.1 Target File: `plugin/scripts/python/cli.py`

#### Edit Site 1: `hook stop` (Lines 621–631)
**Existing Code:**
```python
        payload_raw = sys.stdin.read() if sys.stdin else ""
        try:
            payload = json.loads(payload_raw) if payload_raw.strip() else {}
        except Exception:
            payload = {}

        session_id = payload.get("session_id") or payload.get("conversationId") or ""
        transcript_path = (
            payload.get("transcript_path") or payload.get("transcriptPath") or ""
        )
```

**Replacement Code:**
```python
        payload_raw = sys.stdin.read() if sys.stdin else ""
        try:
            payload = json.loads(payload_raw) if payload_raw.strip() else {}
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        raw_session_id = payload.get("session_id") or payload.get("conversationId") or ""
        session_id = raw_session_id if isinstance(raw_session_id, str) else str(raw_session_id)
        raw_transcript = (
            payload.get("transcript_path") or payload.get("transcriptPath") or ""
        )
        transcript_path = raw_transcript if isinstance(raw_transcript, str) else str(raw_transcript)
```

#### Edit Site 2: `hook narrator` (Lines 678–705)
**Existing Code:**
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
        if not session_id:
            return EXIT_OK

        marker = Path.home() / ".claude" / "auto-speech-narrate-sessions" / session_id
        if not marker.is_file():
            return EXIT_OK

        c = DaemonClient()
        c.ensure_daemon_running()

        record = {
            "ts": int(time.time()),
            "cwd": os.getcwd(),
            "pid": os.getpid(),
            "event": payload.get("hook_event_name", "unknown"),
            "payload": payload,
        }
```

**Replacement Code:**
```python
        payload_raw = sys.stdin.read() if sys.stdin else ""
        try:
            payload = json.loads(payload_raw) if payload_raw.strip() else {}
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        raw_session_id = (
            payload.get("session_id")
            or payload.get("conversationId")
            or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
        )
        session_id = raw_session_id if isinstance(raw_session_id, str) else str(raw_session_id)
        if not session_id:
            return EXIT_OK

        marker = Path.home() / ".claude" / "auto-speech-narrate-sessions" / session_id
        if not marker.is_file():
            return EXIT_OK

        c = DaemonClient()
        c.ensure_daemon_running()

        raw_event = payload.get("hook_event_name", "unknown")
        event = raw_event if isinstance(raw_event, str) else str(raw_event)

        record = {
            "ts": int(time.time()),
            "cwd": os.getcwd(),
            "pid": os.getpid(),
            "event": event,
            "payload": payload,
        }
```

### 3.2 Unified Diff Patch for `plugin/scripts/python/cli.py`
```diff
--- a/plugin/scripts/python/cli.py
+++ b/plugin/scripts/python/cli.py
@@ -624,10 +624,14 @@ def handle_hook(args: argparse.Namespace) -> int:
         except Exception:
             payload = {}
+        if not isinstance(payload, dict):
+            payload = {}
 
-        session_id = payload.get("session_id") or payload.get("conversationId") or ""
-        transcript_path = (
+        raw_session_id = payload.get("session_id") or payload.get("conversationId") or ""
+        session_id = raw_session_id if isinstance(raw_session_id, str) else str(raw_session_id)
+        raw_transcript = (
             payload.get("transcript_path") or payload.get("transcriptPath") or ""
         )
+        transcript_path = raw_transcript if isinstance(raw_transcript, str) else str(raw_transcript)
 
         scope = SoloScope()
         if not scope.session_allowed(session_id):
@@ -681,10 +685,13 @@ def handle_hook(args: argparse.Namespace) -> int:
         except Exception:
             payload = {}
+        if not isinstance(payload, dict):
+            payload = {}
 
-        session_id = (
+        raw_session_id = (
             payload.get("session_id")
             or payload.get("conversationId")
             or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
         )
+        session_id = raw_session_id if isinstance(raw_session_id, str) else str(raw_session_id)
         if not session_id:
             return EXIT_OK
@@ -699,9 +706,12 @@ def handle_hook(args: argparse.Namespace) -> int:
         c = DaemonClient()
         c.ensure_daemon_running()
 
+        raw_event = payload.get("hook_event_name", "unknown")
+        event = raw_event if isinstance(raw_event, str) else str(raw_event)
+
         record = {
             "ts": int(time.time()),
             "cwd": os.getcwd(),
             "pid": os.getpid(),
-            "event": payload.get("hook_event_name", "unknown"),
+            "event": event,
             "payload": payload,
         }
```

---

## 4. Test Specifications for `tests/test_cli_dispatcher.py`

In `tests/test_cli_dispatcher.py`, expand `TestCliHook` and `TestCliTrampolineIntegration` with the following 5 tests:

```python
    def test_hook_stop_non_dict_json_payloads_bail_cleanly(self) -> None:
        """Non-dict valid JSON primitives and arrays must not cause AttributeError."""
        non_dict_payloads = [
            "[1, 2, 3]",
            "42",
            '"a string"',
            "null",
            "true",
            "false",
            "3.14",
        ]
        for raw in non_dict_payloads:
            with (
                self.subTest(payload=raw),
                mock.patch("sys.stdin", io.StringIO(raw)),
                mock.patch("subprocess.Popen") as mock_popen,
            ):
                rc = cli.main(["hook", "stop"])
                self.assertEqual(rc, 0)
                mock_popen.assert_not_called()

    def test_hook_stop_missing_keys_bails_cleanly(self) -> None:
        """Dict payloads with missing or non-string keys must bail cleanly."""
        payloads = [
            "{}",
            '{"unrelated_key": "val"}',
            '{"session_id": null}',
            '{"session_id": ""}',
            '{"session_id": 12345}',
        ]
        for raw in payloads:
            with (
                self.subTest(payload=raw),
                mock.patch("sys.stdin", io.StringIO(raw)),
                mock.patch("subprocess.Popen") as mock_popen,
            ):
                rc = cli.main(["hook", "stop"])
                self.assertEqual(rc, 0)
                mock_popen.assert_not_called()

    def test_hook_narrator_non_dict_json_payloads_bail_cleanly(self) -> None:
        """Narrator hook must safely handle non-dict JSON and exit 0."""
        events_file = Path("/tmp/auto-speech-narrator-events.jsonl")
        events_file.unlink(missing_ok=True)

        non_dict_payloads = [
            "[1, 2, 3]",
            "42",
            '"a string"',
            "null",
            "true",
            "false",
            "3.14",
            "not valid json at all",
        ]
        for raw in non_dict_payloads:
            with (
                self.subTest(payload=raw),
                mock.patch("sys.stdin", io.StringIO(raw)),
                mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": ""}, clear=False),
            ):
                rc = cli.main(["hook", "narrator"])
                self.assertEqual(rc, 0)
        self.assertFalse(events_file.is_file())

    def test_hook_narrator_missing_keys_bails_cleanly(self) -> None:
        """Narrator hook with empty dict or missing session_id must exit 0."""
        events_file = Path("/tmp/auto-speech-narrator-events.jsonl")
        events_file.unlink(missing_ok=True)

        payloads = [
            "{}",
            '{"unrelated": 123}',
            '{"session_id": null}',
            '{"session_id": ""}',
        ]
        for raw in payloads:
            with (
                self.subTest(payload=raw),
                mock.patch("sys.stdin", io.StringIO(raw)),
                mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": ""}, clear=False),
            ):
                rc = cli.main(["hook", "narrator"])
                self.assertEqual(rc, 0)
        self.assertFalse(events_file.is_file())
```

And in `TestCliTrampolineIntegration`:
```python
    def test_bin_hook_non_dict_stdin_exit_zero(self) -> None:
        """Integration test: trampoline exits 0 on non-dict stdin without traceback."""
        repo_root = Path(__file__).resolve().parents[1]
        bin_script = repo_root / "bin" / "auto-speech"
        for subcmd in ["stop", "narrator"]:
            for payload in ["[1, 2, 3]", "42", "null", '"foo"', "true"]:
                res = subprocess.run(
                    [str(bin_script), "hook", subcmd],
                    input=payload,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(
                    res.returncode,
                    0,
                    f"hook {subcmd} failed with exit {res.returncode} for input {payload}: {res.stderr}",
                )
                self.assertNotIn("Traceback", res.stderr)
```

---

## 5. Caveats

1. **Read-Only Investigation**: As instructed, no modifications were made to `/Users/joshua/Developer/auto-speech-worktree/` files during this investigation.
2. **Environment Variable Precedence in `hook narrator`**: In `hook narrator`, if the stdin payload is non-dict (becoming `{}`), but `CLAUDE_CODE_SESSION_ID` exists in the calling environment, `session_id` will be resolved to `os.environ["CLAUDE_CODE_SESSION_ID"]`. If that session is enrolled, an event record will be appended with `"event": "unknown"` and `"payload": {}`. This is existing intended behavior per the Claude Code fallback design and operates safely without error.

---

## 6. Conclusion

The defect reported by `challenger_m1_2` was successfully reproduced and diagnosed. The root cause is the lack of `isinstance(payload, dict)` validation following `json.loads(payload_raw)` in `plugin/scripts/python/cli.py` (`handle_hook`).

Applying the recommended fix:
1. Performs defensive verification: `if not isinstance(payload, dict): payload = {}` in both `hook stop` and `hook narrator`.
2. Handles missing keys via `.get()` with safe string coercion.
3. Exits cleanly with code `0` on all malformed and non-dict JSON payloads without tracebacks.
4. Adds unit and trampoline integration test suites covering arrays, numbers, strings, nulls, booleans, and missing dictionary keys.

---

## 7. Verification Method

Once implemented by the builder agent, independently verify using:

1. **CLI Non-Dict Stdin Smoke Test**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   echo "[1, 2, 3]" | bin/auto-speech hook stop
   echo "42" | bin/auto-speech hook stop
   echo "null" | bin/auto-speech hook stop
   echo '"test"' | bin/auto-speech hook stop
   echo "[1, 2, 3]" | bin/auto-speech hook narrator
   echo "42" | bin/auto-speech hook narrator
   echo "null" | bin/auto-speech hook narrator
   ```
   *Expected result*: All commands return exit code `0` with empty output (no tracebacks).

2. **Unit Test Suite**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/python -m unittest tests/test_cli_dispatcher.py
   ```
   *Expected result*: All tests pass (78+ tests).

3. **Full Regression Suite**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   bash tests/run_all.sh
   ```
   *Expected result*: All 50 test suites pass.

4. **Lint Check**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py
   ```
   *Expected result*: 0 violations.
