# Handoff Report: Shell Hooks, Test Harness Infrastructure, and Git Worktree Procedures

**Agent**: `explorer_survey_3`  
**Working Directory**: `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_3`  
**Date**: 2026-10-06  
**Status**: Completed (Hard Handoff)  

---

## 1. Observation

### Obs 1. Shell Hooks Architecture & Divergence
1. **`plugin/scripts/shell/autoplay_hook.sh` (165 lines)**:
   - Configured in `~/.claude/settings.json` lines 143–151 via `agent-event-router --event Stop --action autoplay` and directly via `setup/install-hook.sh`.
   - Lines 38–39 parse Claude Code and Antigravity payloads:
     ```bash
     TRANSCRIPT_PATH="$(printf '%s' "$PAYLOAD" | jq -r '(.transcript_path // .transcriptPath) // ""' 2>/dev/null || true)"
     SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r '(.session_id // .conversationId) // ""' 2>/dev/null || true)"
     ```
   - Filters execution through four gates: `AUTO_SPEECH_SUPPRESS_HOOKS` (lines 47–49), global mute `~/.claude/auto-speech.disabled` (lines 53–55), solo spotlight `~/.claude/auto-speech-autoplay-solo` (lines 63–69), and opt-in enrollment `~/.claude/auto-speech-autoplay-enabled/$SESSION_ID` (lines 81–84).
   - Starts daemon if dead (lines 87–104), touches per-session beacon `~/.claude/auto-speech-last-stop.$SESSION_ID` with nanosecond precision `BEACON_MTIME` (lines 109–129), and spawns detached worker via inline Python double-fork (`os.setsid()`) in lines 137–162.
