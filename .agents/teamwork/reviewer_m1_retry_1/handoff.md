# Reviewer & Adversarial Critic Handoff Report: Milestone 1 Bugfix Remediation

**Reviewer**: `teamwork_preview_reviewer` (`reviewer_m1_retry_1`)  
**Target Repository**: `/Users/joshua/Developer/auto-speech-worktree`  
**Date**: 2026-10-06T22:02:00Z  
**Verdict**: **APPROVE**  

---

## Review Summary

**Verdict**: **APPROVE**  
The remediation worker (`worker_m1_fix`) has correctly, robustly, and genuinely addressed all 3 reported defects in `plugin/scripts/python/cli.py` and expanded `tests/test_cli_dispatcher.py` to 84 tests. All empirical test suites, full regression test suites, and linter checks pass with zero failures and zero violations. No integrity violations, shortcuts, dummy facades, or hardcoded test cheats were detected.

---

## 1. Observation

### 1.1 Source Code Inspections (`plugin/scripts/python/cli.py`)
1. **Seek Target Validation Reordering (`cli.py:310-360`)**:
   - `handle_seek` inspects and validates `target` before evaluating `_has_active_session()`:
     ```python
     310: def handle_seek(args: argparse.Namespace) -> int:
     311:     target = getattr(args, "target", "")
     312:     target = target.strip() if isinstance(target, str) else ""
     313:     if not target:
     314:         print("control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
     315:         return EXIT_BAD_ARG
     ```
   - Validates `math.isfinite(val)` for relative and absolute numeric targets; invalid targets return `EXIT_BAD_ARG` (`4`) immediately with diagnostic message on `sys.stderr`.
   - Only after syntax validation succeeds does it check `_has_active_session()`, returning `EXIT_NO_SESSION` (`2`) if no playback session is alive.

2. **Speak TTY Detection & Non-Blocking Stdin (`cli.py:113-130`)**:
   - `handle_speak` queries `sys.stdin.isatty()`:
     ```python
     113:     is_tty = False
     114:     if sys.stdin is not None:
     115:         try:
     116:             is_tty = bool(sys.stdin.isatty())
     117:         except (AttributeError, ValueError, OSError):
     118:             is_tty = False
     119: 
     120:     if args.words:
     121:         text = " ".join(args.words)
     122:     elif is_tty:
     123:         if args.source_hash:
     124:             text = ""
     125:         else:
     126:             print("speak: no text provided on command line or stdin", file=sys.stderr)
     127:             return EXIT_INVALID_INPUT
     128:     else:
     129:         text = sys.stdin.read() if sys.stdin else ""
     ```
   - On interactive terminals (`is_tty=True`), supplying `--source-hash` defaults `text = ""` without calling `sys.stdin.read()`, routing immediately to cache lookup without blocking. Supplying no words and no `--source-hash` fails fast with `EXIT_INVALID_INPUT` (`2`).

3. **Hook Non-Dict JSON Sanitization (`cli.py:643-657, 704-718`)**:
   - In `hook stop` and `hook narrator`:
     ```python
     644:         try:
     645:             payload = json.loads(payload_raw) if payload_raw.strip() else {}
     646:         except Exception:
     647:             payload = {}
     648:         if not isinstance(payload, dict):
     649:             payload = {}
     ```
   - After parsing, `if not isinstance(payload, dict): payload = {}` guarantees payload is a dictionary, preventing `AttributeError: 'list' object has no attribute 'get'` when non-dict JSON primitives/arrays (`[1, 2, 3]`, `42`, `null`, `"foo"`, `true`) are piped to stdin. Keys are coerced to string safely.

### 1.2 Empirical Command Executions in `/Users/joshua/Developer/auto-speech-worktree`

1. **Unit Test Suite Execution**:
   - Command: `.venv/bin/python -m unittest tests/test_cli_dispatcher.py`
   - Result: Exit code 0.
   - Verbatim Output:
     ```
     Ran 84 tests in 0.802s
     OK
     ```
2. **Full Project Regression Test Suite**:
   - Command: `bash tests/run_all.sh`
   - Result: Exit code 0.
   - Verbatim Output:
     ```
     ====================
     ran:    50
     failed: 0
     all tests passed
     ```
3. **Linter Check**:
   - Command: `.venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py`
   - Result: Exit code 0.
   - Verbatim Output: `All checks passed!`

4. **Direct CLI Smoke Executions**:
   - Command: `./bin/auto-speech seek foo`
     - Exit code: 4 (`EXIT_BAD_ARG`)
     - Stderr: `control: seek requires a target (+N, -N, N, or 'end')`
   - Command: `./bin/auto-speech seek +10`
     - Exit code: 2 (`EXIT_NO_SESSION`)
     - Stderr: `control: no active playback session (unified daemon uses synchronous audio).`
   - Command: `./bin/auto-speech seek`
     - Exit code: 2 (`argparse` missing argument error)
   - Command: `echo "[1, 2, 3]" | ./bin/auto-speech hook stop`
     - Exit code: 0, no traceback.
   - Command: `echo "[1, 2, 3]" | ./bin/auto-speech hook narrator`
     - Exit code: 0, no traceback.
   - Command: `./bin/auto-speech speak </dev/null`
     - Exit code: 2 (`EXIT_INVALID_INPUT`), exits immediately.

