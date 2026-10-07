# Forensic Audit Report: Milestone M1 (Unified auto-speech CLI Dispatcher)

**Work Product**: `/Users/joshua/Developer/auto-speech-worktree` (Milestone M1)  
**Profile**: General Project  
**Integrity Mode**: `development` (per `ORIGINAL_REQUEST.md` under `## 2026-10-06T20:47:50Z`)  
**Auditor**: `teamwork_preview_auditor` (`auditor_m1_1`)  
**Date**: 2026-10-06T21:40:00Z  
**Verdict**: **CLEAN**

---

### Phase Results

- **Git Diff & Scope Compliance**: **PASS** — Changes in worktree correspond strictly to Milestone M1 scope (`bin/auto-speech`, `plugin/scripts/python/cli.py`, `tests/test_cli_dispatcher.py`, `setup/install-plugin.sh`, `setup/uninstall.sh`, `tests/test_install_plugin.sh`, `tests/run_all.sh`).
- **Static Analysis (Hardcoded Output / Bypass Detection)**: **PASS** — Zero hardcoded expected return strings, zero canned outputs in production code.
- **Facade Detection & Genuine Subsystem Integration**: **PASS** — All 12 subcommands in `cli.py` (`speak`, `replay`, `pause`, `resume`, `restart`, `stop`, `seek`, `autoplay`, `narrate`, `hook`, `doctor`, `update`) are genuine computational logic integrating directly with backend modules (`DaemonClient`, `NativeAudioSink`, `MpvIpc`, `CacheStore`, `SessionEnrollment`, `SoloScope`, `Doctor`, `needs_sync`).
- **Production Mock Bypass Check**: **PASS** — Zero mock references, zero test harnesses, and zero stub decorators in `cli.py`.
- **Defensive Incompleteness & Error Propagation**: **PASS** — Standard exit codes (0, 1, 2, 3, 4, 6, 130) returned with descriptive diagnostics on stderr; invalid arguments and missing session markers rejected deterministically.
- **Test Integrity & Tautology Check**: **PASS** — `tests/test_cli_dispatcher.py` comprises 74 unit/integration tests verifying concrete status codes, stderr diagnostics, argument passing to subsystems, and real filesystem mutations. `test_bin_trampoline_help_invocation` executes the live `bin/auto-speech` bash script via subprocess.
- **Empirical Execution & Lint Verification**: **PASS** — Full test suite `bash tests/run_all.sh` executed cleanly (50 test suites, 0 failures); `tests/test_cli_dispatcher.py` (74/74 passed); `tests/test_install_plugin.sh` (12/12 passed); `ruff check` reports 0 errors.
- **Symlink & PATH Integration**: **PASS** — `~/.local/bin/auto-speech` symlink resolves to `/Users/joshua/Developer/auto-speech-worktree/bin/auto-speech` and functions identically to direct script invocation; multi-hop symlink resolution verified.

---

## 1. Observation

1. **Git Worktree Status and Diff**:
   Running `git -C /Users/joshua/Developer/auto-speech-worktree status`:
   ```
   On branch worktree/cli-mcp-parity
   Changes not staged for commit:
     modified:   setup/install-plugin.sh
     modified:   setup/uninstall.sh
     modified:   tests/run_all.sh
     modified:   tests/test_install_plugin.sh

   Untracked files:
     .venv
     bin/
     plugin/scripts/python/cli.py
     tests/test_cli_dispatcher.py
   ```

   Running `git -C /Users/joshua/Developer/auto-speech-worktree diff main`:
   - `setup/install-plugin.sh`: Added `install_cli_symlink()` linking `$PROJECT_ROOT/bin/auto-speech -> $HOME/.local/bin/auto-speech`.
   - `setup/uninstall.sh`: Added uninstaller cleanup targeting `$HOME/.local/bin/auto-speech` only when pointing into `$PROJECT_ROOT/*`.
   - `tests/run_all.sh`: Appended `test_cli_dispatcher.py` to the `CHEAP` test suite array.
   - `tests/test_install_plugin.sh`: Added assertions verifying symlink creation, uninstallation cleanup, and portability invariant coverage of `bin/`.

2. **Trampoline Script (`bin/auto-speech`)**:
   - Implements `set -euo pipefail`.
   - Unwinds symlinks iteratively using `readlink` with a loop guard (`MAX_HOPS=20`).
   - Resolves true project root directory dynamically (`PROJECT_ROOT="$(cd -P "$BIN_DIR/.." && pwd)"`).
   - Verifies `$PROJECT_ROOT/.venv/bin/python` is executable.
   - Verifies `$PROJECT_ROOT/plugin/scripts/python/cli.py` exists.
   - Prepends `$PROJECT_ROOT/plugin/scripts/python` to `PYTHONPATH`.
   - Replaces the shell process via `exec "$VENV_PYTHON" "$CLI_PY" "$@"`.
   - Portability check confirmed: Zero hardcoded user paths (`/Users/joshua`).

