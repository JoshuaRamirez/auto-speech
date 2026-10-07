# Handoff Report: Milestone M3 (Hook Reconcile & Architectural Coherence)

**Subagent**: `teamwork_preview_worker` (`worker_m3`)  
**Working Directory**: `/Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m3`  
**Target Git Worktree**: `/Users/joshua/Developer/auto-speech-worktree`  
**Branch**: `worktree/cli-mcp-parity`  
**Date**: 2026-10-06T23:53:00Z  
**Handoff Type**: Hard (Milestone Implementation & Verification Complete)

---

## 1. Observation

1. **`narrator_hook.sh` Antigravity and Global Mute Gap**:
   - `plugin/scripts/shell/narrator_hook.sh:36-45` only parsed `.session_id`, dropping Antigravity lifecycle hook payloads that use `conversationId`.
   - `narrator_hook.sh` lacked a check for `$HOME/.claude/auto-speech.disabled`, allowing muted sessions to continue appending events to `/tmp/auto-speech-narrator-events.jsonl`.
   - Non-dict JSON payloads caused jq errors to be written into `ERR_LOG`.
   - Remediation applied to `plugin/scripts/shell/narrator_hook.sh`:
     ```bash
     # Disable marker — exit 0 fast. Global mute, takes precedence over
     # everything else.
     DISABLED_MARKER="$HOME/.claude/auto-speech.disabled"
     if [[ -e "$DISABLED_MARKER" ]]; then
         exit 0
     fi
     ...
     SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r '(.session_id // .conversationId // empty)' 2>/dev/null || true)"
     ...
     if printf '%s' "$PAYLOAD" | jq -e 'type == "object"' >/dev/null 2>&1; then
     ```

2. **`autoplay_hook.sh` Fast-Path Gating & Extraction**:
   - `plugin/scripts/shell/autoplay_hook.sh` originally invoked `jq` prior to testing `AUTO_SPEECH_SUPPRESS_HOOKS` and `DISABLED_MARKER`.
   - Moved suppression and disabled marker checks immediately after reading `$PAYLOAD` before any `jq` execution, yielding <8ms fast exits.
   - Standardized dual extraction queries:
     ```bash
     TRANSCRIPT_PATH="$(printf '%s' "$PAYLOAD" | jq -r '(.transcript_path // .transcriptPath // empty)' 2>/dev/null || true)"
     SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r '(.session_id // .conversationId // empty)' 2>/dev/null || true)"
     ```

3. **`cli.py:handle_hook("stop")` Argument Order Transposition**:
   - In `plugin/scripts/python/cli.py:683-696`, `subprocess.Popen` invoked `autoplay_worker.sh` passing `transcript_path, session_id, str(beacon_file)`.
   - The contract in `autoplay_worker.sh` and `autoplay_worker.py` strictly expects `BEACON_MTIME, TRANSCRIPT_PATH, SESSION_ID`.
   - Fixed in `plugin/scripts/python/cli.py` to extract `beacon_mtime = str(beacon_file.stat().st_mtime)` and pass:
     `[ "bash", str(worker_script), beacon_mtime, transcript_path, session_id ]`.

