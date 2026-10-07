# Investigation & Recommendation Report: `auto-speech seek` Target Validation Order

**Date**: 2026-10-06T21:47:30Z  
**Target Repository**: `/Users/joshua/Developer/auto-speech-worktree`  
**Investigator**: `teamwork_preview_explorer` (`explorer_m1_retry_1`)  
**Milestone**: M1 Retry 1  

---

## 1. Observation

### 1.1 Direct CLI Reproduction
In `/Users/joshua/Developer/auto-speech-worktree`:
Running `bin/auto-speech seek foo`:
```bash
./bin/auto-speech seek foo
```
- **Observed Exit Code**: `2`
- **Observed Stderr**:
  ```text
  control: no active playback session (unified daemon uses synchronous audio).
  ```

Running with empty target string `bin/auto-speech seek ""`:
```bash
./bin/auto-speech seek ""
```
- **Observed Exit Code**: `2`
- **Observed Stderr**:
  ```text
  control: no active playback session (unified daemon uses synchronous audio).
  ```

Running with non-numeric offset `bin/auto-speech seek "+abc"`:
```bash
./bin/auto-speech seek "+abc"
```
- **Observed Exit Code**: `2`
- **Observed Stderr**:
  ```text
  control: no active playback session (unified daemon uses synchronous audio).
  ```

Running with valid offset when no playback is active `bin/auto-speech seek +15`:
```bash
./bin/auto-speech seek +15
```
- **Observed Exit Code**: `2`
- **Observed Stderr**:
  ```text
  control: no active playback session (unified daemon uses synchronous audio).
  ```

### 1.2 Specification Contract in `PROJECT.md`
In `/Users/joshua/Developer/auto-speech/PROJECT.md` lines 88–95:
```markdown
- **Exit Codes**:
  - `0`: Success (or cleanly muted)
  - `1`: General failure / daemon unreachable / doctor FAIL
  - `2`: No active playback session / invalid input / missing target
  - `3`: mpv IPC failure
  - `4`: Invalid target argument
  - `6`: Playback failure
  - `130`: Interrupted (SIGINT)
```
Contract requirement: An invalid target argument passed to `seek` (e.g. non-number string like `foo`, empty string, non-numeric relative offset like `+abc`) must yield exit code `4` (`EXIT_BAD_ARG`). Missing argument entirely (`auto-speech seek`) exits `2` via argparse. Valid target argument executed when no session exists must yield exit code `2` (`EXIT_NO_SESSION`).

### 1.3 Code Inspection of `cli.py`
In `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/cli.py` lines 296–339:
```python
296: def handle_seek(args: argparse.Namespace) -> int:
297:     if not _has_active_session():
298:         print(
299:             "control: no active playback session (unified daemon uses synchronous audio).",
300:             file=sys.stderr,
301:         )
302:         return EXIT_NO_SESSION
303: 
304:     target = args.target.strip()
305:     if not target:
306:         print("control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
307:         return EXIT_BAD_ARG
308: 
309:     if target == "end":
310:         cmd: list[Any] = ["seek", 999999, "absolute"]
311:     elif target.startswith("+"):
312:         try:
313:             val = float(target[1:])
314:             cmd = ["seek", val, "relative"]
315:         except ValueError:
316:             print("control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
317:             return EXIT_BAD_ARG
318:     elif target.startswith("-"):
319:         try:
320:             val = float(target)
321:             cmd = ["seek", val, "relative"]
322:         except ValueError:
323:             print("control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
324:             return EXIT_BAD_ARG
325:     else:
326:         try:
327:             val = float(target)
328:             cmd = ["seek", val, "absolute"]
329:         except ValueError:
330:             print("control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
331:             return EXIT_BAD_ARG
332: 
333:     rc, msg = _send_mpv_command(cmd)
334:     if rc != EXIT_OK:
335:         print(msg, file=sys.stderr)
336:         return rc
337:     print("seek ok")
338:     return EXIT_OK
```