---

## 2. Logic Chain

1. **Interface Contract Conformance**: `PROJECT.md §Interface Contracts` specifies exit code `4` for invalid target arguments and exit code `2` for missing target / no active session. By reordering the validation in `handle_seek` (Obs 1.1.1, Obs 1.2.4), syntax errors like `foo`, `+abc`, `nan` produce exit code `4` independent of session state, satisfying the contract.
2. **Interactive TTY Protection**: `handle_speak` reads standard input only when `stdin.isatty()` is False or when no source hash is provided on interactive terminals (Obs 1.1.2). When `--source-hash` is supplied in an interactive session, `text` is treated as empty and routed directly to cache lookup/playback without awaiting EOF. This eliminates terminal freezes while preserving Unix pipe workflows (`cat prompt.txt | auto-speech speak`).
3. **Hook Robustness**: External agents and hooks pipe heterogeneous JSON into standard input. By asserting `isinstance(payload, dict)` and converting values to strings (Obs 1.1.3, Obs 1.2.4), unexpected payloads (lists, scalars, nulls) safely degrade to `{}` without raising unhandled exceptions or breaking hook lifecycles.
4. **Integrity and Non-Regression**: The 84 unit tests in `tests/test_cli_dispatcher.py` provide thorough coverage of all 12 subcommands, transport controls, and error paths. All 50 project test suites in `tests/run_all.sh` pass with zero failures (Obs 1.2.2), demonstrating that existing system invariants remain intact.

---

## 3. Adversarial Review & Integrity Audit

### 3.1 Integrity Violation Check
- **Hardcoded test results embedded in source code**: None. Grep for test-specific branch conditions in `cli.py` yielded zero matches.
- **Dummy or facade implementations**: None. Subcommands call into genuine backend components (`AutoplayGate`, `SessionEnrollment`, `SoloScope`, `CacheStore`, `DaemonClient`, `NativeAudioSink`, `MpvIpc`, `Doctor`).
- **Shortcuts bypassing the task**: None. All 12 subcommands are implemented in pure Python with clean argument parsing and subprocess trampoline integration.
- **Fabricated verification outputs**: None. All commands were empirically executed in real time in the worktree.

### 3.2 Adversarial Stress Tests & Counter-Examples
| Attack Scenario | Tested Input / Condition | Expected Behavior | Actual Behavior | Result |
|---|---|---|---|---|
| Non-finite seek target | `seek nan`, `seek +inf` | Exit code 4, no crash | Exit code 4 (`EXIT_BAD_ARG`) | PASS |
| Invalid seek prefix | `seek +abc`, `seek -xyz` | Exit code 4 | Exit code 4 (`EXIT_BAD_ARG`) | PASS |
| Non-dict JSON in stop hook | `[1, 2, 3]`, `42`, `"str"`, `null`, `true` | Exit code 0, no exception | Exit code 0, no exception | PASS |
| Non-dict JSON in narrator hook | `[1, 2, 3]`, `42`, `"str"`, `null`, `true` | Exit code 0, no exception | Exit code 0, no exception | PASS |
| Interactive TTY without words | `sys.stdin.isatty() == True` without hash | Exit code 2, no hang | Exit code 2 fast | PASS |
| Piped non-TTY with text and hash | Non-TTY text + `--source-hash` | Uses piped text | Uses piped text | PASS |
| Trampoline resolution | `bin/auto-speech --help` | Resolves root & venv | Exits 0 with help synopsis | PASS |

---

## 4. Caveats

- **Positional Arg Absence in Seek**: Calling `./bin/auto-speech seek` with zero arguments is trapped by Python's `argparse` parser before reaching `handle_seek`, yielding exit code `2`. This matches `PROJECT.md` line 91 (`2: No active playback session / invalid input / missing target`).
- **Narrator Ambient Environment**: When `CLAUDE_CODE_SESSION_ID` is present in the ambient environment and points to an enrolled session, piping non-dict JSON into `hook narrator` will log an event with `"event": "unknown"` and `"payload": {}`. This is intentional defensive fallback behavior.

---

## 5. Conclusion

The Milestone 1 bugfix changes in `/Users/joshua/Developer/auto-speech-worktree` are verified, complete, resilient, and compliant with all project and architectural requirements.

**Verdict**: **APPROVE**

---

## 6. Verification Method

To reproduce and independently verify this evaluation in `/Users/joshua/Developer/auto-speech-worktree`:

1. **Verify Unit Test Suite (84 tests)**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/python -m unittest tests/test_cli_dispatcher.py
   ```
2. **Verify Full Regression Suite (50 suites)**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   bash tests/run_all.sh
   ```
3. **Verify Ruff Code Quality (0 errors)**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py
   ```
4. **Smoke-Test Seek Syntax vs Session**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   ./bin/auto-speech seek foo   # Expected exit: 4
   ./bin/auto-speech seek +10   # Expected exit: 2
   ```
5. **Smoke-Test Hook Non-Dict JSON Resilience**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   echo "[1, 2, 3]" | ./bin/auto-speech hook stop       # Expected exit: 0
   echo "[1, 2, 3]" | ./bin/auto-speech hook narrator   # Expected exit: 0
   ```
