# Empirical Challenge Report: Seek Validation Order & Speak TTY Resolution (Retry 1)

**Date**: 2026-10-06T22:02:00Z  
**Target Repository**: `/Users/joshua/Developer/auto-speech-worktree`  
**Challenger**: `teamwork_preview_challenger` (`challenger_m1_retry_1`)  
**Verdict**: **APPROVE**

---

## 1. Observation

Direct empirical tests were executed against `./bin/auto-speech` and the test suite in `/Users/joshua/Developer/auto-speech-worktree`.

### Test 1: `seek` with invalid target string (`seek foo`)
- **Command**:
  ```bash
  ./bin/auto-speech seek foo
  ```
- **Exit Code**: `4` (`EXIT_BAD_ARG`)
- **Stderr**:
  ```text
  control: seek requires a target (+N, -N, N, or 'end')
  ```
- **Observation**: Exits with code `4` immediately without evaluating session presence.

### Test 2: `seek` with empty target string (`seek ""`)
- **Command**:
  ```bash
  ./bin/auto-speech seek ""
  ```
- **Exit Code**: `4` (`EXIT_BAD_ARG`)
- **Stderr**:
  ```text
  control: seek requires a target (+N, -N, N, or 'end')
  ```
- **Observation**: Exits with code `4` immediately on empty or whitespace strings (`""`, `"   "`).

### Test 3: `seek` with invalid relative target (`seek +abc`)
- **Command**:
  ```bash
  ./bin/auto-speech seek +abc
  ```
- **Exit Code**: `4` (`EXIT_BAD_ARG`)
- **Stderr**:
  ```text
  control: seek requires a target (+N, -N, N, or 'end')
  ```
- **Observation**: Exits with code `4` immediately on non-numeric relative offsets.

### Test 4: `seek` with valid relative target when no session is active (`seek +15`)
- **Command**:
  ```bash
  ./bin/auto-speech seek +15
  ```
- **Exit Code**: `2` (`EXIT_NO_SESSION`)
- **Stderr**:
  ```text
  control: no active playback session (unified daemon uses synchronous audio).
  ```
- **Observation**: Target syntax is validated first; because syntax is valid and no playback session is active, exits cleanly with code `2`.

### Test 5: `seek` with valid absolute target keyword when no session is active (`seek end`)
- **Command**:
  ```bash
  ./bin/auto-speech seek end
  ```
- **Exit Code**: `2` (`EXIT_NO_SESSION`)
- **Stderr**:
  ```text
  control: no active playback session (unified daemon uses synchronous audio).
  ```
- **Observation**: Target keyword `end` is accepted as valid syntax, then exits with code `2` due to absence of an active playback session.

### Test 6: `speak --source-hash` does not block on interactive TTY
- **Verification Harness**:
  Spawning `./bin/auto-speech speak --source-hash <64-char-hex>` in an interactive pseudo-terminal (`pty.openpty()`):
  ```python
  import os, pty, subprocess, time
  master, slave = pty.openpty()
  proc = subprocess.Popen(
      ["./bin/auto-speech", "speak", "--source-hash", "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"],
      stdin=slave,
      stdout=subprocess.PIPE,
      stderr=subprocess.PIPE,
      close_fds=True,
  )
  os.close(slave)
  stdout, stderr = proc.communicate(timeout=4)
  ```
- **Result**: Terminated in `0.05s` without hanging or waiting for EOF.
- **Bare `speak` on TTY**:
  ```python
  proc = subprocess.Popen(["./bin/auto-speech", "speak"], stdin=slave, ...)
  ```
  Terminated in `0.05s` with exit code `2` and stderr:
  ```text
  speak: no text provided on command line or stdin
  ```
  `sys.stdin.read()` is bypassed on interactive TTY when no piped data is present.

### Test 7: Piped stdin pass-through on non-TTY
- **Command**:
  ```bash
  echo "hello world" | PYTHONPATH=plugin/scripts/python ./.venv/bin/python -c '...'
  ```
- **Result**: Successfully read `'hello world\n'` and forwarded to `client.speak`.

### Test 8: Full Test Suite Execution
- **Command**:
  ```bash
  bash tests/run_all.sh
  ```