Notice: Line 297 checks `if not _has_active_session():` and exits immediately with `EXIT_NO_SESSION` (2). Lines 304–332 (target syntax parsing and validation) are unreachable whenever no session is active.

### 1.4 Test Suite Masking in `tests/test_cli_dispatcher.py`
In `/Users/joshua/Developer/auto-speech-worktree/tests/test_cli_dispatcher.py` lines 403–422:
```python
403:     def test_seek_empty_whitespace_target_exits_four(self) -> None:
404:         stderr = io.StringIO()
405:         with (
406:             mock.patch("cli._has_active_session", return_value=True),
407:             mock.patch("sys.stderr", stderr),
408:         ):
409:             rc = cli.main(["seek", "   "])
410:         self.assertEqual(rc, 4)
411:         self.assertIn("seek requires a target", stderr.getvalue())
412: 
413:     def test_seek_invalid_target_exits_four(self) -> None:
414:         stderr = io.StringIO()
415:         with (
416:             mock.patch("cli._has_active_session", return_value=True),
417:             mock.patch("sys.stderr", stderr),
418:         ):
419:             rc = cli.main(["seek", "invalid"])
420:         self.assertEqual(rc, 4)
421:         self.assertIn("seek requires a target", stderr.getvalue())
```

Notice: Lines 406 and 417 explicitly patched `_has_active_session` to return `True`. This prevented the test suite from detecting that under realistic, un-mocked conditions (where no audio is playing), `cli.main(["seek", "invalid"])` returns `2` instead of `4`.

---

## 2. Logic Chain

1. **Premise**: Per PROJECT.md §Interface Contracts, invalid target arguments passed to `seek` must return exit code `4` (`EXIT_BAD_ARG`), whereas valid target arguments when no session exists must return exit code `2` (`EXIT_NO_SESSION`).
2. **Observation**: Executing `./bin/auto-speech seek foo` returns exit code `2` with diagnostic `"control: no active playback session..."` (Observation §1.1).
3. **Trace**: `handle_seek` evaluates `_has_active_session()` on line 297, before parsing or validating `args.target` on lines 304–332 (Observation §1.3).
4. **Root Cause**: The ordering in `handle_seek` prioritizes session presence check ahead of parameter validation. In command line execution without an active playback session, `_has_active_session()` always evaluates to `False`, aborting with code `2` before syntax validation can execute.
5. **Test Failure Origin**: The test suite in `tests/test_cli_dispatcher.py` masked this defect because unit tests artificially mocked `cli._has_active_session = True` on lines 406 and 417, creating an artificial environment that did not reflect CLI execution (Observation §1.4).
6. **Solution Formulation**:
   - Reorder `handle_seek`: Validate `args.target` syntax first (`empty`, `end`, `+N`, `-N`, `N`, checking finite float conversion).
   - If `args.target` is syntactically invalid, emit diagnostic `"control: seek requires a target (+N, -N, N, or 'end')"` to `sys.stderr` and return `EXIT_BAD_ARG` (4) immediately.
   - If `args.target` is syntactically valid, check `_has_active_session()`. If no active session, emit diagnostic `"control: no active playback session (unified daemon uses synchronous audio)."` to `sys.stderr` and return `EXIT_NO_SESSION` (2).
   - Update `tests/test_cli_dispatcher.py` to assert exit code `4` un-mocked and with `_has_active_session=False`.

---

## 3. Caveats

1. **Argparse Missing Argument vs Invalid Argument**:
   Running `./bin/auto-speech seek` (with zero arguments) is caught by `argparse` because `target` is a required positional argument (`p_seek.add_argument("target", ...)`). `argparse` prints a usage error and exits with code `2` (`EXIT_INVALID_INPUT`). This is expected and contract-compliant per PROJECT.md line 91 ("`2`: missing target").
