# Milestone M1 Handoff Report: Unified `auto-speech` CLI Dispatcher

**Agent:** `worker_m1` (teamwork_preview_worker)  
**Date:** 2026-10-06T21:34:00Z  
**Worktree Location:** `/Users/joshua/Developer/auto-speech-worktree`  
**Target Milestone:** Milestone M1 (Unified auto-speech CLI Dispatcher)

---

## 1. Observation

1. **Baseline Environment**:
   - Worktree branch: `worktree/cli-mcp-parity` at `/Users/joshua/Developer/auto-speech-worktree`.
   - `.venv/bin/python` resolved to Python 3.12.13.
   - Initial test baseline execution:
     `bash tests/run_all.sh` ran 49 test suites, 0 failed.
     `bash tests/test_install_plugin.sh` ran 10/10 assertions passed.

2. **Artifacts Created and Modified in Worktree**:
   - `bin/auto-speech`: Created executable bash trampoline (`chmod +x`). Resolves true script directory across symlink hops (`MAX_HOPS=20`), finds `$PROJECT_ROOT/.venv/bin/python`, validates `plugin/scripts/python/cli.py`, exports `PYTHONPATH`, and replaces shell process via `exec`.
   - `plugin/scripts/python/cli.py`: Created top-level CLI router with 12 subcommands: `speak`, `replay`, `pause`, `resume`, `restart`, `stop`, `seek`, `autoplay`, `narrate`, `hook`, `doctor`, `update`.
   - `setup/install-plugin.sh`: Added `install_cli_symlink` helper and execution step linking `~/.local/bin/auto-speech -> $PROJECT_ROOT/bin/auto-speech`.
   - `setup/uninstall.sh`: Added removal logic for `~/.local/bin/auto-speech` if pointing into `$PROJECT_ROOT/*`.
   - `tests/test_install_plugin.sh`: Added assertions verifying symlink creation, removal on uninstall, and portability invariant coverage of `bin/`.
   - `tests/test_cli_dispatcher.py`: Implemented 74 unit and integration tests across 9 test classes covering all 12 subcommands, positional vs piped stdin, global mute, transport controls, and exit codes.
   - `tests/run_all.sh`: Registered `test_cli_dispatcher.py` under the `CHEAP` test array.

3. **User System Symlink**:
   - Symlinked `~/.local/bin/auto-speech -> /Users/joshua/Developer/auto-speech-worktree/bin/auto-speech`.

4. **Empirical Verification Results**:
   - `.venv/bin/python -m unittest tests/test_cli_dispatcher.py`:
     ```
     Ran 74 tests in 0.213s
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
   - Direct CLI help invocation:
     `bin/auto-speech --help`: Exited 0 with full 12 subcommand synopsis.
     `~/.local/bin/auto-speech --help`: Exited 0 with full 12 subcommand synopsis.

---

## 2. Logic Chain

1. **Trampoline Portability and Virtual Environment Resolution**:
   - As observed in §1.2, executing `bin/auto-speech` requires Python 3.12 with project dependencies.
   - The recursive `readlink` loop with `MAX_HOPS=20` reliably unwinds symlinks from `~/.local/bin/auto-speech` to the true worktree root without relying on hardcoded paths or `~/.config/auto-speech/root` (which avoids cross-contamination between worktree and main).
   - Calling `exec "$VENV_PYTHON" "$CLI_PY" "$@"` replaces the wrapper process, preserving exit codes, standard I/O streams, and UNIX signals.

2. **Unified CLI Subcommand Router (`cli.py`)**:
   - The top-level `argparse` configuration accurately exposes `--help` (exit 0), `--version` (exit 0, `auto-speech 0.2.0`), and exits 2 on missing or invalid subcommands.
   - `speak` checks `AutoplayGate().worker_gated_off()` (exit 0 on mute), validates 64-hex `--source-hash` (exit 2 on invalid format), prioritizes positional text over stdin, and routes speech requests to `DaemonClient.speak()` or pre-cached playback via `play_cache()`.
   - `replay` queries `CacheStore.list_by_recency()`, validates 1-indexed `--ordinal`, respects global mute, and routes playback through daemon or `NativeAudioSink`.
   - Transport controls (`pause`, `resume`, `restart`, `seek`, `stop`) evaluate active session liveness via `_has_active_session()` and `_is_mpv_socket_alive()`. Absent sessions return exit code 2; malformed seek targets return exit code 4; active commands dispatch via `MpvIpc.send()`.
   - `autoplay` manages `SessionEnrollment`, `SoloScope`, and `~/.config/auto-speech/autoplay.toml`.
   - `narrate` manages session markers in `~/.claude/auto-speech-narrate-sessions/`, ensures daemon liveness, and exposes `status`, `stop`, and `config` (`show`, `path`, `edit`).
   - `hook` routes `Stop`, `narrator`, and `bootstrap` events with defensive JSON parsing, session scoping, and daemon auto-starting.
   - `doctor` and `update` execute the diagnostic suite and dependency synchronizer.

3. **Installer & Uninstaller Integration**:
   - The additions to `setup/install-plugin.sh` establish `~/.local/bin/auto-speech` idempotently during plugin installation.
   - `setup/uninstall.sh` safely removes the link only if it targets the repository root.
   - `tests/test_install_plugin.sh` verifies these behaviors against isolated sandbox environments, expanding test coverage from 10 to 12 passing assertions.

---

## 3. Caveats

- **Worktree vs. Main Linkage**: `~/.local/bin/auto-speech` on the user system currently links to the worktree (`/Users/joshua/Developer/auto-speech-worktree/bin/auto-speech`). When Milestone M5 completes the merge into `main` and the worktree is cleaned up, `setup/install-plugin.sh` should be re-run in the main repo to repoint the symlink to `/Users/joshua/Developer/auto-speech/bin/auto-speech`.
- **E2E Audio Device**: Tests run hermetically with mocked sinks and subprocesses. No physical audio hardware is required during automated testing.

---

## 4. Conclusion

Milestone M1 (Unified auto-speech CLI Dispatcher) is completely implemented, cleanly formatted, and empirically verified:
1. `bin/auto-speech` provides the canonical executable entrypoint on PATH via `~/.local/bin/auto-speech`.
2. `plugin/scripts/python/cli.py` delivers complete functional coverage across all 12 subcommands with standard exit codes (0, 1, 2, 3, 4, 6, 130).
3. All 74 unit tests in `tests/test_cli_dispatcher.py` pass.
4. All 12 installer assertions in `tests/test_install_plugin.sh` pass.
5. All 50 test suites in `tests/run_all.sh` pass with 0 failures.
6. Ruff reports 0 lint errors on all implemented files.

The codebase is ready for Milestone M2 (Expanded MCP Server Tool Suite).

---

## 5. Verification Method

To independently verify this milestone within `/Users/joshua/Developer/auto-speech-worktree`:

```bash
# 1. Verify unit tests for CLI dispatcher (74 tests)
.venv/bin/python -m unittest tests/test_cli_dispatcher.py

# 2. Verify installer script integration (12 assertions)
bash tests/test_install_plugin.sh

# 3. Run full test suite (50 test suites)
bash tests/run_all.sh

# 4. Verify code formatting and linting
.venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py

# 5. Verify direct CLI entrypoints
bin/auto-speech --help
~/.local/bin/auto-speech --help
bin/auto-speech --version
```