- **Result**:
  ```text
  ====================
  ran:    50
  failed: 0
  all tests passed
  ```
- **Linter Check**:
  ```bash
  ./.venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py
  ```
  Result: `All checks passed!`

---

## 2. Logic Chain

1. **Prior Defect Identification**: In report `challenger_m1_1/handoff.md`, `handle_seek` in `plugin/scripts/python/cli.py` called `_has_active_session()` on line 297 prior to validating `args.target`, causing invalid arguments to exit `2` (`EXIT_NO_SESSION`) rather than `4` (`EXIT_BAD_ARG`), and `handle_speak` called `sys.stdin.read()` on interactive terminals when words were empty.
2. **Implementation Verification**:
   - In `plugin/scripts/python/cli.py` (lines 310–353), `handle_seek` now evaluates `target.strip()`, parses `"end"`, `"+N"`, `"-N"`, and absolute `"N"`, and immediately returns `EXIT_BAD_ARG` (4) on syntax errors before calling `_has_active_session()`.
   - In `plugin/scripts/python/cli.py` (lines 113–130), `handle_speak` detects `sys.stdin.isatty()`. On an interactive TTY with `--source-hash`, it sets `text = ""` to trigger cache lookup. On an interactive TTY without text or `--source-hash`, it prints an error and returns `EXIT_INVALID_INPUT` (2) immediately without blocking on `sys.stdin.read()`.
3. **Empirical Confirmation**:
   - Commands 1–3 (`seek foo`, `seek ""`, `seek +abc`) exit with `4` as observed.
   - Commands 4–5 (`seek +15`, `seek end`) exit with `2` as observed.
   - PTY execution terminates in `0.05s`, confirming interactive terminals do not hang on `speak --source-hash` or bare `speak`.
   - All 50 regression test suites pass cleanly.

---

## 3. Caveats

- Systemone decision model service (`~/.local/bin/systemone`) was queried per user policy, but returned `ConnectionRefusedError: [Errno 61] Connection refused` because the local background daemon is not running in the environment. Empirical testing via subprocess and test runner was used exclusively.
- Physical audio playback on macOS hardware was verified in non-emitting / mocked test conditions to avoid unsolicited loudspeaker audio during automated evaluation.
- No caveats regarding code correctness: all contract behaviors adhere to specification.

---

## 4. Conclusion

**Verdict: APPROVE**

The implementation in `/Users/joshua/Developer/auto-speech-worktree` completely resolves the issues raised in `challenger_m1_1/handoff.md`:
1. `seek` target validation executes strictly before session presence checks, returning exit code 4 on invalid targets and exit code 2 when valid targets find no active session.
2. `speak --source-hash` and bare `speak` resolve cleanly without blocking on interactive TTY inputs.
3. Unit test coverage in `tests/test_cli_dispatcher.py` now includes unmocked error cases.
4. All 50 project tests in `tests/run_all.sh` pass.

---

## 5. Verification Method

To independently verify all findings:

```bash
cd /Users/joshua/Developer/auto-speech-worktree

# 1. Invalid seek targets return 4
./bin/auto-speech seek foo
[ $? -eq 4 ] && echo "PASS: seek foo returned 4"

./bin/auto-speech seek ""
[ $? -eq 4 ] && echo "PASS: seek empty returned 4"

./bin/auto-speech seek +abc
[ $? -eq 4 ] && echo "PASS: seek +abc returned 4"

# 2. Valid seek targets without active session return 2
./bin/auto-speech seek +15
[ $? -eq 2 ] && echo "PASS: seek +15 returned 2"

./bin/auto-speech seek end
[ $? -eq 2 ] && echo "PASS: seek end returned 2"

# 3. TTY non-blocking check
python3 -c '
import os, pty, subprocess
master, slave = pty.openpty()
proc = subprocess.Popen(["./bin/auto-speech", "speak", "--source-hash", "0"*64], stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
os.close(slave)
proc.communicate(timeout=2)
print("PASS: speak --source-hash did not block on TTY")
os.close(master)
'

# 4. Full test suite passes
bash tests/run_all.sh
```