2. **Interactive Stdin Hang on `speak --source-hash`**:
   As identified in challenger_m1_1's secondary finding, `handle_speak` at lines 112–115 unconditionally calls `sys.stdin.read()` when `not args.words`. When `--source-hash` is provided in an interactive terminal, `sys.stdin.read()` blocks until EOF. While separate from the `seek` ordering bug, the implementer should ensure `sys.stdin.read()` is guarded with `not sys.stdin.isatty()` when `--source-hash` is supplied.
3. **Decision Model (`systemone`)**:
   Attempting to query `systemone round` reported that the local launchd service was not currently active (`[Errno 61] Connection refused`). Conclusions are grounded directly on empirical execution and code inspection.

---

## 4. Conclusion & Concrete Fix Strategy

### 4.1 Recommended Changes in `plugin/scripts/python/cli.py`

#### 4.1.1 Add `import math`
In `plugin/scripts/python/cli.py`, add `import math` to the standard library imports (around line 7).

#### 4.1.2 Replacement for `handle_seek` (lines 296–339)
Replace `handle_seek` with the following implementation:

```python
def handle_seek(args: argparse.Namespace) -> int:
    # 1. Validate target syntax FIRST before checking session presence
    target = getattr(args, "target", "")
    target = target.strip() if isinstance(target, str) else ""
    if not target:
        print("control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
        return EXIT_BAD_ARG

    cmd: list[Any]
    if target == "end":
        cmd = ["seek", 999999, "absolute"]
    elif target.startswith("+"):
        try:
            val = float(target[1:])
            if not math.isfinite(val):
                raise ValueError("non-finite seek target")
            cmd = ["seek", val, "relative"]
        except ValueError:
            print("control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
            return EXIT_BAD_ARG
    elif target.startswith("-"):
        try:
            val = float(target)
            if not math.isfinite(val):
                raise ValueError("non-finite seek target")
            cmd = ["seek", val, "relative"]
        except ValueError:
            print("control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
            return EXIT_BAD_ARG
    else:
        try:
            val = float(target)
            if not math.isfinite(val) or val < 0:
                raise ValueError("non-finite or negative absolute seek target")
            cmd = ["seek", val, "absolute"]
        except ValueError:
            print("control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
            return EXIT_BAD_ARG

    # 2. Only after target is validated, check active session presence
    if not _has_active_session():
        print(
            "control: no active playback session (unified daemon uses synchronous audio).",
            file=sys.stderr,
        )
        return EXIT_NO_SESSION

    # 3. Dispatch command to active mpv session
    rc, msg = _send_mpv_command(cmd)
    if rc != EXIT_OK:
        print(msg, file=sys.stderr)
        return rc
    print("seek ok")
    return EXIT_OK
```

#### 4.1.3 Behavior Matrix of the New Implementation

| Invocations | Target Validated | Session Check | Resulting Exit Code | Stderr Output |
| :--- | :--- | :--- | :--- | :--- |
| `auto-speech seek ""` | Fails (`not target`) | Not reached | `4` (`EXIT_BAD_ARG`) | `control: seek requires a target (+N, -N, N, or 'end')` |
| `auto-speech seek "   "` | Fails (`not target`) | Not reached | `4` (`EXIT_BAD_ARG`) | `control: seek requires a target (+N, -N, N, or 'end')` |
| `auto-speech seek foo` | Fails (not float) | Not reached | `4` (`EXIT_BAD_ARG`) | `control: seek requires a target (+N, -N, N, or 'end')` |
| `auto-speech seek +abc` | Fails (not float) | Not reached | `4` (`EXIT_BAD_ARG`) | `control: seek requires a target (+N, -N, N, or 'end')` |
| `auto-speech seek +nan` | Fails (not finite) | Not reached | `4` (`EXIT_BAD_ARG`) | `control: seek requires a target (+N, -N, N, or 'end')` |
| `auto-speech seek +15` (no session) | Passes (`cmd` built) | Fails (`False`) | `2` (`EXIT_NO_SESSION`) | `control: no active playback session (...)` |
| `auto-speech seek end` (no session) | Passes (`cmd` built) | Fails (`False`) | `2` (`EXIT_NO_SESSION`) | `control: no active playback session (...)` |
| `auto-speech seek +15` (session active) | Passes (`cmd` built) | Passes (`True`) | `0` (or mpv status) | `seek ok` (on stdout) |

