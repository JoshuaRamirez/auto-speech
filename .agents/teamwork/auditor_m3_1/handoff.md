# Forensic Audit Report: Milestone M3 (Hook Reconcile & Architectural Coherence)

**Work Product**: Milestone M3 in `/Users/joshua/Developer/auto-speech-worktree`  
**Profile**: General Project (Development Mode per `ORIGINAL_REQUEST.md`)  
**Auditor**: `teamwork_preview_auditor` (`auditor_m3_1`)  
**Verdict**: **CLEAN**  

---

### Phase Results
- **Hardcoded Output Detection**: PASS — No hardcoded test outputs, dummy constants, or fake string bypasses found in production code or test additions.
- **Facade Detection**: PASS — `cli.py`, `autoplay_hook.sh`, `narrator_hook.sh`, and `bin/auto-speech` contain genuine, complete implementations with real system calls, IPC, error propagation, and fallbacks.
- **Pre-populated Artifact Detection**: PASS — No pre-populated logs, result files, or fabricated test records exist in the repository or worktree.
- **Mock Bypass Detection in Production Code**: PASS — No mocks, test-mode shortcuts, or artificial success stubs in production files.
- **Portability Invariant (Zero Hardcoded Personal Paths)**: PASS — Grep across all 21 markdown commands in `plugin/commands/` and `plugin/commands-extra/`, `bin/auto-speech`, and `plugin/scripts/` confirmed zero instances of `/Users/joshua`.
- **Command Syntax & Root Resolution Guard**: PASS — All 21 commands enforce the canonical 2-line root resolution guard. All fenced bash code blocks verified via `bash -n` with 0 syntax errors.
- **Test Integrity & Behavioral Verification**: PASS — Tests perform genuine empirical assertions on process exit codes, log deltas, and state beacons. All 50 test suites passed in `bash tests/run_all.sh`.
- **Static Analysis & Linting**: PASS — `.venv/bin/ruff check plugin/` reported 0 errors across all source files.

---

## 1. Observation

1. **Git Worktree State & Changes**:
   - Location: `/Users/joshua/Developer/auto-speech-worktree` on branch `worktree/cli-mcp-parity`.
   - Modified tracked files:
     - `plugin/commands/` (8 files) and `plugin/commands-extra/` (13 files): 21 markdown commands updated.
     - `plugin/scripts/shell/autoplay_hook.sh`
     - `plugin/scripts/shell/narrator_hook.sh`
     - `setup/install-plugin.sh`, `setup/uninstall.sh`
     - `tests/run_all.sh`, `tests/test_autoplay_shim.sh`, `tests/test_install_plugin.sh`, `tests/test_narrator_hook.sh`
   - Untracked files verified:
     - `bin/auto-speech` (Bash venv trampoline)
     - `plugin/scripts/python/cli.py` (Unified CLI dispatcher, 1088 lines)
     - `tests/test_cli_dispatcher.py` (CLI unit & integration suite, 1056 lines)
     - `tests/test_challenger_harness.py`, `tests/test_challenger_m2_retry.py`

2. **Hook Scripts Inspection**:
   - `plugin/scripts/shell/autoplay_hook.sh`:
     - Lines 41-49: Fast-exit guards for `AUTO_SPEECH_SUPPRESS_HOOKS=1` and `DISABLED_MARKER` precede `jq` execution:
       ```bash
       if [[ "${AUTO_SPEECH_SUPPRESS_HOOKS:-}" == "1" ]]; then
           exit 0
       fi
       if [[ -e "$DISABLED_MARKER" ]]; then
           exit 0
       fi
       ```
     - Lines 53-56: Supports both Claude Code and Antigravity payload schemas:
       ```bash
       TRANSCRIPT_PATH="$(printf '%s' "$PAYLOAD" | jq -r '(.transcript_path // .transcriptPath // empty)' 2>/dev/null || true)"
       SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r '(.session_id // .conversationId // empty)' 2>/dev/null || true)"
       ```
     - Lines 58-85: Enforces solo scoping (`auto-speech-autoplay-solo`) and opt-in session enrollment (`auto-speech-autoplay-enabled/$SESSION_ID`).
     - Lines 87-105: Verifies daemon alive state via `narrator_pid_is_ours` and triggers daemon start if dormant.
     - Lines 138-163: Spawns detached double-forked worker executing `bash $WORKER $BEACON_MTIME $TRANSCRIPT_PATH $SESSION_ID`.
   - `plugin/scripts/shell/narrator_hook.sh`:
     - Lines 25-34: Checks `AUTO_SPEECH_SUPPRESS_HOOKS` and `$DISABLED_MARKER` before any processing.
     - Lines 43-46: Extracts `SESSION_ID` via `jq -r '(.session_id // .conversationId // empty)' 2>/dev/null || true`.
     - Lines 53-56: Verifies session enrollment marker in `~/.claude/auto-speech-narrate-sessions/$SESSION_ID`.
     - Lines 93-118: Validates JSON payload object type via `jq -e 'type == "object"'` to cleanly handle non-dict primitives without crashing or logging jq syntax errors.
     - Lines 128: Appends valid JSONL record to `/tmp/auto-speech-narrator-events.jsonl`.