3. **Core Python Dispatcher (`plugin/scripts/python/cli.py`)**:
   - File length: 1,051 lines.
   - Contains zero imports of `unittest`, `mock`, or test libraries.
   - Implements 12 distinct subcommands:
     - `speak`: Checks `AutoplayGate().worker_gated_off()`; validates 64-character hex `--source-hash`; reads positional text or stdin; routes speech to `DaemonClient.speak()` with fallback to `send_speech_request()`.
     - `replay`: Queries `CacheStore.list_by_recency()`; validates 1-indexed `--ordinal`; verifies mute gate; triggers cached playback via `DaemonClient.play_cache()` or `NativeAudioSink().play()`.
     - `pause`, `resume`, `restart`: Evaluates active session liveness via `_has_active_session()`; dispatches via `MpvIpc.send()`; returns exit code 2 when no playback is active, exit code 3 on IPC errors.
     - `seek`: Validates target argument syntax (`+N`, `-N`, `N`, `end`); returns exit code 4 on invalid target; sends seek commands via `MpvIpc`.
     - `stop`: Halts playback across `MpvIpc`, daemon interrupt (`DaemonClient.interrupt()`), and active PID cleanup (`/tmp/auto-speech-mpv.pid`).
     - `autoplay`: Manages session enrollment via `SessionEnrollment()`, solo spotlight via `SoloScope()`, and persistent config via `~/.config/auto-speech/autoplay.toml`.
     - `narrate`: Manages session markers in `~/.claude/auto-speech-narrate-sessions/`; checks daemon liveness; stops daemon via `narrator_service_stop.sh`; inspects/edits `narrator.toml`.
     - `hook`: Handles `stop` (verifies gate, solo scope, enrollment; spawns `autoplay_worker.sh`), `narrator` (appends structured events to `/tmp/auto-speech-narrator-events.jsonl`), and `bootstrap` (`needs_sync()` check, runs `bootstrap.sh`).
     - `doctor`: Instantiates `Doctor(max_queue_depth=...)` and renders human text or structured JSON (`--json`).
     - `update`: Triggers `bootstrap.sh` with `--force` support.

4. **Unit and Integration Test Suite (`tests/test_cli_dispatcher.py`)**:
   - File length: 862 lines across 9 test classes and 74 test methods.
   - Employs hermetic `BaseCliTestCase` providing temporary sandbox `HOME` and `XDG_CONFIG_HOME`.
   - Tests assert specific exit codes (`0`, `1`, `2`, `4`, `6`, `130`), verbatim error diagnostics on `sys.stderr`, exact stdout payloads, and mock argument routing.
   - `TestCliTrampolineIntegration.test_bin_trampoline_help_invocation` executes the live `bin/auto-speech` bash trampoline via `subprocess.run()`.

5. **Empirical Verification Runs**:
   - `tests/test_cli_dispatcher.py`:
     ```
     Ran 74 tests in 0.207s
     OK
     ```
   - `bash tests/test_install_plugin.sh`:
     ```
     install-plugin: 12/12 passed
     ```
   - `bash tests/run_all.sh`:
     ```
     ran:    50
     failed: 0
     all tests passed
     ```
   - `.venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py`:
     ```
     All checks passed!
     ```

6. **Direct CLI Invocations and Stress Testing**:
   - `bin/auto-speech --help`: Exited 0 with full usage description and all 12 subcommands.
   - `bin/auto-speech --version`: Exited 0 with `auto-speech 0.2.0`.
   - `~/.local/bin/auto-speech --help`: Exited 0; verified active symlink resolution.
   - `bin/auto-speech doctor`: Exited 0; evaluated 13 live system probes.
   - `bin/auto-speech doctor --json`: Exited 0; produced valid JSON matching `HealthReport` schema.
   - `bin/auto-speech speak < /dev/null`: Exited 2 with `"speak: no text provided on command line or stdin"`.
   - `bin/auto-speech speak --source-hash 123 < /dev/null`: Exited 2 with `"speak: --source-hash must be 64 hex chars, got '123'"`.
   - `bin/auto-speech pause`: Exited 2 with `"control: no active playback session (unified daemon uses synchronous audio)."`.
   - `bin/auto-speech seek +abc`: Exited 2 without active session; exited 4 (`"control: seek requires a target (+N, -N, N, or 'end')"`) when active session mocked.
   - `bin/auto-speech replay --ordinal 0`: Exited 2 with `"replay: --ordinal must be >= 1, got 0"`.
   - `bin/auto-speech autoplay mode badmode`: Exited 2 with `"autoplay-mode: unknown value badmode"`.
   - Chained symlinks: Multi-hop symlink (`step2 -> step1 -> bin/auto-speech`) resolved cleanly and exited 0.

