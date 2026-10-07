# Review & Handoff Report: Milestone M1 CLI Dispatcher, Installer Integration & Remediation Verification

**Agent:** `reviewer_m1_retry_2` (teamwork_preview_reviewer / critic)  
**Date:** 2026-10-06T22:02:30Z  
**Target Repository / Worktree:** `/Users/joshua/Developer/auto-speech-worktree`  
**Working Directory:** `/Users/joshua/Developer/auto-speech/.agents/teamwork/reviewer_m1_retry_2`  
**Verdict:** **APPROVE**

---

## 1. Observation

Direct empirical invocations and code examinations conducted inside `/Users/joshua/Developer/auto-speech-worktree`:

### 1.1 Mandatory Verification Invocations
1. **`bash tests/test_install_plugin.sh`**:
   - Exit code: `0`
   - Verbatim output:
     ```text
       ok  curated install links exactly 8 commands
       ok  root file recorded with the clone path
       ok  curated install links ~/.local/bin/auto-speech
       ok  doctor block resolves root from ~/.config/auto-speech/root
       ok  guard fires when root file is absent
       ok  --with-extras links 21 commands
       ok  plain re-run preserves opted-in extras
       ok  prune removes non-keep-set links into plugin/commands/
       ok  prune removes dangling links into plugin/commands-extra/
       ok  uninstall removes the recorded root file
       ok  uninstall removes ~/.local/bin/auto-speech symlink
       ok  no hardcoded absolute project paths in command files

     install-plugin: 12/12 passed
     ```

2. **`bin/auto-speech --help`**:
   - Exit code: `0`
   - Verbatim output:
     ```text
     usage: auto-speech [-h] [--version] <subcommand> ...

     Unified CLI dispatcher for auto-speech: speech synthesis, playback transport, session management, and diagnostics.

     positional arguments:
       <subcommand>  Subcommand to execute. Run 'auto-speech <subcommand> --help'
                     for details.
         speak       Synthesize and play speech.
         replay      Replay recent cached speech.
         pause       Pause active mpv audio playback.
         resume      Resume paused mpv audio playback.
         restart     Restart active mpv audio playback from start.
         stop        Stop playback immediately and purge pending queue.
         seek        Seek active playback (+N, -N, N, or 'end').
         autoplay    Autoplay subsystem controls.
         narrate     Narration subsystem controls.
         hook        Lifecycle hook execution.
         doctor      System diagnostic health check.
         update      Update and synchronize virtual environment dependencies.

     options:
       -h, --help    show this help message and exit
       --version     show program's version number and exit
     ```

3. **`~/.local/bin/auto-speech --help`**:
   - Exit code: `0`
   - Verified symlink target: `/Users/joshua/.local/bin/auto-speech -> /Users/joshua/Developer/auto-speech-worktree/bin/auto-speech`
   - Produced identical 12-subcommand synopsis as above.

4. **`bin/auto-speech --version`**:
   - Exit code: `0`
   - Verbatim output:
     ```text
     auto-speech 0.2.0
     ```

### 1.2 Verification of `worker_m1_fix` Bugfixes
1. **Seek Target Validation & Exit Code Ordering**:
   - Invocation: `./bin/auto-speech seek foo`
     - Stderr: `control: seek requires a target (+N, -N, N, or 'end')`
     - Exit code: `4` (matches `EXIT_BAD_ARG = 4`).
   - Invocation: `./bin/auto-speech seek ""`
     - Stderr: `control: seek requires a target (+N, -N, N, or 'end')`
     - Exit code: `4`.
   - Invocation: `./bin/auto-speech seek +15`
     - Stderr: `control: no active playback session (unified daemon uses synchronous audio).`
     - Exit code: `2` (matches `EXIT_NO_SESSION = 2`).
   - Invocation: `./bin/auto-speech seek end`
     - Stderr: `control: no active playback session (unified daemon uses synchronous audio).`
     - Exit code: `2`.
   - Invocation: `./bin/auto-speech seek +nan` -> Exit code `4`.
   - Invocation: `./bin/auto-speech seek -- -inf` -> Exit code `4`.
   - Invocation: `./bin/auto-speech seek -- -10` -> Exit code `2`.

2. **Speak TTY Stdin Blocking Prevention**:
   - Invocation via Python PTY (simulating interactive terminal stdin):
     - `./bin/auto-speech speak` without arguments exited immediately with code `2` in <0.05s without hanging.
     - `./bin/auto-speech speak --source-hash <64_hex_chars>` on PTY without input bypassed stdin read and attempted cache playback without hanging.
   - Piped stdin behavior: `echo "test" | ./bin/auto-speech speak` reads piped text normally.

3. **Hook Non-Dict JSON Sanitization**:
   - Invocations tested directly from shell:
     - `echo "[1, 2, 3]" | ./bin/auto-speech hook stop` -> Exit code `0`.
     - `echo "[1, 2, 3]" | ./bin/auto-speech hook narrator` -> Exit code `0`.
     - `echo "null" | ./bin/auto-speech hook stop` -> Exit code `0`.
     - `echo "42" | ./bin/auto-speech hook stop` -> Exit code `0`.
     - `echo "\"foo\"" | ./bin/auto-speech hook stop` -> Exit code `0`.
     - `echo "true" | ./bin/auto-speech hook stop` -> Exit code `0`.
   - Zero `AttributeError` or unhandled exceptions occurred.

### 1.3 Test Suite & Code Quality Results
1. **Expanded Unit Tests**:
   - Command: `.venv/bin/python -m unittest tests/test_cli_dispatcher.py`
   - Result: `Ran 84 tests in 0.784s` -> `OK` (all 84 passed, 0 failures, 0 errors).