---

### 4.2 Recommended Test Changes in `tests/test_cli_dispatcher.py`

In `TestCliTransportControls` (around lines 387–422), replace and expand the seek test methods:

```python
    def test_seek_no_session_valid_target_exits_two(self) -> None:
        """When no session is active, valid targets must exit 2 with diagnostic."""
        for target in ["+15", "-10", "30", "end"]:
            with self.subTest(target=target):
                stderr = io.StringIO()
                with mock.patch("sys.stderr", stderr):
                    rc = cli.main(["seek", target])
                self.assertEqual(rc, 2)
                self.assertIn("no active playback session", stderr.getvalue())

    def test_seek_missing_target_exits_two(self) -> None:
        """Missing required positional target exits 2 via argparse."""
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            rc = cli.main(["seek"])
        self.assertEqual(rc, 2)

    def test_seek_empty_whitespace_target_unmocked_exits_four(self) -> None:
        """Empty or whitespace targets must exit 4 without mocking session presence."""
        for target in ["", "   ", "\t"]:
            with self.subTest(target=target):
                stderr = io.StringIO()
                with mock.patch("sys.stderr", stderr):
                    rc = cli.main(["seek", target])
                self.assertEqual(rc, 4)
                self.assertIn("seek requires a target", stderr.getvalue())

    def test_seek_invalid_target_unmocked_exits_four(self) -> None:
        """Un-mocked execution with invalid target syntax must exit 4, NOT 2."""
        invalid_targets = ["foo", "invalid", "+abc", "+", "-", "nan", "+inf"]
        for target in invalid_targets:
            with self.subTest(target=target):
                stderr = io.StringIO()
                with mock.patch("sys.stderr", stderr):
                    rc = cli.main(["seek", target])
                self.assertEqual(
                    rc,
                    4,
                    f"seek target {target!r} returned rc={rc}, expected 4 (EXIT_BAD_ARG)",
                )
                self.assertIn("seek requires a target", stderr.getvalue())

    def test_seek_invalid_target_even_when_session_inactive_mocked_exits_four(self) -> None:
        """Even if _has_active_session explicitly returns False, invalid target still exits 4."""
        stderr = io.StringIO()
        with (
            mock.patch("cli._has_active_session", return_value=False),
            mock.patch("sys.stderr", stderr),
        ):
            rc = cli.main(["seek", "foo"])
        self.assertEqual(rc, 4)
        self.assertIn("seek requires a target", stderr.getvalue())
```

---

## 5. Verification Method

To independently verify the implementation:

1. **Verify Un-mocked CLI Return Codes in Worktree**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree

   # Case 1: Invalid target (foo) -> Must exit 4
   ./bin/auto-speech seek foo
   echo "Exit code: $?"  # Expected: 4

   # Case 2: Empty target ("") -> Must exit 4
   ./bin/auto-speech seek ""
   echo "Exit code: $?"  # Expected: 4

   # Case 3: Invalid relative offset (+abc) -> Must exit 4
   ./bin/auto-speech seek "+abc"
   echo "Exit code: $?"  # Expected: 4

   # Case 4: Valid relative offset with no active session (+15) -> Must exit 2
   ./bin/auto-speech seek +15
   echo "Exit code: $?"  # Expected: 2

   # Case 5: Valid absolute offset with no active session (end) -> Must exit 2
   ./bin/auto-speech seek end
   echo "Exit code: $?"  # Expected: 2
   ```

2. **Verify Python Unit Tests**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/python -m unittest tests/test_cli_dispatcher.py
   ```
   Must pass all tests including the new un-mocked seek assertions.

3. **Verify Full Test Suite Regression**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   bash tests/run_all.sh
   ```
   Must pass all 50/50 test suites with 0 failures.

4. **Lint and Formatting Check**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py
   ```
   Must return 0 violations.
