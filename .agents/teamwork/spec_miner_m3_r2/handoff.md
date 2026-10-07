# Handoff Report: Milestone M3 Test Specification & Verification Harness

**Author**: `teamwork_preview_spec_miner` (`spec_miner_m3_r2`)  
**Recipient**: `parent` (`4db9af0e-498d-49a5-831a-0009fd31326e`)  
**Date**: 2026-10-06T23:38:00Z  
**Type**: Hard Handoff (Task Complete)

---

## 1. Observation

1. **`tests/test_narrator_hook.sh`**:
   - Currently runs 6 test cases (1 anchor + 5 functional cases):
     - `anchor:hook-uses-narrator-pid-is-ours`
     - `suppress=1 bails immediately (delta=0)`
     - `no session anywhere bails (delta=0)`
     - `session id present but no marker → bail (delta=0)`
     - `payload session matches marker → fire (delta=1)`
     - `env session matches marker (no payload) → fire (delta=1)`
   - Command `bash tests/test_narrator_hook.sh` exited 0.
   - Missing test cases: Antigravity payload `{"conversationId": "conv-1"}`, dual payload `{"session_id": "s1", "conversationId": "c1"}`, global mute (`auto-speech.disabled`), and non-dict payload (`["array"]`, `"string"`, `123`, `malformed`, `""`).

2. **`plugin/scripts/shell/narrator_hook.sh`**:
   - Lines 36-40:
     ```bash
     SESSION_ID=""
     if command -v jq >/dev/null 2>&1; then
         SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r '.session_id // ""' 2>/dev/null || true)"
     fi
     ```
     Lacks check for `.conversationId`.
   - Lacks check for `$HOME/.claude/auto-speech.disabled` (`DISABLED_MARKER`).
   - Line 86:
     ```bash
     if printf '%s' "$PAYLOAD" | jq -e . >/dev/null 2>&1; then
     ```
     Succeeds on JSON arrays (`[1, 2]`), but downstream `.hook_event_name` indexing throws jq error `Cannot index array with string "hook_event_name"` into `ERR_LOG`.

3. **`tests/test_autoplay_shim.sh`**:
   - Currently runs 9 test cases (3 anchors, 3 PID guard tests, 3 functional cases).
   - Command `bash tests/test_autoplay_shim.sh` exited 0 (9 ran, 0 failed).
   - Missing explicit test cases: Antigravity payload `{"conversationId": "conv-1"}`, Antigravity global mute, and non-dict payloads (`[...]`, `"string"`, `123`, `malformed`, `""`).

4. **`plugin/scripts/shell/autoplay_hook.sh`**:
   - Lines 38-39:
     ```bash
     TRANSCRIPT_PATH="$(printf '%s' "$PAYLOAD" | jq -r '(.transcript_path // .transcriptPath) // ""' 2>/dev/null || true)"
     SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r '(.session_id // .conversationId) // ""' 2>/dev/null || true)"
     ```
     Already parses `.conversationId` and `.transcriptPath`.
   - Lines 53-55:
     ```bash
     if [[ -e "$DISABLED_MARKER" ]]; then
         exit 0
     fi
     ```
     Already enforces global mute.
   - On non-dict payloads (e.g. `[1, 2]`), jq emits error to stderr which is caught by `2>/dev/null || true`, returning `""` and bailing cleanly. Can be hardened with `type == "object"`.

5. **`tests/test_install_plugin.sh`**:
   - Command `bash tests/test_install_plugin.sh` ran and exited 0 with all 12/12 passed:
     - `curated install links exactly 8 commands`
     - `root file recorded with the clone path`
     - `curated install links ~/.local/bin/auto-speech`
     - `doctor block resolves root from ~/.config/auto-speech/root`
     - `guard fires when root file is absent`
     - `--with-extras links 21 commands`
     - `plain re-run preserves opted-in extras`
     - `prune removes non-keep-set links into plugin/commands/`
     - `prune removes dangling links into plugin/commands-extra/`
     - `uninstall removes the recorded root file`
     - `uninstall removes ~/.local/bin/auto-speech symlink`
     - `no hardcoded absolute project paths in command files`

