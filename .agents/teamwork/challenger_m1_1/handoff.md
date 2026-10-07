# Empirical Challenge Report: CLI Argument Parsing, Input Resolution & Error Exit Codes

**Date**: 2026-10-06T21:40:50Z  
**Target Repository**: `/Users/joshua/Developer/auto-speech-worktree`  
**Challenger**: `teamwork_preview_challenger` (`challenger_m1_1`)  
**Verdict**: **REQUEST_CHANGES**

---

## 1. Observation

Direct empirical test invocations and observed behaviors across the 8 required test scopes and boundary checks against `bin/auto-speech` and `plugin/scripts/python/cli.py`:

### Test Scope 1: Unknown subcommand
- **Command**:
  ```bash
  ./bin/auto-speech unknown_subcommand
  ```
- **Exit Code**: `2`
- **Stderr**:
  ```text
  usage: auto-speech [-h] [--version] <subcommand> ...
  auto-speech: error: argument <subcommand>: invalid choice: 'unknown_subcommand' (choose from speak, replay, pause, resume, restart, stop, seek, autoplay, narrate, hook, doctor, update)
  ```
- **Status**: **PASS** (expected exit code 2 and usage diagnostic).

---

### Test Scope 2: `speak` with invalid `--source-hash` (non-hex or not 64 chars)
- **Command A (short non-64 chars)**:
  ```bash
  ./bin/auto-speech speak --source-hash "not_64_chars"
  ```
  - **Exit Code**: `2`
  - **Stderr**:
    ```text
    speak: --source-hash must be 64 hex chars, got 'not_64_chars'
    ```
- **Command B (64 chars with non-hex characters)**:
  ```bash
  ./bin/auto-speech speak --source-hash "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"
  ```
  - **Exit Code**: `2`
  - **Stderr**:
    ```text
    speak: --source-hash must be 64 hex chars, got 'zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz'
    ```
- **Status**: **PASS** (expected exit code 2).

---

### Test Scope 3: `seek` with invalid targets (empty, non-number string like `foo`)
- **Specification**: PROJECT.md lines 91–93:
  - `2`: No active playback session / invalid input / missing target
  - `4`: Invalid target argument
  And dispatch requirement 3: "`seek` with invalid targets (empty, non-number string like `foo`) -> verify exit code 4."
- **Command A (`seek foo`)**:
  ```bash
  ./bin/auto-speech seek foo
  ```
  - **Exit Code**: `2` (**FAIL — expected `4`**)
  - **Stderr**:
    ```text
    control: no active playback session (unified daemon uses synchronous audio).
    ```
- **Command B (`seek ""` / empty string)**:
  ```bash
  ./bin/auto-speech seek ""
  ```
  - **Exit Code**: `2` (**FAIL — expected `4`**)
  - **Stderr**:
    ```text
    control: no active playback session (unified daemon uses synchronous audio).
    ```
- **Command C (`seek "+abc"`)**:
  ```bash
  ./bin/auto-speech seek "+abc"
  ```
  - **Exit Code**: `2` (**FAIL — expected `4`**)
  - **Stderr**:
    ```text
    control: no active playback session (unified daemon uses synchronous audio).
    ```