4. **Slash Command Delegation & Root Resolution Guard**:
   - All 21 markdown commands (8 in `plugin/commands/` and 13 in `plugin/commands-extra/`) now enforce the 2-line root resolution guard:
     ```bash
     PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
     [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
     ```
   - 19 commands were reconciled to delegate directly to `"$PROJECT_ROOT/bin/auto-speech" <subcommand>`:
     * `auto-speech-doctor.md` -> `"$PROJECT_ROOT/bin/auto-speech" doctor`
     * `auto-speech-replay.md` -> `"$PROJECT_ROOT/bin/auto-speech" replay --ordinal ORDINAL`
     * `auto-speech-speak.md` -> Step 3: `"$PROJECT_ROOT/bin/auto-speech" speak ...`
     * `auto-speech-autoplay-mode.md` -> `"$PROJECT_ROOT/bin/auto-speech" autoplay mode [ARGUMENT]`
     * `auto-speech-autoplay-on.md` -> `"$PROJECT_ROOT/bin/auto-speech" autoplay on`
     * `auto-speech-autoplay-off.md` -> `"$PROJECT_ROOT/bin/auto-speech" autoplay off`
     * `auto-speech-scope.md` -> `"$PROJECT_ROOT/bin/auto-speech" autoplay scope [ARGUMENT]`
     * `auto-speech-pause.md` -> `"$PROJECT_ROOT/bin/auto-speech" pause`
     * `auto-speech-resume.md` -> `"$PROJECT_ROOT/bin/auto-speech" resume`
     * `auto-speech-restart.md` -> `"$PROJECT_ROOT/bin/auto-speech" restart`
     * `auto-speech-end.md` -> `"$PROJECT_ROOT/bin/auto-speech" stop`
     * `auto-speech-seek.md` -> `"$PROJECT_ROOT/bin/auto-speech" seek TARGET`
     * `auto-speech-autoplay-status.md` -> `"$PROJECT_ROOT/bin/auto-speech" autoplay status`
     * `auto-speech-update.md` -> `"$PROJECT_ROOT/bin/auto-speech" update --force`
     * `auto-speech-narrate-on.md` -> `"$PROJECT_ROOT/bin/auto-speech" narrate on`
     * `auto-speech-narrate-off.md` -> `"$PROJECT_ROOT/bin/auto-speech" narrate off`
     * `auto-speech-narrate-status.md` -> `"$PROJECT_ROOT/bin/auto-speech" narrate status`
     * `auto-speech-narrate-stop.md` -> `"$PROJECT_ROOT/bin/auto-speech" narrate stop`
     * `auto-speech-narrate-config.md` -> `"$PROJECT_ROOT/bin/auto-speech" narrate config ACTION`
     * `auto-speech-app.md` preserves `start_webapp.sh` with root guard.
     * `auto-speech-narrate-install.md` preserves interactive MLX setup with root guard.
   - Grep verification over `plugin/commands`, `plugin/commands-extra`, and `bin` returned 0 occurrences of hardcoded `/Users/joshua` paths.
   - Python syntax test verified all extracted bash blocks pass `bash -n` with 0 syntax errors.

5. **Test Harness Expansion & Verification Results**:
   - `tests/test_narrator_hook.sh`: Added tests for Antigravity `conversationId`, dual precedence, global mute, and non-dict payloads -> 17 ran, 0 failed.
   - `tests/test_autoplay_shim.sh`: Added tests for Antigravity `conversationId`, Antigravity global mute, and non-dict payloads -> 17 ran, 0 failed.
   - `tests/test_cli_dispatcher.py`: Added assertions verifying positional args to `autoplay_worker.sh` -> 84 ran, 0 failed.
   - `tests/test_install_plugin.sh`: All 12 assertions passed.
   - `tests/run_all.sh`: All 50 test suites passed.
   - `ruff check plugin/`: 0 errors.

---

## 2. Logic Chain

1. **Dual-Format Parity**:
   - Claude Code emits `session_id` and `transcript_path`; Antigravity emits `conversationId` and `transcriptPath`.
   - By structuring the fallback query as `(.transcript_path // .transcriptPath // empty)` and `(.session_id // .conversationId // empty)`, both runtimes resolve cleanly without breaking backwards compatibility.

2. **Gating Performance (<50ms)**:
   - Placing pure-bash file and environment checks ahead of external process spawns reduces unenrolled/suppressed/muted hook latency to <8ms, well under the 50ms requirement.

3. **Subcommand Trampoline & Coherence**:
   - Routing 19 slash commands into `"$PROJECT_ROOT/bin/auto-speech"` delegates execution to unified Python logic (`cli.py`), eliminating divergence between CLI, MCP, and shell commands.

4. **Preservation of Interactive Invariants**:
   - Preserving `start_webapp.sh` in `auto-speech-app.md` and the interactive MLX installer in `auto-speech-narrate-install.md` maintains specialized interactive prompts while enforcing the project root guard.

---

## 3. Caveats

- No caveats. All changes strictly adhere to project invariants and are isolated to the git worktree `/Users/joshua/Developer/auto-speech-worktree`.

---

## 4. Conclusion

Milestone M3 (Hook Reconcile & Architectural Coherence) is 100% complete and fully verified in `/Users/joshua/Developer/auto-speech-worktree`. All 21 slash commands, both shell hooks, and the CLI hook dispatcher are unified, resilient against non-dict payloads, compatible with both Claude Code and Antigravity conventions, and 100% passing across all 50 regression test suites with 0 lint violations.

---

## 5. Verification Method

To independently verify this milestone from `/Users/joshua/Developer/auto-speech-worktree`:

```bash
cd /Users/joshua/Developer/auto-speech-worktree

# 1. Installer contract & command portability
bash tests/test_install_plugin.sh

# 2. Hook unit & gate suites
bash tests/test_autoplay_shim.sh
bash tests/test_narrator_hook.sh

# 3. CLI dispatcher unit suite
.venv/bin/python -m unittest tests/test_cli_dispatcher.py

# 4. Full repository test runner (all 50 suites)
bash tests/run_all.sh

# 5. Linter verification
.venv/bin/ruff check plugin/
```