---

## 2. Logic Chain

1. **Absence of Mock / Dummy Facades in Production Code**:
   - As observed in §1.3, `cli.py` contains no `unittest.mock` imports or stub shortcuts.
   - Every subcommand delegates directly to the existing production modules (`DaemonClient`, `NativeAudioSink`, `MpvIpc`, `CacheStore`, `SessionEnrollment`, `Doctor`).
   - Defensive exception handling logs to stderr and returns explicit error codes (e.g. `send_speech_request` fallback on daemon failure, `AudioSinkError` -> exit 6, IPC error -> exit 3).
   - Therefore, the implementation is 100% authentic production code without dummy facades.

2. **Test Integrity and Absence of Tautological Checks**:
   - As observed in §1.4, `tests/test_cli_dispatcher.py` tests verify specific failure modes and boundary conditions.
   - Exit codes are checked against contract constants (0, 1, 2, 4, 6, 130).
   - Mocks in tests are used strictly at system boundaries (`DaemonClient`, `MpvIpc`, `subprocess.Popen`) to verify that the CLI parser translates user arguments into the exact required method calls.
   - `test_bin_trampoline_help_invocation` executes the live trampoline script on disk without mocking, proving that bash path resolution and Python environment invocation work together end-to-end.
   - Therefore, the tests are genuine, rigorous, and free from self-certifying tautologies.

3. **Defensive Incompleteness and Error Handling**:
   - As observed in §1.6, missing or invalid inputs are rejected deterministically before performing side effects.
   - No mock successes are returned when underlying operations fail.
   - Therefore, the work product adheres to the defensive incompleteness requirement.

4. **Portability and System Coherence**:
   - As observed in §1.1 and §1.2, `bin/auto-speech` uses dynamic directory resolution rather than hardcoded absolute paths.
   - `tests/test_install_plugin.sh` portably validates installation into sandbox environments.
   - `~/.local/bin/auto-speech` symlink correctly delegates to the worktree during development.

---

## 3. Caveats

- **User Symlink Location**: `~/.local/bin/auto-speech` currently points to `/Users/joshua/Developer/auto-speech-worktree/bin/auto-speech`. During Milestone M5, once the worktree is merged to `main` and deleted, running `setup/install-plugin.sh` in the main repo will repoint this link to `/Users/joshua/Developer/auto-speech/bin/auto-speech`.
- **SystemOne Model Daemon**: The local `systemone` daemon was offline (`ConnectionRefusedError: [Errno 61]`), so automated prompt classification was not executed; all decisions were verified empirically via live test executions and static analysis.

---

## 4. Conclusion

Milestone M1 (Unified auto-speech CLI Dispatcher) in `/Users/joshua/Developer/auto-speech-worktree` passes all forensic integrity checks with zero violations:
1. `bin/auto-speech` is a robust, portable bash trampoline with multi-hop symlink resolution.
2. `plugin/scripts/python/cli.py` is a genuine, comprehensive router implementing all 12 subcommands with standard exit codes and direct integration with core subsystems.
3. `tests/test_cli_dispatcher.py` provides exhaustive, non-tautological test coverage (74 tests).
4. `setup/install-plugin.sh` and `setup/uninstall.sh` safely and idempotently manage `~/.local/bin/auto-speech`.
5. All 50 test suites pass cleanly with 0 failures and 0 ruff errors.

**Verdict**: **CLEAN**.

---

## 5. Verification Method

To independently verify this audit within `/Users/joshua/Developer/auto-speech-worktree`:

```bash
# 1. Execute CLI unit tests (74 tests)
.venv/bin/python -m unittest tests/test_cli_dispatcher.py

# 2. Execute installer integration tests (12 assertions)
bash tests/test_install_plugin.sh

# 3. Execute complete test suite (50 test suites)
bash tests/run_all.sh

# 4. Verify code formatting and linting
.venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py

# 5. Verify direct CLI entrypoints and diagnostic probes
bin/auto-speech --help
bin/auto-speech --version
bin/auto-speech doctor
~/.local/bin/auto-speech --help

# 6. Verify input validation and exit codes
bin/auto-speech speak < /dev/null          # Exits 2
bin/auto-speech pause                     # Exits 2 (no active session)
bin/auto-speech replay --ordinal 0        # Exits 2 (invalid ordinal)
```
