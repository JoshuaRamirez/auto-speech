# Handoff Report: Milestone M3 Review & Adversarial Audit (Reviewer 2)

**Subagent**: `teamwork_preview_reviewer` (`reviewer_m3_2`)  
**Working Directory**: `/Users/joshua/Developer/auto-speech/.agents/teamwork/reviewer_m3_2`  
**Target Git Worktree**: `/Users/joshua/Developer/auto-speech-worktree`  
**Branch**: `worktree/cli-mcp-parity`  
**Date**: 2026-10-06T23:59:50Z  
**Handoff Type**: Hard (Review & Adversarial Verification Complete)  
**Verdict**: **APPROVE**

---

## 1. Observation

1. **Installer Contract Tests (`tests/test_install_plugin.sh`)**:
   - Command executed: `bash tests/test_install_plugin.sh` in `/Users/joshua/Developer/auto-speech-worktree`.
   - Result:
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

2. **Hardcoded User Paths Audit**:
   - Command executed:
     `grep -rn "/Users/joshua" plugin/commands plugin/commands-extra setup/install-plugin.sh setup/uninstall.sh bin/`
     and `grep -rn "/Users/" plugin/commands plugin/commands-extra setup/install-plugin.sh setup/uninstall.sh bin/`
   - Result: 0 matches found across all command markdown files, setup scripts, and the CLI binary trampoline.

3. **Bash Syntax Verification (`bash -n`) on All 21 Commands**:
   - Command executed: Extracted all bash code blocks across all 8 curated commands in `plugin/commands/` and 13 extra commands in `plugin/commands-extra/`, tested with `bash -n`.
   - Result: All 21 files passed with 0 syntax errors:
     * `plugin/commands/auto-speech-app.md`: PASS
     * `plugin/commands/auto-speech-autoplay-mode.md`: PASS
     * `plugin/commands/auto-speech-autoplay-off.md`: PASS
     * `plugin/commands/auto-speech-autoplay-on.md`: PASS
     * `plugin/commands/auto-speech-doctor.md`: PASS
     * `plugin/commands/auto-speech-replay.md`: PASS
     * `plugin/commands/auto-speech-scope.md`: PASS
     * `plugin/commands/auto-speech-speak.md`: PASS
     * `plugin/commands-extra/auto-speech-autoplay-status.md`: PASS
     * `plugin/commands-extra/auto-speech-end.md`: PASS
     * `plugin/commands-extra/auto-speech-narrate-config.md`: PASS
     * `plugin/commands-extra/auto-speech-narrate-install.md`: PASS
     * `plugin/commands-extra/auto-speech-narrate-off.md`: PASS
     * `plugin/commands-extra/auto-speech-narrate-on.md`: PASS
     * `plugin/commands-extra/auto-speech-narrate-status.md`: PASS
     * `plugin/commands-extra/auto-speech-narrate-stop.md`: PASS
     * `plugin/commands-extra/auto-speech-pause.md`: PASS
     * `plugin/commands-extra/auto-speech-restart.md`: PASS
     * `plugin/commands-extra/auto-speech-resume.md`: PASS
     * `plugin/commands-extra/auto-speech-seek.md`: PASS
     * `plugin/commands-extra/auto-speech-update.md`: PASS

4. **Root Resolution Guard Enforcement**:
   - Command executed: Tested all 21 command blocks against a clean environment with missing `~/.config/auto-speech/root`, and against an environment with an invalid/nonexistent root path.
   - Result: All 21 commands exited with return code 1 and emitted the exact guard message:
     `auto-speech: project root not configured — run setup/install-plugin.sh from your clone`

5. **Empirical Regression Suites & Linter**:
   - Full test suite: `bash tests/run_all.sh` executed to completion:
     `ran: 50, failed: 0, all tests passed`.
   - CLI dispatcher unit suite: `.venv/bin/python -m unittest tests/test_cli_dispatcher.py`:
     `Ran 84 tests in 0.942s, OK`.
   - Autoplay and Narrator hook suites:
     * `bash tests/test_autoplay_shim.sh`: 17 ran, 0 failed.
     * `bash tests/test_narrator_hook.sh`: 17 ran, 0 failed.
   - Ruff linter: `.venv/bin/ruff check plugin/`:
     `All checks passed!`.