- **Direct Code Inspection**: `plugin/scripts/python/cli.py` lines 296–308:
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
  ```
- **Test Suite Masking**: In `tests/test_cli_dispatcher.py` lines 403–422:
  ```python
  403:     def test_seek_empty_whitespace_target_exits_four(self) -> None:
  404:         stderr = io.StringIO()
  405:         with (
  406:             mock.patch("cli._has_active_session", return_value=True),
  407:             mock.patch("sys.stderr", stderr),
  408:         ):
  409:             rc = cli.main(["seek", "   "])
  410:         self.assertEqual(rc, 4)
  ...
  414:     def test_seek_invalid_target_exits_four(self) -> None:
  415:         stderr = io.StringIO()
  416:         with (
  417:             mock.patch("cli._has_active_session", return_value=True),
  418:             mock.patch("sys.stderr", stderr),
  419:         ):
  420:             rc = cli.main(["seek", "invalid"])
  421:         self.assertEqual(rc, 4)
  ```
  The unit tests mocked `cli._has_active_session = True`, creating an artificial environment that hid the real CLI failure where `_has_active_session()` evaluates to `False` on the command line.
- **Status**: **FAIL**.

---

### Test Scope 4: `seek` when no playback session active (valid target)
- **Command A (`seek +15`)**:
  ```bash
  ./bin/auto-speech seek +15
  ```
  - **Exit Code**: `2`
  - **Stderr**:
    ```text
    control: no active playback session (unified daemon uses synchronous audio).
    ```
- **Command B (`seek end`)**:
  ```bash
  ./bin/auto-speech seek end
  ```
  - **Exit Code**: `2`
  - **Stderr**:
    ```text
    control: no active playback session (unified daemon uses synchronous audio).
    ```
- **Status**: **PASS** (with valid target argument syntax, lack of active session correctly yields exit code 2).

---

### Test Scope 5: `autoplay mode` with invalid mode
- **Command A (`autoplay mode invalid_mode`)**:
  ```bash
  ./bin/auto-speech autoplay mode invalid_mode
  ```
  - **Exit Code**: `2`
  - **Stderr**:
    ```text
    autoplay-mode: unknown value invalid_mode (expected raw | verbatim | small | medium | large)
    ```
- **Command B (`autoplay mode ultra`)**:
  ```bash
  ./bin/auto-speech autoplay mode ultra
  ```
  - **Exit Code**: `2`
  - **Stderr**:
    ```text
    autoplay-mode: unknown value ultra (expected raw | verbatim | small | medium | large)
    ```
- **Status**: **PASS** (expected exit code 2).

---

### Test Scope 6: `autoplay scope` with invalid scope
- **Command A (`autoplay scope invalid_scope`)**:
  ```bash
  ./bin/auto-speech autoplay scope invalid_scope
  ```
  - **Exit Code**: `2`
  - **Stderr**:
    ```text
    scope: unknown value invalid_scope (expected all | solo)
    ```
- **Command B (`autoplay scope custom`)**:
  ```bash
  ./bin/auto-speech autoplay scope custom
  ```
  - **Exit Code**: `2`
  - **Stderr**:
    ```text
    scope: unknown value custom (expected all | solo)
    ```
- **Status**: **PASS** (expected exit code 2).

---

### Test Scope 7: `replay` with ordinal 0 or negative
- **Command A (`--ordinal 0`)**:
  ```bash
  ./bin/auto-speech replay --ordinal 0
  ```
  - **Exit Code**: `2`
  - **Stderr**:
    ```text
    replay: --ordinal must be >= 1, got 0
    ```
- **Command B (`--ordinal -1`)**:
  ```bash
  ./bin/auto-speech replay --ordinal -1
  ```
  - **Exit Code**: `2`
  - **Stderr**:
    ```text
    replay: --ordinal must be >= 1, got -1
    ```
- **Command C (`--ordinal foo`)**:
  ```bash
  ./bin/auto-speech replay --ordinal foo
  ```
  - **Exit Code**: `2`
  - **Stderr**:
    ```text
    usage: auto-speech replay [-h] [--ordinal ORDINAL] [--keep-artifacts]
    auto-speech replay: error: argument --ordinal: invalid int value: 'foo'
    ```
- **Status**: **PASS** (expected exit code 2).

---

### Test Scope 8: Piped stdin vs positional text args priority
- **Empirical Execution**:
  ```python
  echo "piped stdin content" | PYTHONPATH=plugin/scripts/python ./.venv/bin/python -c '
  import cli
  from unittest.mock import MagicMock
  from daemon_client import DaemonResponse

  client = MagicMock()
  client.speak.return_value = DaemonResponse(status="ok", action="speak")

  # Case A: Positional text provided while stdin is piped
  rc = cli.main(["speak", "positional", "override"], client=client)
  pos_text = client.speak.call_args[0][0]
  print(f"Case A (both present) -> speak received: {pos_text!r}")

  # Case B: Only stdin provided
  client.reset_mock()
  client.speak.return_value = DaemonResponse(status="ok", action="speak")
  rc = cli.main(["speak"], client=client)
  stdin_text = client.speak.call_args[0][0]
  print(f"Case B (stdin only) -> speak received: {stdin_text!r}")
  '
  ```
- **Output**:
  ```text
  Case A (both present) -> speak received: 'positional override'
  Case B (stdin only) -> speak received: 'piped stdin content\n'
  ```
- **Direct Code Inspection**: `cli.py` lines 112–115:
  ```python
  if args.words:
      text = " ".join(args.words)
  else:
      text = sys.stdin.read() if sys.stdin else ""
  ```
- **Status**: **PASS** (positional tokens take strict precedence over stdin; stdin is read only when positional words are empty).

---

### Additional Empirical Finding: Interactive Terminal Hang on `speak --source-hash`
- **Observed Behavior**:
  Executing `./bin/auto-speech speak --source-hash <hash>` in an interactive terminal (or unredirected subprocess) hung indefinitely.
  - Subprocess task ID `task-65` remained in `RUNNING` state indefinitely until cancelled.
  - Adding `< /dev/null` resolved immediately:
    ```bash
    ./bin/auto-speech speak --source-hash "AAA...AAA" < /dev/null
    ```
    exited immediately with code 1.
- **Root Cause**:
  In `cli.py` lines 112–118:
  ```python
  if args.words:
      text = " ".join(args.words)
  else:
      text = sys.stdin.read() if sys.stdin else ""

  if not text.strip():
      if args.source_hash:
          ...
  ```
  When `args.words` is empty, `cli.py` unconditionally calls `sys.stdin.read()`. In an interactive terminal, `sys.stdin.read()` blocks until EOF (Ctrl-D), preventing `speak --source-hash <hash>` from executing the cache lookup unless input is closed.
  - When `--source-hash` is provided and `sys.stdin.isatty()`, `sys.stdin.read()` should not block on terminal input.

---

## 2. Logic Chain

1. **Premise**: Per PROJECT.md §Interface Contracts and Dispatch Requirement 3 & 4:
   - Invalid target arguments (empty, non-number string like `foo`) must return exit code 4 (`EXIT_BAD_ARG`).
   - Valid target arguments executed when no active session exists must return exit code 2 (`EXIT_NO_SESSION`).
   - Missing required arguments (`auto-speech seek`) exit 2 (handled by argparse).
2. **Observation**: Executing `./bin/auto-speech seek foo` returned exit code 2 and printed `control: no active playback session (unified daemon uses synchronous audio).` (Observation §Test Scope 3).
3. **Trace**: In `plugin/scripts/python/cli.py` lines 296–308:
   `handle_seek` calls `_has_active_session()` on line 297, immediately returning `EXIT_NO_SESSION` (2) before reaching the target validation checks on lines 304–332.
4. **Impact**: Any invocation of `auto-speech seek <invalid>` in real-world CLI use exits with code 2 instead of the contractually required code 4.
5. **Masking Trace**: The unit tests in `tests/test_cli_dispatcher.py` passed because `_has_active_session` was patched to `True` using `mock.patch`, concealing the ordering bug from automated test runs.
6. **Remediation**: In `handle_seek`:
   - Validate `args.target` syntax first (`empty`, `end`, valid float prefix `+`/`-`/plain number). If invalid, print error and return `EXIT_BAD_ARG` (4).
   - Only after target syntax is validated, check `_has_active_session()`. If no session is active, print notice and return `EXIT_NO_SESSION` (2).
   - In `handle_speak`, avoid blocking on `sys.stdin.read()` if `args.source_hash` is present and `sys.stdin.isatty()`.

---

## 3. Caveats

- Testing of `auto-speech speak` with actual audio synthesis was run in dry/mocked and muted modes to avoid emitting disruptive neural speech on the host audio hardware.
- The full test suite `bash tests/run_all.sh` ran and passed all 50/50 test cases cleanly. The failure identified is specifically in the un-mocked CLI dispatch behavior of `bin/auto-speech seek <invalid_target>`.

---

## 4. Conclusion

**Verdict: REQUEST_CHANGES**

The CLI dispatcher implementation in `/Users/joshua/Developer/auto-speech-worktree` fails Scope 3 of the challenge:
1. `bin/auto-speech seek <invalid>` (`foo`, `""`, `+abc`) returns exit code `2` instead of `4` when no playback session is active.
2. `bin/auto-speech speak --source-hash <hash>` blocks indefinitely on standard input when run interactively in a terminal without redirected stdin.

**Actionable Fix Required**:
1. In `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/cli.py` inside `handle_seek`:
   Move the target string validation and float parsing before the `_has_active_session()` check so invalid arguments return `EXIT_BAD_ARG` (4) before session availability is checked.
2. In `handle_speak`:
   Only read from `sys.stdin` if `not sys.stdin.isatty()` or if `--source-hash` is not provided.
3. Update `tests/test_cli_dispatcher.py` to add integration tests asserting that `cli.main(["seek", "foo"])` returns `4` without patching `_has_active_session`.

---

## 5. Verification Method

To independently verify the bug and confirm the fix:

1. **Reproduce Bug**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   ./bin/auto-speech seek foo
   echo "Exit code: $?"
   # Expected per contract: 4
   # Actual observed: 2
   ```

2. **Verify Fix**:
   After reordering target validation in `handle_seek`:
   ```bash
   # Should exit 4
   ./bin/auto-speech seek foo
   [ $? -eq 4 ] && echo "PASS: seek foo returned 4"

   # Should exit 4
   ./bin/auto-speech seek ""
   [ $? -eq 4 ] && echo "PASS: seek empty returned 4"

   # Should exit 2
   ./bin/auto-speech seek +15
   [ $? -eq 2 ] && echo "PASS: seek +15 returned 2"

   # Run entire test suite
   bash tests/run_all.sh
   ```