2. **Full Regression Suite**:
   - Command: `bash tests/run_all.sh`
   - Result: `ran: 50, failed: 0, all tests passed`.
3. **Linter & Code Quality**:
   - Command: `.venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py setup/`
   - Result: `All checks passed!` (0 lint errors).

### 1.4 Integrity & Anti-Cheat Audit
- Verified `plugin/scripts/python/cli.py`, `bin/auto-speech`, and `tests/test_cli_dispatcher.py`.
- No hardcoded test outputs or mock bypasses in production code.
- No facade or dummy stubs. Full logic delegates to `DaemonClient`, `MpvIpc`, `CacheStore`, `Doctor`, and `SessionEnrollment`.
- No fabricated verification artifacts.

---

## 2. Logic Chain

1. **Trampoline Portability (`bin/auto-speech`)**:
   - Uses `set -euo pipefail`.
   - Resolves symlinks safely with a maximum hop limit of 20 (`MAX_HOPS=20`), correctly handling relative link destinations (`[[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"`).
   - Dynamically determines `PROJECT_ROOT` without baking user-specific absolute paths into tracked scripts.
   - Replaces bash process via `exec "$VENV_PYTHON" "$CLI_PY" "$@"`, propagating UNIX process signals and exit codes directly.
   - Verified via `tests/test_install_plugin.sh` and direct CLI executions (`bin/auto-speech` and `~/.local/bin/auto-speech`).

2. **Exit Code & Contract Consistency (`cli.py`)**:
   - `handle_seek` validates argument tokens before checking hardware/IPC session state. Syntactically invalid offsets (`foo`, `+abc`, `nan`, `inf`, empty string) trigger `EXIT_BAD_ARG` (`4`). Valid offsets with an inactive session trigger `EXIT_NO_SESSION` (`2`). This satisfies the contract defined in `PROJECT.md §Interface Contracts`.
   - `handle_speak` uses `sys.stdin.isatty()` to distinguish terminal typing from Unix pipelines. On a TTY without positional text, `--source-hash` immediately routes to cache/daemon playback without hanging on EOF, while missing text fails fast with code `2`. Piped stdin (`echo ... | auto-speech speak`) continues to read stdin cleanly.
   - `handle_hook` sanitizes parsed JSON structures. If the payload parsed is not an instance of `dict` (e.g. JSON arrays, integers, strings, booleans, or null), it falls back to `{}` safely, extracting strings without `AttributeError` crashes and exiting `0`.

3. **Installer & Uninstaller Safety (`setup/install-plugin.sh`, `setup/uninstall.sh`)**:
   - `install-plugin.sh` creates the `~/.local/bin/auto-speech` symlink idempotently and refuses to overwrite non-symlink user files.
   - `uninstall.sh` removes the CLI symlink only if it points to `$PROJECT_ROOT/*`, avoiding accidental deletion of third-party user files.
   - All 12 assertions in `test_install_plugin.sh` pass cleanly.

---

## 3. Caveats

1. **Global Linter Scope**:
   - Running `ruff check .` across the entire legacy repository reports 67 `E402` import ordering warnings in legacy test files (due to `sys.path.insert(0, ...)`). All Milestone M1 production files (`cli.py`, `test_cli_dispatcher.py`, `setup/*`) are completely clean with 0 ruff errors.
2. **Post-Merge Symlink Repoint**:
   - `~/.local/bin/auto-speech` currently points to the isolated worktree (`/Users/joshua/Developer/auto-speech-worktree/bin/auto-speech`). Once Milestone M5 finishes and merges back into `main`, running `bash setup/install-plugin.sh` on `main` will repoint the symlink to `/Users/joshua/Developer/auto-speech/bin/auto-speech`.

---

## 4. Conclusion

**Verdict: APPROVE**

Milestone M1 satisfies all requirements set forth in `ORIGINAL_REQUEST.md` (§R1, §R4) and `PROJECT.md`:
- The CLI dispatcher is fully functional, portable, and cleanly integrated on `PATH`.
- All 12 subcommands and exit codes conform strictly to documented interface contracts.
- All 3 defect remediation items implemented by `worker_m1_fix` have been empirically validated.
- All 84 unit tests and 50 regression test suites pass 100% green with 0 errors.

---

## 5. Verification Method

To independently reproduce and verify this assessment in `/Users/joshua/Developer/auto-speech-worktree`:

1. **Verify Installer Integration**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   bash tests/test_install_plugin.sh
   # Expected: install-plugin: 12/12 passed (exit 0)
   ```

2. **Verify CLI Dispatcher & Version**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   bin/auto-speech --help
   ~/.local/bin/auto-speech --help
   bin/auto-speech --version
   # Expected: all exit 0, displaying version "auto-speech 0.2.0"
   ```

3. **Verify Remediated Seek Exit Codes**:
   ```bash
   ./bin/auto-speech seek foo      # Exit code: 4
   ./bin/auto-speech seek +15      # Exit code: 2
   ./bin/auto-speech seek end      # Exit code: 2
   ```

4. **Verify Remediated Hook JSON Robustness**:
   ```bash
   echo "[1, 2, 3]" | ./bin/auto-speech hook stop        # Exit code: 0
   echo "[1, 2, 3]" | ./bin/auto-speech hook narrator    # Exit code: 0
   ```

5. **Run Full Test Suites**:
   ```bash
   .venv/bin/python -m unittest tests/test_cli_dispatcher.py   # 84 tests, OK
   bash tests/run_all.sh                                       # 50 suites, 0 failed
   .venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py setup/
   ```
