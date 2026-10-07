# Review & Handoff Report: Milestone M1 CLI Dispatcher & Installer Integration

**Agent:** `reviewer_m1_2` (teamwork_preview_reviewer / critic)  
**Date:** 2026-10-06T21:39:00Z  
**Worktree Directory:** `/Users/joshua/Developer/auto-speech-worktree`  
**Working Directory:** `/Users/joshua/Developer/auto-speech/.agents/teamwork/reviewer_m1_2`  
**Verdict:** **APPROVE**

---

## Review Summary

**Verdict**: **APPROVE**

Milestone M1 delivers a robust, portable, and cleanly decoupled CLI surface and installer integration for `auto-speech`. The bash trampoline correctly handles arbitrary symlink hops without path assumptions or hardcoded directories, `cli.py` faithfully implements the 12 subcommands specified in `PROJECT.md`, installer and uninstaller integration protect user files with guarded removal, and test suites pass 100% across all 50 suites with 0 lint violations. No integrity violations or facade implementations were detected.

---

## 1. Observation

Direct observations and empirical execution records in `/Users/joshua/Developer/auto-speech-worktree`:

1. **Required Verification Invocations**:
   - `bash tests/test_install_plugin.sh`:
     ```
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
     Exit code: `0`.

   - `bin/auto-speech --help`:
     ```
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
     Exit code: `0`.

   - `~/.local/bin/auto-speech --help`:
     Resolved link `/Users/joshua/.local/bin/auto-speech -> /Users/joshua/Developer/auto-speech-worktree/bin/auto-speech`.
     Exited `0` with identical 12 subcommand synopsis.

   - `bin/auto-speech --version`:
     Output: `auto-speech 0.2.0`.
     Exit code: `0`.

2. **Full Regression and Unit Test Execution**:
   - `.venv/bin/python -m unittest tests/test_cli_dispatcher.py`:
     ```
     Ran 74 tests in 0.201s
     OK
     ```
     Exit code: `0`.
   - `bash tests/run_all.sh`:
     ```
     ran:    50
     failed: 0
     all tests passed
     ```
     Exit code: `0`.
   - `.venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py setup/`:
     ```
     All checks passed!
     ```
     Exit code: `0`.

3. **Code Inspection and Path Hardcoding Audit**:
   - `grep -r "/Users/" bin/ plugin/scripts/python/cli.py setup/install-plugin.sh setup/uninstall.sh`:
     Returned exit code `1` (0 matches). No absolute home or user directory paths exist in executable or installation code.
   - `bin/auto-speech`:
     Unwinds symlink chains via `readlink` loop with `MAX_HOPS=20` and relative link resolution (`[[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"`). Resolves `$PROJECT_ROOT/.venv/bin/python`, validates existence of `cli.py`, exports `PYTHONPATH`, and replaces shell process via `exec`.
   - `setup/install-plugin.sh`:
     `install_cli_symlink()` idempotently creates `~/.local/bin/auto-speech`, replaces stale symlinks, and safely aborts if `dst` is a non-symlink file.
   - `setup/uninstall.sh`:
     Removes `~/.local/bin/auto-speech` if and only if its target matches `"$PROJECT_ROOT/"*`, protecting foreign symlinks.

4. **Adversarial Stress-Test Observations**:
   - Multi-hop relative symlink chain (`test-link-3 -> ../test-link-2 -> test-link-1 -> bin/auto-speech` executed from foreign directory `/tmp`):
     Exited `0` outputting `auto-speech 0.2.0`.
   - Exit codes on absent session:
     `pause`, `resume`, `restart`, `stop`, `seek +10` all exited with code `2` as specified in `PROJECT.md`.
   - Exit codes on input validation failure:
     `replay --ordinal 0` exited `2`.
     `speak --source-hash 123` exited `2`.
     `speak` without text or stdin exited `2`.
     `seek` with non-numeric target during active session exited `4`.
   - Global mute check:
     Running `bin/auto-speech speak "hello"` with `~/.claude/auto-speech.disabled` present printed notice to stderr and cleanly exited `0`.

---

## 2. Logic Chain