6. **21 Slash Commands (`plugin/commands/` & `plugin/commands-extra/`)**:
   - Syntax validation: Verified `bash -n` on all 21 commands passes with 0 syntax errors.
   - Portability: Verified grep for `/Users/joshua` across all 21 commands and `bin/` yields 0 matches.
   - Delegation: All 21 commands resolve `PROJECT_ROOT` from `~/.config/auto-speech/root` and delegate to backend scripts or `auto-speech`.

7. **Full Test Suite (`tests/run_all.sh`)**:
   - Command `bash tests/run_all.sh` ran 50 suites:
     - 36 cheap Python unit suites
     - 8 shell script test suites
     - 1 web API test suite
     - 5 heavy audio pipeline test suites
   - Result: `ran: 50`, `failed: 0`, `all tests passed`.

---

## 2. Logic Chain

1. **Step 1 (Hook Invariants)**:
   - Observation 1 & 2 show that `narrator_hook.sh` currently does not support `conversationId` or `auto-speech.disabled`. When an Antigravity hook fires or global mute is enabled, the narrator hook behaves incorrectly (fails to record Antigravity events, or speaks during global mute).
   - Therefore, `narrator_hook.sh` must be updated with `DISABLED_MARKER` check and `(.session_id // .conversationId)`.
   - Observation 1 specifies the exact test additions needed in `tests/test_narrator_hook.sh` to pin these contracts.

2. **Step 2 (Autoplay Invariants)**:
   - Observation 4 shows that `autoplay_hook.sh` already has the parsing logic for `.conversationId` and `auto-speech.disabled`.
   - Observation 3 shows that `tests/test_autoplay_shim.sh` lacks dedicated test assertions for `conversationId`, global mute under Antigravity payloads, and non-dict payloads.
   - Adding these assertions to `tests/test_autoplay_shim.sh` will prevent regressions and lock in the dual-platform contract.

3. **Step 3 (Installer and Slash Command Invariants)**:
   - Observation 5 confirms that the installer contract is 100% compliant and all 12 assertions are passing.
   - Observation 6 confirms that all 21 slash commands are syntactically valid and free of hardcoded paths.

4. **Step 4 (Test Harness Invariants)**:
   - Observation 7 proves that the baseline test runner passes 50/50 test suites cleanly.
   - Any test additions to `test_narrator_hook.sh` and `test_autoplay_shim.sh` will integrate directly into `tests/run_all.sh` under `SHELL_TESTS`.

---

## 3. Caveats

- **No Caveats**: All 21 slash command markdown files, hook entrypoints, trampoline scripts, and 50 test suites were forensically inspected and directly executed on disk in the isolated worktree `/Users/joshua/Developer/auto-speech-worktree`.

---

## 4. Conclusion

The specification mining for Milestone M3 is complete. The detailed requirements, edge cases, input/output mappings, test assertions, and production file remediation contracts are fully documented in:
`/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m3_r2/analysis.md`

The worker/implementer can directly implement the test additions in `tests/test_narrator_hook.sh` and `tests/test_autoplay_shim.sh`, and apply the corresponding updates in `plugin/scripts/shell/narrator_hook.sh` and `plugin/scripts/shell/autoplay_hook.sh`, maintaining 100% green pass on `bash tests/run_all.sh`.

---

## 5. Verification Method

To independently verify this specification and baseline status:

1. **Verify Installer & Commands**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   bash tests/test_install_plugin.sh
   # Expected: install-plugin: 12/12 passed
   ```

2. **Verify Autoplay Shim**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   bash tests/test_autoplay_shim.sh
   # Expected: autoplay shim: 9 ran, 0 failed
   ```

3. **Verify Narrator Hook**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   bash tests/test_narrator_hook.sh
   # Expected: narrator_hook gate: 6 ran, 0 failed
   ```

4. **Verify Full Test Harness**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   bash tests/run_all.sh
   # Expected: ran: 50, failed: 0, all tests passed
   ```

5. **Verify Slash Command Bash Syntax**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   for f in plugin/commands/*.md plugin/commands-extra/*.md; do
       awk '/^```/{f=!f;next} f' "$f" | sed 's/<[A-Za-z_]*>/dummy/g; s/ORDINAL/1/g; s/TARGET/10/g' | bash -n || echo "FAIL: $f"
   done
   # Expected: 0 failures
   ```
