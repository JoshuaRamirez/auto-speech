# Forensic Audit Report: Milestone M1 Bugfixes (`auditor_m1_retry_1`)

**Work Product**: `/Users/joshua/Developer/auto-speech-worktree` (Milestone M1 Bugfixes)  
**Profile**: General Project  
**Integrity Mode**: `development` (per `ORIGINAL_REQUEST.md` under `## 2026-10-06T20:47:50Z`)  
**Auditor**: `teamwork_preview_auditor` (`auditor_m1_retry_1`)  
**Date**: 2026-10-06T22:03:00Z  
**Verdict**: **CLEAN**

---

### Phase Results

- **Git Diff & Target Scope Compliance**: **PASS** — Changes in `/Users/joshua/Developer/auto-speech-worktree` strictly address the 3 reported defects in `plugin/scripts/python/cli.py` (`handle_seek`, `handle_speak`, `handle_hook`) and add corresponding test coverage in `tests/test_cli_dispatcher.py`.
- **Static Analysis (Hardcoded Output / Bypass Detection)**: **PASS** — Zero hardcoded test return values, zero canned responses, zero bypasses in production logic.
- **Facade Detection & Genuine Subsystem Integration**: **PASS** — All 3 bugfix implementations are genuine computational logic directly addressing the defects.
  1. `handle_seek`: Token syntax validation reordered to precede session state evaluation; uses `math.isfinite()` on all numerical conversions (`+N`, `-N`, `N`) and distinguishes syntax error (exit code 4) from inactive session (exit code 2).
  2. `handle_speak`: Terminal detection via `sys.stdin.isatty()` prevents blocking reads on interactive terminals when `--source-hash` is supplied or when no words are given, while fully preserving piped stdin reading for non-TTY streams.
  3. `handle_hook`: Gracefully handles non-dict JSON payloads (`[1, 2, 3]`, `42`, `null`, `true`, `"foo"`) by falling back to `{}` and coercing identifiers safely to strings, eliminating unhandled `AttributeError` exceptions and exiting cleanly with code 0.
- **Production Mock Bypass Check**: **PASS** — Zero imports or references to `unittest`, `mock`, or fake decorators in `cli.py`.
- **Defensive Incompleteness & Error Propagation**: **PASS** — Standard exit codes (0, 1, 2, 4) and stderr diagnostics strictly preserved across all branches.
- **Test Suite Integrity & Non-Tautological Assertions**: **PASS** — `tests/test_cli_dispatcher.py` expanded from 74 to 84 tests. Tests assert specific exit codes, verify that `sys.stdin.read()` is NOT called on interactive TTYs, verify subprocess exit codes and absence of Python tracebacks, and test live `bin/auto-speech` execution via subprocess. Zero self-certifying or tautological assertions.
- **Empirical Execution & Lint Verification**: **PASS** —
  - `tests/test_cli_dispatcher.py`: 84/84 tests passed in 0.725s.
  - `tests/test_install_plugin.sh`: 12/12 assertions passed.
  - Full test suite `bash tests/run_all.sh`: 50/50 test suites passed cleanly with 0 failures.
  - Linter: `.venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py` returned 0 violations.

---

## 1. Observation

### 1.1 Source Code Verification in `plugin/scripts/python/cli.py`

1. **`handle_seek` Implementation (lines 310–360)**:
   ```python
   def handle_seek(args: argparse.Namespace) -> int:
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

       if not _has_active_session():
           print(
               "control: no active playback session (unified daemon uses synchronous audio).",
               file=sys.stderr,
           )
           return EXIT_NO_SESSION

       rc, msg = _send_mpv_command(cmd)
       if rc != EXIT_OK:
           print(msg, file=sys.stderr)
           return rc
       print("seek ok")
       return EXIT_OK
   ```
   - Target syntax is validated *before* evaluating `_has_active_session()`.
   - `math.isfinite()` is explicitly invoked on `float(target[1:])`, `float(target)`, and absolute offsets.
   - Non-finite numbers (`+nan`, `+inf`, `-inf`) and non-numeric strings raise `ValueError` and return `EXIT_BAD_ARG` (4).
   - Valid targets when no session is active return `EXIT_NO_SESSION` (2).

