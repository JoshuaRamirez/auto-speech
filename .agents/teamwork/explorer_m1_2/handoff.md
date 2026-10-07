# Handoff Report: Milestone M1 CLI Trampoline, Symlink Integration, and Installer Synchronization

**Agent:** `explorer_m1_2` (Teamwork Explorer)  
**Task:** Formulate implementation strategy for `bin/auto-speech` bash trampoline, `~/.local/bin/auto-speech` symlink, installer/uninstaller synchronization, and test suite verification for Milestone M1.  
**Target Worktree:** `/Users/joshua/Developer/auto-speech-worktree`  
**Handoff Type:** Hard (Task complete)

---

## 1. Observation

1. **Test Suite Baseline**:
   - Tool command: `bash tests/test_install_plugin.sh`
   - Output: 10/10 tests passed (Exit code: 0):
     - `ok curated install links exactly 8 commands`
     - `ok root file recorded with the clone path`
     - `ok doctor block resolves root from ~/.config/auto-speech/root`
     - `ok guard fires when root file is absent`
     - `ok --with-extras links 21 commands`
     - `ok plain re-run preserves opted-in extras`
     - `ok prune removes non-keep-set links into plugin/commands/`
     - `ok prune removes dangling links into plugin/commands-extra/`
     - `ok uninstall removes the recorded root file`
     - `ok no hardcoded absolute project paths in command files`
2. **Worktree Environment**:
   - `git -C /Users/joshua/Developer/auto-speech-worktree status` confirmed branch `worktree/cli-mcp-parity`.
   - `ls -la /Users/joshua/Developer/auto-speech-worktree/.venv` confirmed `.venv -> /Users/joshua/Developer/auto-speech/.venv`.
   - `test -x /Users/joshua/Developer/auto-speech-worktree/.venv/bin/python` returned exit code 0 (`executable`).
3. **Existing Installer Structure (`setup/install-plugin.sh:21-66`)**:
   - `PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"` (line 21).
   - `install_one()` handles symlink creation and updates with stale link replacement and non-symlink collision checks (lines 39–66).
   - Currently, `setup/install-plugin.sh` does not install anything into `~/.local/bin/`.
4. **Existing Uninstaller Structure (`setup/uninstall.sh:49-66, 144-150`)**:
   - `setup/uninstall.sh` iterates through `NAMESPACED_CMDS` and `LEGACY_CMDS` and removes symlinks matching `"$PROJECT_ROOT/"*` (lines 50–65).
   - Removes `$HOME/.config/auto-speech/root` (lines 146–149).
   - Does not currently remove `$HOME/.local/bin/auto-speech`.
5. **System PATH**:
   - `echo "$PATH"` confirmed `/Users/joshua/.local/bin` is present on PATH, hosting user toolchains (`agent-event-router`, `itg`, `svc`, `sysobs`, `systemone`).
   - `ls -l ~/.local/bin/auto-speech` reported not present.
6. **Empirical Simulation**:
   - A sandbox simulation testing multi-hop symlink resolution, relative symlink resolution, missing venv error reporting (`error: virtual environment python not found at ... Run .../setup/install.sh`), and `exec` process replacement confirmed clean exit codes (`0` on success, `1` on missing venv).

---

## 2. Logic Chain

1. From **Observation 2**, the worktree has a symlinked `.venv` pointing to the main clone's `.venv`. Therefore, `bin/auto-speech` must verify execution permission via `[[ -x "$VENV_PYTHON" ]]` rather than directory tests (`[[ -d ... ]]`), ensuring transparent resolution across symlinks.
2. From **Observation 2 and 6**, falling back to `~/.config/auto-speech/root` for python interpreter discovery would cause cross-talk between the worktree and the main repo if the worktree's venv was ever absent or broken. Therefore, `bin/auto-speech` must strictly resolve its virtual environment relative to its own canonicalized `$PROJECT_ROOT` (`$PROJECT_ROOT/.venv/bin/python`).
3. From **Observation 1 and 5**, `tests/test_install_plugin.sh` runs with `HOME="$SANDBOX"`. Because the installer uses `$HOME/.local/bin`, all test runs remain strictly confined to `$SANDBOX/.local/bin/auto-speech`, ensuring real user binaries in `/Users/joshua/.local/bin` are never touched during automated testing.
4. From **Observation 1**, `tests/test_install_plugin.sh` asserts line count specifically against `$SANDBOX/.claude/commands/auto-speech-*.md` (lines 31, 62). Adding a symlink at `$SANDBOX/.local/bin/auto-speech` does not affect this count and leaves all 10 existing assertions green.
5. From **Observation 3 and 4**, following the established pattern of checking `case "$target" in "$PROJECT_ROOT/"*)` guarantees that uninstalling `auto-speech` never deletes a foreign or unrelated binary if the user or another package owned `~/.local/bin/auto-speech`.
6. From **Observation 6**, using `exec "$VENV_PYTHON" "$CLI_PY" "$@"` replaces the shell process image, ensuring that signal delivery (`SIGINT`, `SIGTERM`), exit code propagation, and file descriptor inheritance (pipes and redirections) pass cleanly through to `cli.py` without intermediate shell wrappers.