2. **`plugin/scripts/shell/narrator_hook.sh` (123 lines)**:
   - Registers for `PreToolUse`, `PostToolUse`, `Stop`, and `UserPromptSubmit` in `~/.claude/settings.json` via `setup/install-narrator-hooks.sh`.
   - Lines 38–39:
     ```bash
     SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r '.session_id // ""' 2>/dev/null || true)"
     ```
     *Direct Observation*: Fails to inspect `.conversationId` (Antigravity's key), so Antigravity sessions fail the session gate at lines 43–45 and exit silently.
   - Lines 86–121: Decorates events with `{ts, cwd, pid, event, payload}` and writes to `/tmp/auto-speech-narrator-events.jsonl`.
3. **`~/.local/bin/agent-event-router` (114 lines)**:
   - Generic multiplexer on user's system. Lines 89–91 routes `--event Stop --action autoplay` to `~/Developer/auto-speech/plugin/scripts/shell/autoplay_hook.sh` piping stdin.
4. **`.agents/plugins/auto-speech/hooks.json` (12 lines)**:
   - Registers `agent-event-router --event Stop --action autoplay` for `Stop` with `timeout: 15`.

### Obs 2. Slash Commands and Invariant Counts
1. Curated keep-set in `plugin/commands/`: 8 files (`auto-speech-speak.md`, `auto-speech-replay.md`, `auto-speech-app.md`, `auto-speech-autoplay-on.md`, `auto-speech-autoplay-off.md`, `auto-speech-autoplay-mode.md`, `auto-speech-scope.md`, `auto-speech-doctor.md`).
2. Extra commands in `plugin/commands-extra/`: 13 files (`auto-speech-narrate-on.md`, `auto-speech-pause.md`, etc.).
3. `tests/test_install_plugin.sh` lines 31–33 and 63–65 assert:
   - `curated install links exactly 8 commands`
   - `--with-extras links 21 commands`
   - Adding or removing `.md` files directly in those directories will fail this test suite.

### Obs 3. Test Suite Infrastructure & Execution
1. Virtual environment: `.venv/bin/python` is Python 3.12.13, managed by `uv`.
2. `bash tests/run_all.sh`:
   - Iterates through 37 cheap Python tests, 8 shell tests, 1 web test, and 5 heavy tests without requiring pytest (`$PYTHON "$path"` and `bash "$path"`).
   - Execution command: `bash tests/run_all.sh`.
   - Result: `ran: 49, failed: 0, all tests passed` (exit code 0).
3. `tests/e2e/run_e2e.py`:
   - Uses `unittest.TestSuite` across 5 tiers (74 total tests).
   - Execution command: `.venv/bin/python tests/e2e/run_e2e.py --tier 5`.
   - Result: `Ran 33 tests in 18.61s. OK` (exit code 0).
4. `tests/test_mcp_server.py`:
   - Line 84: `def test_tools_list_has_only_speak()` asserts `[t["name"] for t in tools] == ["speak"]`.
   - Extending `tools/list` with 5 additional tools will require updating this assertion.

### Obs 4. Git Worktree and Branch Status
1. `git status`:
   - On branch `main`.
   - Ahead of `origin/main` by 10 commits.
   - Working tree for all tracked production and test code is clean.
   - Untracked files exist only under `.agents/teamwork/`.
2. `git worktree list`:
   - Currently only `/Users/joshua/Developer/auto-speech` is registered.
   - Worktree directory `.git/worktrees` does not yet exist.

---

## 2. Logic Chain

1. **Clean Hook Delegation (Connecting Obs 1 & Obs 2 to R3)**:
   - The shell hooks `autoplay_hook.sh` and `narrator_hook.sh` are invoked by Claude Code and Antigravity hooks on every turn and tool call.
   - Because `PreToolUse` fires frequently, bash pre-gating (<50ms) must remain intact to avoid starting a Python interpreter on un-enrolled sessions.
   - However, the business logic for handling the payload, checking enrollments, managing markers, and launching workers can be invoked via `auto-speech hook stop` and `auto-speech hook narrator`, unifying the CLI with the shell hooks while preserving zero regression for existing slash commands and `test_install_plugin.sh`.
   - Fixing `narrator_hook.sh:38` to include `(.session_id // .conversationId)` bridges Antigravity compatibility.
2. **MCP Parity Alignment (Connecting Obs 1 & Obs 3 to R2)**:
   - `mcp_server.py` currently advertises only `speak`.
   - The proposed new MCP tools (`autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`, `doctor`) map 1:1 to the domain logic already implemented in `MessageSelector`, `cli_rewrite`, `DaemonClient`, `control.py`, `autoplay_enrollment.py`, `autoplay_scope.py`, and `doctor.py`.
   - In `tests/test_mcp_server.py`, updating `test_tools_list_has_only_speak` to assert the full 6-tool suite ensures complete test coverage.
3. **Isolated Worktree Lifecycle (Connecting Obs 4 to R4)**:
   - Creating `git worktree add -b worktree/cli-mcp-parity /Users/joshua/Developer/auto-speech-worktree main` creates an isolated directory outside the main repo.
   - Symlinking `.venv` into the worktree (`ln -s /Users/joshua/Developer/auto-speech/.venv /Users/joshua/Developer/auto-speech-worktree/.venv`) ensures all tests and tools execute identically without duplicate MLX model downloads.
   - Bidirectional merge (merging `main` into `worktree/cli-mcp-parity`, testing, then merging `worktree/cli-mcp-parity` into `main`) guarantees zero integration failures.

---

## 3. Caveats

1. **Worktree Directory Location**: Creating the worktree inside `/Users/joshua/Developer/auto-speech/worktree/` would show up as untracked files on `main` unless added to `.gitignore`. Creating it at `/Users/joshua/Developer/auto-speech-worktree` (sibling directory) avoids repository pollution.
2. **Shared Unix Socket Port Contention**: When running the test suites or daemon inside a worktree, `AUTO_SPEECH_DAEMON_SOCK` defaults to `/tmp/auto-speech-daemon.sock`. Tests use isolated temporary socket paths (via `IsolatedEnvironment` or `tempfile`), so running tests inside the worktree does not conflict with any live system daemon.
3. **Log Rotation & File Permissions**: Shell hooks append to `/tmp/auto-speech-autoplay.log` and `/tmp/auto-speech-narrator-events.jsonl`. Permissions must remain user-writable.

---

## 4. Conclusion

1. **Hook Architecture**: `autoplay_hook.sh` is dual-compatible with Claude Code and Antigravity; `narrator_hook.sh` needs a 1-line update to support Antigravity's `.conversationId`.
2. **Unified CLI Dispatcher (R1 & R3)**: A single CLI dispatcher `auto-speech` can be implemented and symlinked to `~/.local/bin/auto-speech`, exposing `speak`, `replay`, `pause`, `resume`, `stop`, `restart`, `seek`, `autoplay`, `narrate`, `doctor`, and `hook`, delegating directly to shared Python domain logic.
3. **MCP Parity (R2)**: `mcp_server.py` is ready to be expanded with 5 additional tools (`autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`, `doctor`).
4. **Test Baseline (R3 & Verification)**: `bash tests/run_all.sh` passes 49/49 suites cleanly. Adding new tests for the CLI dispatcher and MCP tools will maintain 100% green status.
5. **Worktree Protocol (R4)**: An isolated worktree at `/Users/joshua/Developer/auto-speech-worktree` with a symlinked `.venv` provides the exact isolated, reproducible sandbox needed for implementation and bidirectional merge.

---

## 5. Verification Method

### 1. Test Suite Verification
Run the standard test runner and verify 0 failures:
```bash
bash tests/run_all.sh
```
Verify E2E adversarial suite:
```bash
.venv/bin/python tests/e2e/run_e2e.py --tier 5
```

### 2. File and Hook Verification
1. Inspect `plugin/scripts/shell/autoplay_hook.sh` lines 37–40 for session parsing.
2. Inspect `plugin/scripts/shell/narrator_hook.sh` lines 36–45 for session parsing.
3. Inspect `~/.local/bin/agent-event-router` lines 89–91 for Stop action routing.
4. Inspect `tests/test_install_plugin.sh` lines 31–33 for the 8/21 command count assertion.

### 3. Worktree Protocol Verification
Run the worktree setup sequence:
```bash
git worktree add -b worktree/cli-mcp-parity /Users/joshua/Developer/auto-speech-worktree main
ln -s /Users/joshua/Developer/auto-speech/.venv /Users/joshua/Developer/auto-speech-worktree/.venv
/Users/joshua/Developer/auto-speech-worktree/.venv/bin/python -c "import mlx_audio; print('Venv OK')"
git worktree remove /Users/joshua/Developer/auto-speech-worktree
git branch -D worktree/cli-mcp-parity
```

### Invalidation Conditions
- If Claude Code or Antigravity alters hook payload schemas without notice.
- If `tests/test_install_plugin.sh` fails due to uncoordinated modification of command markdown files.