6. **Adversarial & Edge Case Invariants**:
   - Trampoline symlink traversal: `bin/auto-speech` resolves multi-hop symlink chains (tested across 5 nested symlinks) and protects against cyclic loops via `MAX_HOPS=20` and OS inode limits.
   - Variable quoting: In all 21 command markdown files, every reference to `$PROJECT_ROOT` is strictly encapsulated in double quotes (`"$PROJECT_ROOT"`), ensuring resilience against directory paths containing whitespace or special characters.
   - Installer/Uninstaller Lifecycle: Tested curated install -> idempotent re-run -> extras install -> extras preservation on re-run -> uninstall cleanup -> uninstaller idempotent re-run in an isolated temporary sandbox. All assertions held without state leakage.

7. **Integrity Violation Audit**:
   - Checked for hardcoded test results, facade stubs returning fake success, and skipped implementations.
   - Verified that CLI dispatch, MCP server tools, and hook routing execute genuine computational logic and system calls without mocks or dummy short-circuits.

---

## 2. Logic Chain

1. **Contract Adherence**:
   - Observations 1 and 4 confirm that the slash commands rely on the contract established by `setup/install-plugin.sh` (writing the clone path into `~/.config/auto-speech/root`). When present, the commands route directly to the clone's `bin/auto-speech`; when absent or invalid, they fail fast with clear actionable remediation instructions.

2. **Portability & Security Invariants**:
   - Observation 2 proves there are zero leaked absolute user paths (`/Users/joshua`), guaranteeing that any user or environment cloning the repository can install and execute the plugin cleanly.
   - Observation 6 proves that path expansions handle whitespace safely via uniform double quoting.

3. **Subcommand Reconciliation**:
   - Observation 3 confirms that all 21 slash command code blocks are syntactically sound bash scripts. 19 of them directly delegate to `"$PROJECT_ROOT/bin/auto-speech" <subcommand>`, while the remaining 2 (`auto-speech-app` and `auto-speech-narrate-install`) maintain their necessary interactive entrypoints (`start_webapp.sh` and MLX package sync/download) while enforcing the identical root resolution guard.

4. **Regression Safety**:
   - Observation 5 confirms that none of the changes introduced regressions across any of the 50 repository test suites, the 84 dedicated CLI unit tests, or the hook gate test suites, with zero ruff lint errors.

---

## 3. Caveats

No caveats. All commands, scripts, hooks, and tests were empirically executed inside `/Users/joshua/Developer/auto-speech-worktree` and verified against sandboxed isolated environments.

---

## 4. Conclusion

**Verdict: APPROVE**

Milestone M3 (Slash Command Reconciliation and Installer Contracts) meets all acceptance criteria, maintains strict architectural coherence, enforces robust error guards, and exhibits no integrity violations or regressions.

---

## 5. Verification Method

To independently reproduce and verify this review from `/Users/joshua/Developer/auto-speech-worktree`:

```bash
cd /Users/joshua/Developer/auto-speech-worktree

# 1. Run installer contract tests
bash tests/test_install_plugin.sh

# 2. Check for hardcoded user paths
grep -rn "/Users/joshua" plugin/commands plugin/commands-extra setup/install-plugin.sh setup/uninstall.sh bin/

# 3. Check bash syntax across all 21 commands
python3 -c '
import glob, re, subprocess
for p in sorted(glob.glob("plugin/commands/*.md") + glob.glob("plugin/commands-extra/*.md")):
    b = re.findall(r"```(?:bash)?\n(.*?)```", open(p).read(), re.DOTALL)[0]
    tb = b.replace("[ARGUMENT]","verbatim").replace("ORDINAL","1").replace("TARGET","+10").replace("ACTION","show").replace("<MODEL>","m")
    assert subprocess.run(["bash", "-n"], input=tb, text=True).returncode == 0
print("All 21 bash blocks pass bash -n")
'

# 4. Run CLI unit suite & full repository tests
.venv/bin/python -m unittest tests/test_cli_dispatcher.py
bash tests/run_all.sh
.venv/bin/ruff check plugin/
```