3. **CLI Core Dispatcher (`plugin/scripts/python/cli.py`)**:
   - Total lines: 1088.
   - Implements all 12 subcommands: `speak`, `replay`, `pause`, `resume`, `restart`, `stop`, `seek`, `autoplay`, `narrate`, `hook`, `doctor`, `update`.
   - Exit codes strictly adhere to contract:
     - `EXIT_OK = 0`
     - `EXIT_FAILURE = 1`
     - `EXIT_NO_SESSION = 2` / `EXIT_INVALID_INPUT = 2` / `EXIT_NO_CACHE_ENTRY = 2`
     - `EXIT_IPC_FAIL = 3`
     - `EXIT_BAD_ARG = 4`
     - `EXIT_PLAYBACK_FAIL = 6`
     - `EXIT_INTERRUPTED = 130`
   - In `handle_hook("stop")` (lines 680-703):
     ```python
     beacon_mtime = "0"
     if beacon_file.is_file():
         try:
             beacon_mtime = str(beacon_file.stat().st_mtime)
         except OSError:
             beacon_mtime = "0"
     subprocess.Popen(
         [
             "bash",
             str(worker_script),
             beacon_mtime,
             transcript_path,
             session_id,
         ],
         stdout=subprocess.DEVNULL,
         stderr=subprocess.DEVNULL,
         stdin=subprocess.DEVNULL,
         start_new_session=True,
     )
     ```
     Positional argument order matches `autoplay_worker.sh` contract: `BEACON_MTIME, TRANSCRIPT_PATH, SESSION_ID`.
   - Global mute checks: `AutoplayGate().worker_gated_off()` is checked in `handle_speak`, `handle_replay`, and `handle_hook("stop")`.

4. **Slash Command Inspection (21 Commands)**:
   - Evaluated 8 files in `plugin/commands/` and 13 files in `plugin/commands-extra/`.
   - All 21 command markdown files implement the standard 2-line root guard:
     ```bash
     PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
     [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
     ```
   - 19 commands delegate directly to `"$PROJECT_ROOT/bin/auto-speech" <subcommand>`.
   - `auto-speech-app.md` preserves `start_webapp.sh` with the project root guard.
   - `auto-speech-narrate-install.md` preserves interactive MLX setup with the project root guard.
   - Grep verification across `plugin/commands`, `plugin/commands-extra`, `bin/auto-speech`, and `plugin/scripts` returned 0 occurrences of hardcoded `/Users/joshua`.
   - Fenced bash block syntax check via `bash -n` across all 21 command markdown files completed with 0 errors.

5. **Empirical Test Suite Execution**:
   - `bash tests/test_install_plugin.sh`: 12/12 passed (verifying root resolution, 8 curated links, 21 extras links, pruning, uninstall, and no hardcoded `/Users/joshua`).
   - `bash tests/test_autoplay_shim.sh`: 17/17 passed (verifying shim contract, opt-in gating, Antigravity `conversationId`, global mute override, and non-dict JSON handling).
   - `bash tests/test_narrator_hook.sh`: 17/17 passed (verifying daemon PID guard, opt-in gating, Antigravity `conversationId`, dual precedence, global mute, and non-dict payload tolerance).
   - `.venv/bin/python -m unittest tests/test_cli_dispatcher.py`: 84/84 tests passed in 0.821s.
   - `bash tests/run_all.sh`: 50/50 test suites passed (Ran: 50, Failed: 0).
   - `.venv/bin/ruff check plugin/`: All checks passed (0 lint violations).
   - `tests/test_challenger_harness.py`: All 7 adversarial scenarios passed.
   - `tests/test_challenger_m2_retry.py`: All 4 boundary checks passed.

