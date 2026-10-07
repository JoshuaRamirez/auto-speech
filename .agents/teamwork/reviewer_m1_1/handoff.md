# Review & Adversarial Critic Report: Milestone M1 (Unified auto-speech CLI Dispatcher)

**Reviewer:** `reviewer_m1_1` (teamwork_preview_reviewer)  
**Date:** 2026-10-06T21:38:30Z  
**Target:** Milestone M1 in `/Users/joshua/Developer/auto-speech-worktree`  
**Verdict:** **APPROVE**

---

## 1. Observation

1. **Worktree Baseline & Scope**:
   - Worktree path: `/Users/joshua/Developer/auto-speech-worktree` on branch `worktree/cli-mcp-parity`.
   - Files examined:
     - `bin/auto-speech` (lines 1–41)
     - `plugin/scripts/python/cli.py` (lines 1–1051)
     - `tests/test_cli_dispatcher.py` (lines 1–862)
     - `setup/install-plugin.sh` (lines 30–65)
     - `setup/uninstall.sh` (lines 65–82)
     - `tests/test_install_plugin.sh` (lines 37–46, 101–114)
     - `tests/run_all.sh` (lines 84–90)

2. **Empirical Test Verification**:
   - **Command**: `.venv/bin/python -m unittest tests/test_cli_dispatcher.py` (in `/Users/joshua/Developer/auto-speech-worktree`)
     - **Result**: `Ran 74 tests in 0.250s` -> `OK` (0 failures, 0 errors).
   - **Command**: `bash tests/run_all.sh` (in `/Users/joshua/Developer/auto-speech-worktree`)
     - **Result**: `ran: 50`, `failed: 0`, `all tests passed`.
   - **Command**: `.venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py`
     - **Result**: `All checks passed!` (0 lint errors).
   - **Command**: `bash tests/test_install_plugin.sh`
     - **Result**: `install-plugin: 12/12 passed` (verifying symlink creation and cleanup).

3. **Direct CLI Invocations**:
   - `~/.local/bin/auto-speech --help`: Exited `0`, printed synopsis covering all 12 subcommands.
   - `~/.local/bin/auto-speech --version`: Exited `0`, returned `auto-speech 0.2.0`.
   - `~/.local/bin/auto-speech doctor`: Exited `0`, displayed 13 probe findings (`HEALTHY`).
   - `~/.local/bin/auto-speech doctor --json`: Exited `0`, returned valid JSON object with `"healthy": true`.
   - `~/.local/bin/auto-speech autoplay status`: Exited `0`, reported enrollment and scope status cleanly.
   - `~/.local/bin/auto-speech narrate status`: Exited `0`, reported session status and daemon liveness cleanly.
   - `~/.local/bin/auto-speech pause`: Exited `2`, output: `"control: no active playback session (unified daemon uses synchronous audio)."`.

4. **Integrity & Anti-Cheat Audit**:
   - No hardcoded test results found in `cli.py` or `bin/auto-speech`.
   - No dummy facades or stubbed returns bypassing execution.
   - No fabricated verification outputs or logs detected.
   - Complete real computational logic implemented across all 12 subcommands.

5. **Decision Model Check**:
   - Attempted: `systemone round`
   - Observed: `ConnectionRefusedError: [Errno 61] Connection refused: is the daemon running?` (systemone launchd service is offline; checked `launchctl list | grep systemone` which returned empty).

---

## 2. Logic Chain

1. **Portability and Trampoline Robustness (`bin/auto-speech`)**:
   - The script uses `set -euo pipefail`.
   - Line 11–20 unwinds symlink hops in a `while [ -L "$SOURCE" ]` loop bounded by `MAX_HOPS=20`, resolving relative link hops (`[[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"`). This prevents infinite symlink recursion while correctly traversing `~/.local/bin/auto-speech` down to `/Users/joshua/Developer/auto-speech-worktree/bin/auto-speech`.
   - Checks `$PROJECT_ROOT/.venv/bin/python` for executability with clear remediation instructions if missing.
   - Replaces shell process via `exec "$VENV_PYTHON" "$CLI_PY" "$@"`, ensuring signal propagation and accurate process exit codes.