2. **`handle_speak` Implementation (lines 113–130)**:
   ```python
       is_tty = False
       if sys.stdin is not None:
           try:
               is_tty = bool(sys.stdin.isatty())
           except (AttributeError, ValueError, OSError):
               is_tty = False

       if args.words:
           text = " ".join(args.words)
       elif is_tty:
           if args.source_hash:
               text = ""
           else:
               print("speak: no text provided on command line or stdin", file=sys.stderr)
               return EXIT_INVALID_INPUT
       else:
           text = sys.stdin.read() if sys.stdin else ""
   ```
   - Reliably queries `sys.stdin.isatty()` with exception fallbacks.
   - When interactive TTY stdin is detected with `--source-hash`, sets `text = ""` without calling `sys.stdin.read()`, routing straight to cache lookup/playback (`play_cache`).
   - When interactive TTY stdin is detected without words or hash, exits immediately with code 2 (`EXIT_INVALID_INPUT`), eliminating terminal hanging.
   - When non-TTY stdin (piped or redirected) is present, retains full `sys.stdin.read()`.

3. **`handle_hook` Implementation (lines 643–656, 704–730)**:
   ```python
       # In hook stop:
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
   - Guards against non-dict JSON: `if not isinstance(payload, dict): payload = {}`.
   - Coerces session IDs and transcript paths to strings safely.
   - Identical defense implemented in `hook narrator`.
   - When given JSON lists (`[1, 2, 3]`), numbers (`42`), or primitives (`null`, `true`), hooks complete cleanly with exit code 0 without tracebacks.

### 1.2 Test Suite Verification in `tests/test_cli_dispatcher.py`

- Total tests: 84 test cases across 9 classes.
- Tests specifically verifying the bugfixes:
  - `test_speak_tty_source_hash_triggers_cache_without_reading_stdin`: Configures `mock_stdin.read.side_effect = AssertionError(...)`; verifies `play_cache` called and `read()` never called.
  - `test_speak_tty_no_words_no_hash_exits_two_without_reading_stdin`: Configures `mock_stdin.read.side_effect = AssertionError(...)`; verifies exit code 2 and `read()` never called.
  - `test_speak_piped_non_tty_with_text_and_source_hash_uses_piped_text`: Passes piped text via `io.StringIO`; verifies `speak` called with piped text.
  - `test_speak_piped_non_tty_empty_with_source_hash_triggers_cache`: Passes whitespace stdin via `io.StringIO`; verifies fallback to `play_cache`.
  - `test_seek_no_session_valid_target_exits_two`: Iterates through `+15`, `-10`, `30`, `end` verifying exit code 2 without session.
  - `test_seek_empty_whitespace_target_unmocked_exits_four`: Tests `""`, `"   "`, `"\t"` unmocked; asserts exit code 4.
  - `test_seek_invalid_target_unmocked_exits_four`: Tests `foo`, `invalid`, `+abc`, `+`, `-`, `nan`, `+inf` unmocked; asserts exit code 4.
  - `test_seek_invalid_target_even_when_session_inactive_mocked_exits_four`: Mocks `_has_active_session` returning `False`; asserts invalid target still returns exit code 4.
  - `test_hook_stop_non_dict_json_payloads_bail_cleanly`: Tests `[1, 2, 3]`, `42`, `"a string"`, `null`, `true`, `false`, `3.14`; asserts exit code 0.
  - `test_hook_narrator_non_dict_json_payloads_bail_cleanly`: Tests non-dict JSON payloads; asserts exit code 0.
  - `test_bin_hook_non_dict_stdin_exit_zero`: Live subprocess integration test running `bin/auto-speech hook stop` and `hook narrator` with non-dict stdin inputs.

### 1.3 Empirical Execution Results

1. **Unit Test Execution**:
   ```bash
   .venv/bin/python -m unittest tests/test_cli_dispatcher.py
   ```
   Output: `Ran 84 tests in 0.725s — OK`.

2. **Plugin Installer Tests**:
   ```bash
   bash tests/test_install_plugin.sh
   ```
   Output: `install-plugin: 12/12 passed`.

3. **Full Regression Suite**:
   ```bash
   bash tests/run_all.sh
   ```
   Output: `ran: 50, failed: 0, all tests passed`.

4. **Code Linter**:
   ```bash
   .venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py
   ```
   Output: `All checks passed!`.

5. **Adversarial Direct CLI Invocations**:
   - `./bin/auto-speech seek foo`: Returned `RC=4` (diagnostic: `"control: seek requires a target (+N, -N, N, or 'end')"`).
   - `./bin/auto-speech seek +nan`: Returned `RC=4`.
   - `./bin/auto-speech seek +inf`: Returned `RC=4`.
   - `./bin/auto-speech seek -- -inf`: Returned `RC=4`.
   - `./bin/auto-speech seek end`: Returned `RC=2` (diagnostic: `"control: no active playback session (unified daemon uses synchronous audio)."`).
   - `./bin/auto-speech seek +10`: Returned `RC=2`.
   - `./bin/auto-speech seek -- -10`: Returned `RC=2`.
   - `echo "[1, 2, 3]" | ./bin/auto-speech hook stop`: Returned `RC=0`.
   - `echo "[1, 2, 3]" | ./bin/auto-speech hook narrator`: Returned `RC=0`.
   - `echo "42" | ./bin/auto-speech hook stop`: Returned `RC=0`.
   - `echo "null" | ./bin/auto-speech hook narrator`: Returned `RC=0`.
   - `echo '{"session_id": 999}' | ./bin/auto-speech hook narrator`: Returned `RC=0`.
   - `python3 -c "... pty.openpty() ... ./bin/auto-speech speak"`: Exited `RC=2` cleanly in <50ms without hanging on terminal stdin.
   - `python3 -c "... pty.openpty() ... ./bin/auto-speech speak --source-hash <hash>"`: Exited `RC=1` cleanly in <50ms without hanging on terminal stdin.

---

## 2. Logic Chain

1. **Bugfix 1 (Seek Syntax Validation Ordering & Mathematical Integrity)**:
   - Target syntax is an intrinsic property of the CLI command line input, entirely independent of runtime playback state.
   - By validating syntax first, malformed targets (`foo`, `+nan`, `+inf`, `-inf`) consistently fail with `EXIT_BAD_ARG` (4) regardless of whether an audio session is active.
   - The use of `math.isfinite()` prevents floating point anomalies (`NaN`, `Infinity`) from being treated as valid numerical offsets.
   - When syntax is valid but no playback session is running, the command correctly fails with `EXIT_NO_SESSION` (2).
   - The implementation is genuine computational logic with zero facade bypasses.

2. **Bugfix 2 (Interactive Terminal Stdin Detection)**:
   - On interactive terminals, calling `sys.stdin.read()` blocks until the user sends an EOF (Ctrl+D), causing a terminal hang.
   - When `--source-hash` is supplied, the caller specifies content by hash; requiring stdin reading on a TTY is counterproductive.
   - Checking `sys.stdin.isatty()` distinguishes interactive terminals from pipes/redirections.
   - Setting `text = ""` on TTY when `--source-hash` is present allows immediate delegation to cache lookup and daemon playback.
   - Failing fast with exit code 2 when no words or hash are provided on a TTY prevents terminal hangs while preserving piped inputs (`echo "test" | auto-speech speak`).

3. **Bugfix 3 (Non-Dict JSON Defense in Lifecycle Hooks)**:
   - External callers (Claude Code, Antigravity, shell hooks) may pipe arbitrary JSON data to standard input.
   - Valid JSON top-level types include lists, numbers, booleans, strings, and null, which parse without raising `json.JSONDecodeError` but lack dictionary methods like `.get()`.
   - Validating `isinstance(payload, dict)` and defaulting to `{}` guarantees that subsequent lookups succeed safely.
   - Coercing fields to strings ensures unexpected integer session IDs (e.g., `{"session_id": 123}`) do not cause type errors downstream.
   - Returning `EXIT_OK` (0) conforms to hook conventions, preventing shell error spam during agent lifecycle events.

4. **Integrity Mode Conformance**:
   - Under `development` mode (`ORIGINAL_REQUEST.md`), code must contain no hardcoded test results, facade implementations, or fabricated verification outputs.
   - All three bugfixes contain genuine logic integrated with the existing auto-speech architecture.
   - The test suite is authentic, exercising real failure modes and verifying concrete contracts.

---

## 3. Caveats

1. **SystemOne Daemon Liveness**: The local `systemone` daemon was offline (`ConnectionRefusedError: [Errno 61]`), so automated prompt classification rounds could not be queried; all judgements were verified empirically via live test executions, code inspection, and PTY process isolation.
2. **Positional Arguments Starting with Dash**: In bash, passing negative values directly like `./bin/auto-speech seek -10` causes `argparse` to interpret `-10` as an unknown flag unless preceded by `--` (`./bin/auto-speech seek -- -10`). This is standard POSIX/argparse command-line behavior and is properly documented in the test suite and CLI help.

---

## 4. Conclusion

The 3 bugfixes in `/Users/joshua/Developer/auto-speech-worktree` pass all forensic integrity checks with zero violations:
1. `handle_seek`: Genuine, mathematical syntax validation using `math.isfinite()`, returning exit code 4 on invalid syntax and exit code 2 when session is inactive.
2. `handle_speak`: Robust TTY detection preventing terminal hangs while preserving piped input streams.
3. `handle_hook`: Defensive type handling for arbitrary JSON inputs, cleanly bailing with exit code 0.
4. `tests/test_cli_dispatcher.py`: 84 comprehensive, non-tautological unit and integration tests.
5. Full regression test suite (`bash tests/run_all.sh`) passes all 50/50 test suites with 0 failures, and `ruff check` reports 0 errors.

**Verdict**: **CLEAN**.

---

## 5. Verification Method

To independently verify this audit within `/Users/joshua/Developer/auto-speech-worktree`:

```bash
cd /Users/joshua/Developer/auto-speech-worktree

# 1. Run expanded unit tests (84 tests)
.venv/bin/python -m unittest tests/test_cli_dispatcher.py

# 2. Run plugin installer tests (12 tests)
bash tests/test_install_plugin.sh

# 3. Run complete regression test suite (50 test suites)
bash tests/run_all.sh

# 4. Verify code formatting and lint
.venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py

# 5. Direct verification of seek exit codes
./bin/auto-speech seek foo              # Exits 4 (EXIT_BAD_ARG)
./bin/auto-speech seek +nan             # Exits 4 (EXIT_BAD_ARG)
./bin/auto-speech seek -- -inf          # Exits 4 (EXIT_BAD_ARG)
./bin/auto-speech seek +10              # Exits 2 (EXIT_NO_SESSION)
./bin/auto-speech seek end              # Exits 2 (EXIT_NO_SESSION)

# 6. Direct verification of hook non-dict JSON handling
echo "[1, 2, 3]" | ./bin/auto-speech hook stop       # Exits 0
echo "[1, 2, 3]" | ./bin/auto-speech hook narrator   # Exits 0
echo "42" | ./bin/auto-speech hook stop              # Exits 0
echo "null" | ./bin/auto-speech hook narrator        # Exits 0
```