---

## 2. Logic Chain

1. **Absence of Fakes & Mocks in Production Logic**:
   - Examination of `plugin/scripts/shell/autoplay_hook.sh`, `plugin/scripts/shell/narrator_hook.sh`, and `plugin/scripts/python/cli.py` shows complete, functional logic.
   - The CLI router directly invokes actual system facilities (`DaemonClient`, `MpvIpc`, `NativeAudioSink`, `Doctor`, `CacheStore`, `subprocess.run/Popen`).
   - Mocks in `test_cli_dispatcher.py` are standard unit test doubles for isolating socket/process calls during CLI parser testing; real integration behavior is empirically verified via `TestCliTrampolineIntegration` running `bin/auto-speech` as a real subprocess.

2. **Full Compatibility across Claude Code & Antigravity Runtimes**:
   - The fallback queries `(.transcript_path // .transcriptPath // empty)` and `(.session_id // .conversationId // empty)` in shell hooks and `cli.py` correctly extract metadata whether dispatched from Claude Code or Antigravity.
   - The precedence order preserves `session_id` when both keys are present.

3. **Correct Argument Contracts**:
   - The positional arguments dispatched by `cli.py:handle_hook("stop")` (`beacon_mtime`, `transcript_path`, `session_id`) match the signature required by `autoplay_worker.sh` (`BEACON_MTIME="${1:-0}"`, `TRANSCRIPT_PATH="${2:-}"`, `SESSION_ID="${3:-}"`).

4. **Portability and Defense Incompleteness**:
   - Every slash command is decoupled from static paths through `~/.config/auto-speech/root`.
   - Input validation exists across all subcommands (e.g. 64-hex verification on `--source-hash`, target verification on `seek`, non-dict JSON handling on hook payloads).

5. **Clean Verification**:
   - All tests run against live files in `/Users/joshua/Developer/auto-speech-worktree` pass without modifications or skips.

---

## 3. Caveats

- No caveats. The audit directly inspected all files in scope and executed all test suites in the isolated worktree `/Users/joshua/Developer/auto-speech-worktree`.

---

## 4. Conclusion

Milestone M3 (Hook Reconcile & Architectural Coherence) is authentic, fully implemented, resilient, portable, and free of integrity violations.

**Verdict**: **CLEAN**

---

## 5. Verification Method

To independently reproduce the forensic verification from `/Users/joshua/Developer/auto-speech-worktree`:

```bash
cd /Users/joshua/Developer/auto-speech-worktree

# 1. Verify absence of hardcoded personal paths in commands and scripts
grep -rq "/Users/joshua" plugin/commands plugin/commands-extra bin plugin/scripts

# 2. Verify bash syntax of all 21 slash commands
python3 -c "
import glob, subprocess, re
cmd_files = glob.glob('plugin/commands*/*.md')
for f in sorted(cmd_files):
    for b in re.findall(r'\`\`\`(?:bash)?\n(.*?)\`\`\`', open(f).read(), re.DOTALL):
        if 'PROJECT_ROOT' in b or 'auto-speech' in b or 'set ' in b:
            test_b = b.replace('[ARGUMENT]', 'arg').replace('ORDINAL', '1').replace('<MODEL>', 'test-model').replace('TARGET', '+5').replace('ACTION', 'show')
            res = subprocess.run(['bash', '-n'], input=test_b, text=True, capture_output=True)
            assert res.returncode == 0, f'Error in {f}: {res.stderr}'
print('All 21 commands pass bash -n')
"

# 3. Execute hook test suites
bash tests/test_autoplay_shim.sh
bash tests/test_narrator_hook.sh
bash tests/test_install_plugin.sh

# 4. Execute CLI unit & integration suite
.venv/bin/python -m unittest tests/test_cli_dispatcher.py

# 5. Execute full repository test suite (all 50 suites)
bash tests/run_all.sh

# 6. Execute linter
.venv/bin/ruff check plugin/
```