2. **Dispatcher Completeness and Protocol Fidelity (`cli.py`)**:
   - Implements top-level `argparse` configuration exposing 12 subcommands: `speak`, `replay`, `pause`, `resume`, `restart`, `stop`, `seek`, `autoplay`, `narrate`, `hook`, `doctor`, `update`.
   - Exit code matrix strictly aligns with `PROJECT.md` contracts:
     - `0`: Success (or cleanly muted)
     - `1`: General failure / daemon unreachable / doctor failure
     - `2`: No active session / invalid input / missing target / empty cache
     - `3`: mpv IPC failure
     - `4`: Invalid target argument
     - `6`: Playback failure
     - `130`: Interrupted (SIGINT)
   - `speak` handles positional tokens, stdin pipes, validates 64-hex `--source-hash`, checks global mute (`AutoplayGate`), routes via `DaemonClient.speak()` with fallback to `send_speech_request()`.
   - `replay` queries `CacheStore.list_by_recency()`, validates 1-indexed `--ordinal`, checks bounds, enforces global mute, routes playback to daemon socket or `NativeAudioSink`.
   - Transport controls (`pause`, `resume`, `restart`, `stop`, `seek`) check `_has_active_session()` and `_is_mpv_socket_alive()`. Absent sessions return code `2`; malformed seek offsets return code `4`.
   - `autoplay` handles `on`, `off`, `status`, `mode`, `scope` with defensive session ID extraction and TOML configuration writes.
   - `narrate` manages session markers in `~/.claude/auto-speech-narrate-sessions/`, validates daemon liveness, and exposes `status`, `stop`, and `config` (`show`, `path`, `edit`).
   - `hook` routes `stop`, `narrator`, `bootstrap`, handling both Claude Code payload schema (`session_id`, `transcript_path`) and Antigravity payload schema (`conversationId`, `transcriptPath`), respecting `AUTO_SPEECH_SUPPRESS_HOOKS` and global mute markers.
   - `doctor` and `update` wrap diagnostic and bootstrap routines accurately.

3. **Adversarial Analysis & Stress-Testing**:
   - *Empty/Whitespace stdin on speak*: Properly rejected with exit code 2 and descriptive stderr.
   - *Malformed source hash*: Validated against 64 hex characters, rejecting corrupt hashes with exit code 2.
   - *Missing session ID on enrollment*: Rejected with exit code 1.
   - *Malformed seek arguments*: Rejected with exit code 4.
   - *Missing playback session*: Exits cleanly with code 2 instead of crashing or hanging on socket timeouts.
   - *Global mute present*: Returns exit code 0 silently without synthesizing audio.
   - *Malformed JSON payload on hook invocation*: Safely parsed without uncaught exceptions, bailing cleanly with exit code 0.
   - *Signal handling*: `KeyboardInterrupt` caught at top level and main entrypoint, returning standard UNIX exit code 130.

4. **Integration with Build & Installer**:
   - `setup/install-plugin.sh` and `setup/uninstall.sh` maintain idempotent linking of `~/.local/bin/auto-speech`.
   - Portability invariant verified: no absolute `/Users/joshua` paths baked into tracked files.
   - Regression suite: all 50 test suites pass cleanly.

---

## 3. Caveats

1. **User Symlink Points to Worktree**:
   - `~/.local/bin/auto-speech` currently targets `/Users/joshua/Developer/auto-speech-worktree/bin/auto-speech`. This is normal during development in the isolated worktree. When Milestone M5 completes the merge into `main` and cleans up the worktree, `setup/install-plugin.sh` must be re-executed in the main repository so the symlink points to `/Users/joshua/Developer/auto-speech/bin/auto-speech`.
2. **Audio Hardware Mocking**:
   - Automated unit and regression tests run in a hermetic environment with mocked audio sinks and IPC sockets. Real hardware audio playback was not invoked to preserve environment quietness.
3. **Decision Model Daemon Offline**:
   - `systemone` daemon was offline on the host machine (`ConnectionRefusedError: [Errno 61]`), so judgment validation relied on rigorous empirical execution of all 50 test suites, lint checks, and manual CLI verification.

---

## 4. Conclusion

The Milestone M1 implementation is **APPROVED**.

- **Correctness**: Fully compliant with `PROJECT.md` and `ORIGINAL_REQUEST.md` requirements.
- **Completeness**: All 12 subcommands implemented, tested, and executable on PATH.
- **Robustness**: Error handling adheres to specification, exit codes are consistent, boundary conditions and malformed inputs are gracefully handled.
- **Integrity**: Zero cheating, zero facades, zero hardcoded shortcuts.
- **Regression**: 50/50 test suites passing, 74/74 unit tests passing, 0 ruff errors.

Codebase is ready to proceed to Milestone M2 (Expanded MCP Server Tool Suite).

---

## 5. Verification Method

To independently verify this milestone within `/Users/joshua/Developer/auto-speech-worktree`:

```bash
# 1. Run unit test suite for CLI dispatcher (74 tests)
.venv/bin/python -m unittest tests/test_cli_dispatcher.py

# 2. Run plugin installer and uninstaller integration assertions (12 tests)
bash tests/test_install_plugin.sh

# 3. Run entire test suite (50 test suites)
bash tests/run_all.sh

# 4. Verify code formatting and linting (0 errors)
.venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py

# 5. Verify direct CLI entrypoint and options on PATH
~/.local/bin/auto-speech --help
~/.local/bin/auto-speech --version
~/.local/bin/auto-speech doctor
~/.local/bin/auto-speech doctor --json
~/.local/bin/auto-speech autoplay status
~/.local/bin/auto-speech narrate status
~/.local/bin/auto-speech pause
```