1. **Integrity and Authenticity Verification**:
   - All tests in `tests/test_cli_dispatcher.py` instantiate real or cleanly mocked components (`argparse`, `DaemonResponse`, `AutoplayGate`, `CacheStore`). None of the tests use hardcoded return values embedded in production code.
   - `cli.py` contains substantive dispatch and integration logic for all 12 subcommands, linking directly with existing domain modules (`daemon_client`, `cache_store`, `mpv_ipc`, `doctor`, `native_audio_sink`, `autoplay_config`, `narrator_config`).
   - Every verification result was produced by running commands directly in the shell environment.

2. **Symlink and Shell Portability**:
   - `bin/auto-speech` does not assume a fixed install directory; it dynamically resolves its own true path across up to 20 symlink hops and relative target paths.
   - The multi-hop symlink stress test proved that even when invoked from an arbitrary current working directory through relative symlinks, the trampoline correctly locates `$PROJECT_ROOT/.venv/bin/python` and replaces itself via `exec`.
   - The uninstaller `setup/uninstall.sh` uses case matching against `"$PROJECT_ROOT/"*` before deleting the symlink, ensuring it will not inadvertently remove a symlink belonging to another user installation or tool.

3. **Interface and Exit Code Contracts**:
   - In accordance with `PROJECT.md` §75–96:
     - Normal operations and muted runs return `0`.
     - Transport operations without an active playback session return `2` (`EXIT_NO_SESSION`).
     - Validation failures (e.g. invalid `--source-hash` length, invalid ordinal `< 1`, empty text) return `2` (`EXIT_INVALID_INPUT`).
     - Bad seek targets return `4` (`EXIT_BAD_ARG`).
     - Diagnostic failures return `1` (`EXIT_FAILURE`).
   - The dispatcher strictly enforces these codes, preventing erroneous status reporting to callers and shell hooks.

---

## 3. Caveats

- **Worktree Symlink Retargeting at Milestone M5**: `~/.local/bin/auto-speech` currently targets `/Users/joshua/Developer/auto-speech-worktree/bin/auto-speech`. When Milestone M5 completes the merge into `main` and deletes the worktree directory, `setup/install-plugin.sh` must be executed from `/Users/joshua/Developer/auto-speech` to retarget the symlink to the permanent repository.
- **Hermetic Mocking**: Automated test suites mock `MpvIpc` and audio sinks; no physical audio device was actuated during automated tests.

---

## 4. Conclusion

Milestone M1 fulfills all functional and non-functional requirements specified in `PROJECT.md` and `ORIGINAL_REQUEST.md`:
1. `bin/auto-speech` is portable, robust, and correctly linked to `~/.local/bin/auto-speech`.
2. `plugin/scripts/python/cli.py` provides complete coverage of all 12 subcommands with exact exit code conformity.
3. `setup/install-plugin.sh` and `setup/uninstall.sh` handle symlink lifecycle idempotently and defensively.
4. All 50 test suites in `tests/run_all.sh` pass cleanly with zero lint errors.
5. No integrity violations, shortcuts, or facade implementations exist.

**Final Verdict**: **APPROVE**. The codebase is ready to proceed to Milestone M2.

---

## 5. Verification Method

To independently reproduce and verify this review in `/Users/joshua/Developer/auto-speech-worktree`:

```bash
# 1. Run installer contract test suite (12 assertions)
bash tests/test_install_plugin.sh

# 2. Run CLI dispatcher unit and integration tests (74 tests)
.venv/bin/python -m unittest tests/test_cli_dispatcher.py

# 3. Run full test suite (50 suites)
bash tests/run_all.sh

# 4. Check ruff linter
.venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py setup/

# 5. Verify direct CLI entrypoints
bin/auto-speech --help
~/.local/bin/auto-speech --help
bin/auto-speech --version

# 6. Verify exit code contracts
bin/auto-speech pause; echo "exit: $?"       # Expect 2
bin/auto-speech seek +10; echo "exit: $?"    # Expect 2
bin/auto-speech replay --ordinal 0; echo "exit: $?" # Expect 2
```