---

## 3. Caveats

1. **`cli.py` Python Implementation**: The internal implementation of `cli.py` (argument parsers, 12 subcommands, DaemonClient integration) is assigned to peer agent `explorer_m1_1`. The trampoline specifies the execution boundary and contract (`exec "$VENV_PYTHON" "$CLI_PY" "$@"`), which expects `cli.py` to reside at `plugin/scripts/python/cli.py`.
2. **Worktree Venv Prerequisite**: The isolated worktree at `/Users/joshua/Developer/auto-speech-worktree` must have its `.venv` symlinked to `/Users/joshua/Developer/auto-speech/.venv` (already verified present). If the worktree is moved or the main venv is deleted, `setup/install.sh` must be re-run in the worktree.
3. **Execution Permissions**: `bin/auto-speech` must be tracked in Git with executable mode `100755` (`chmod +x bin/auto-speech`). `setup/install-plugin.sh` additionally enforces `chmod +x` during installation as defense-in-depth.

---

## 4. Conclusion

The specification for Milestone M1's trampoline, symlink, and installer synchronization is complete, validated, and ready for implementation by `worker_m1`:
1. `bin/auto-speech` provides a zero-dependency, symlink-canonicalized bash trampoline that detects `$PROJECT_ROOT/.venv/bin/python`, emits actionable stderr diagnostics, exports `PYTHONPATH`, and execs `cli.py "$@"`.
2. `setup/install-plugin.sh` creates `~/.local/bin/auto-speech` idempotently, with stale link removal and non-symlink collision detection.
3. `setup/uninstall.sh` cleans up `~/.local/bin/auto-speech` if and only if it points into `$PROJECT_ROOT/`.
4. `tests/test_install_plugin.sh` retains 100% green status on all 10 existing assertions while adding 3 new assertions for CLI symlink creation, uninstallation, and portability (13/13 passing).

All exact file contents and diff patches are documented in `analysis.md`.

---

## 5. Verification Method

To independently verify the implementation in `/Users/joshua/Developer/auto-speech-worktree`:

1. **Verify Installer and Uninstaller Contract Tests**:
   ```bash
   bash tests/test_install_plugin.sh
   ```
   *Expected result:* 13/13 assertions pass with exit code 0.
2. **Verify Direct Trampoline Execution**:
   ```bash
   ./bin/auto-speech --help
   ```
   *Expected result:* Exits 0 and displays CLI dispatcher help.
3. **Verify Symlink Resolution from `~/.local/bin`**:
   ```bash
   bash setup/install-plugin.sh
   ~/.local/bin/auto-speech --help
   ```
   *Expected result:* Exits 0; `readlink ~/.local/bin/auto-speech` points to the worktree's `bin/auto-speech`.
4. **Verify Missing Venv Diagnostic**:
   ```bash
   AUTO_SPEECH_TEST_DIR=$(mktemp -d)
   mkdir -p "$AUTO_SPEECH_TEST_DIR/bin" "$AUTO_SPEECH_TEST_DIR/plugin/scripts/python"
   cp bin/auto-speech "$AUTO_SPEECH_TEST_DIR/bin/"
   touch "$AUTO_SPEECH_TEST_DIR/plugin/scripts/python/cli.py"
   "$AUTO_SPEECH_TEST_DIR/bin/auto-speech" 2>&1
   ```
   *Expected result:* Exits with code 1 and prints:
   `error: virtual environment python not found at <path>/.venv/bin/python`
   `       Run <path>/setup/install.sh to initialize the virtual environment.`
   Clean up: `rm -rf "$AUTO_SPEECH_TEST_DIR"`.
5. **Verify Full Test Suite Regression**:
   ```bash
   bash tests/run_all.sh
   ```
   *Expected result:* All 42+ test suites pass with 0 failures.
