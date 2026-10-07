# Handoff Report: R1 Unified `auto-speech` CLI Dispatcher Survey & Specification

**Date:** 2026-10-06T21:03:00Z  
**From:** `explorer_survey_1` (Teamwork Explorer)  
**To:** `parent` (`4db9af0e-498d-49a5-831a-0009fd31326e`)  
**Status:** Complete (Hard Handoff)  
**Artifacts Produced:**
- `analysis.md`: `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_1/analysis.md`
- `handoff.md`: `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_1/handoff.md`

---

## 1. Observation

1. **Test Suite Baseline**:
   - Command `bash tests/run_all.sh` executed against `/Users/joshua/Developer/auto-speech`.
   - Result: 49 test suites executed (35 cheap unit tests, 8 shell script tests, 1 web test, 5 heavy audio tests). All 49 suites passed cleanly (`ran: 49, failed: 0, all tests passed`).
2. **Current Entrypoints & Dispatch Fragmentation**:
   - `speak.py` (`plugin/scripts/python/speak.py:114-212`): reads transcript from `sys.stdin.read()`, resolves daemon socket via `DEFAULT_SOCKET_PATH` or `AUTO_SPEECH_DAEMON_SOCK`, and dispatches via `DaemonClient` or fallback socket byte stream.
   - `replay.py` (`plugin/scripts/python/replay.py:31-118`): accepts `--ordinal` (1-indexed), queries `CacheStore.list_by_recency()`, and sends `play_cache` to `DaemonClient` or fallback `NativeAudioSink`. Exits `2` when no entries found.
   - `control.py` (`plugin/scripts/python/control.py:8-62`): implements subcommands `pause`, `resume`, `restart`, `end`, and `seek TARGET`. Exit codes defined: `EXIT_OK = 0`, `EXIT_NO_SESSION = 2`, `EXIT_IPC_FAIL = 3`, `EXIT_BAD_ARG = 4`. `_ensure_session()` emits `"control: no active playback session (unified daemon uses synchronous audio)."` to stderr and returns exit code 2.
   - Verified contract in `tests/test_replay_control.py:25-57`: explicitly asserts that `control.main(["pause"])`, `["resume"]`, `["restart"]`, `["end"]`, and `["seek", "+15"]` return `EXIT_NO_SESSION = 2`.
   - `doctor.py` (`plugin/scripts/python/doctor.py:60-287`): defines class `Doctor` with probes for binaries (`mpv`, `uv`, `jq`), venv, disk space, log sizes, daemon PID, queue depth, scope, MCP registration, configs, and updates. Supports `--json`. Returns `report.exit_code` (0 on OK/WARN, 1 on FAIL).
   - `self_update.py` (`plugin/scripts/python/self_update.py:44-55`) and `setup/bootstrap.sh:1-72`: handles dependency sync checking between `uv.lock` and `setup/.synced`. `bootstrap.sh --force` runs synchronous `uv sync`.
3. **Autoplay & Narration Gating Markers**:
   - Autoplay enrollment marker (`autoplay_enrollment.py:33-52`): `~/.claude/auto-speech-autoplay-enabled/<session_id>`.
   - Autoplay spotlight solo marker (`autoplay_scope.py:26-47`): `~/.claude/auto-speech-autoplay-solo`.
   - Global mute marker (`autoplay_gate.py`): `~/.claude/auto-speech.disabled`.
   - Narrator session marker (`narrator_hook.sh:46-49`): `~/.claude/auto-speech-narrate-sessions/<session_id>`.
   - Autoplay mode config (`autoplay_config.py:48-52`): `~/.config/auto-speech/autoplay.toml`.
   - Narrator config (`narrator_config.py`): `~/.config/auto-speech/narrator.toml`.
4. **Lifecycle Hooks & External Dispatching**:
   - `autoplay_hook.sh` (`plugin/scripts/shell/autoplay_hook.sh:34-165`): processes Stop hook payload from stdin, extracts session ID and transcript path, checks gating, touches per-session beacon, and detaches `autoplay_worker.sh`.
   - `agent-event-router` (`/Users/joshua/.local/bin/agent-event-router:89-92`): contains `Stop) if [ "$ACTION" == "autoplay" ]; then run_hook "~/Developer/auto-speech/plugin/scripts/shell/autoplay_hook.sh"`.
5. **Path & Symlink Conventions**:
   - User PATH includes `~/.local/bin` (verified active symlinks: `itg`, `svc`, `sysobs`, `systemone`).
   - `setup/install-plugin.sh` (`setup/install-plugin.sh:29-32`) records clone path into `~/.config/auto-speech/root` and symlinks 8 curated commands to `~/.claude/commands/`.
   - `tests/test_install_plugin.sh:98-100` strictly asserts: `grep -rq "/Users/joshua" "$PROJECT_ROOT/plugin/commands" ...` must not find hardcoded user paths.

---

## 2. Logic Chain

1. **Environment Isolation & Interpreter Guarantees**:
   - From Observation 1 & 5: Auto-speech depends on Python 3.12+ and packages in `.venv` (`mlx-audio`, `flask`, `misaki`, `tomllib`). System Python on macOS defaults to Python 3.9 in `/usr/bin/python3`.
   - Therefore, a direct Python shebang in `auto-speech` (`#!/usr/bin/env python3`) is unsafe and causes `ModuleNotFoundError` when invoked from bare shells or launchd.
   - Reasoning: The dispatcher executable must be a bash trampoline (`bin/auto-speech`) that dynamically resolves `PROJECT_ROOT` (traversing symlinks via `readlink` loop) and invokes `$PROJECT_ROOT/.venv/bin/python "$PROJECT_ROOT/plugin/scripts/python/cli.py" "$@"`.
2. **Subcommand Routing & Contract Compatibility**:
   - From Observation 2: Existing callers expect specific exit codes and stderr diagnostics. Specifically, transport commands (`pause`, `resume`, `stop`, `restart`, `seek`) must return 0 on successful action, 2 (`EXIT_NO_SESSION`) when no playback session is active, and 4 (`EXIT_BAD_ARG`) on malformed seek targets.
   - For `speak`: Callers may provide text as CLI positional arguments (`auto-speech speak "hello"`) or pipe it via stdin (`echo "hello" | auto-speech speak`). When positional arguments exist, they take precedence; when absent and stdin is piped, stdin is read.
   - For `replay`: Must delegate to `CacheStore` recency lookup, returning 0 on playback or 2 when out of bounds, matching `test_replay_control.py`.
3. **Transport Mechanism**:
   - From Observation 2: `NativeAudioSink` plays synchronously via `mpv`. To support live transport operations (`pause`, `resume`, `restart`, `seek`) without reintroducing detached process sprawl, `NativeAudioSink` should start `mpv` with `--input-ipc-server=/tmp/auto-speech-mpv.sock`. `MpvIpc.send()` can then issue commands directly while playback is active. If the socket is absent, it cleanly returns `EXIT_NO_SESSION = 2`.
   - For `stop`: Dispatches `DaemonClient.interrupt()`, which immediately terminates `mpv` and purges queued items, returning 0.
4. **Subsystem Unification**:
   - From Observation 3 & 4: All autoplay and narrator configuration commands currently run inline bash in markdown files. Unifying them into `auto-speech autoplay {on|off|status|mode|scope}` and `auto-speech narrate {on|off|status|stop|config}` collapses fragmented logic into testable Python code while retaining identical file markers and behavioral semantics.
   - Hook commands (`auto-speech hook {stop|narrator|bootstrap}`) allow both programmatic invocation and clean delegation from `agent-event-router` and Claude Code hooks.
5. **Zero-Hardcoded-Path Invariant**:
   - From Observation 5: All code and generated symlinks must use `$PROJECT_ROOT` or `~/.config/auto-speech/root`, maintaining complete portability across machines without hardcoding `/Users/joshua`.

---

## 3. Caveats

1. **Active Playback IPC Socket**:
   - Implementing interactive `pause`/`resume`/`restart`/`seek` on active playback requires `NativeAudioSink` to pass `--input-ipc-server=/tmp/auto-speech-mpv.sock`. If `mpv` is run without this socket, transport operations will continue to return `EXIT_NO_SESSION = 2` (which passes current unit tests, but doesn't pause active audio). The implementer should ensure `NativeAudioSink` adds this socket parameter.
2. **Standard Input Blocking**:
   - If `auto-speech speak` is invoked without arguments from an interactive TTY, it must not hang indefinitely waiting for stdin. The dispatcher should check `sys.stdin.isatty()`: if interactive and no `--source-hash` is provided, it must output an error and exit 2 immediately.
3. **Worktree Isolation**:
   - Per requirements, implementation of code changes must take place in an isolated Git worktree branch (`worktree/cli-mcp-parity`), verified, bidirectionally merged, and cleaned up. This survey produced zero modifications to tracked production code.

---

## 4. Conclusion

1. **Feasibility**: Full architectural consolidation into a single `auto-speech` CLI dispatcher (linked to `~/.local/bin/auto-speech`) is straightforward and completely backwards-compatible with all existing tests.
2. **Architecture**:
   - Shell trampoline: `/Users/joshua/Developer/auto-speech/bin/auto-speech` (executable `rwxr-xr-x`, resolving virtualenv).
   - Python core dispatcher: `plugin/scripts/python/cli.py` (structured with `argparse`).
   - Symlink: `~/.local/bin/auto-speech -> /Users/joshua/Developer/auto-speech/bin/auto-speech`.
3. **Command Surface**:
   - `speak`, `replay`, `pause`, `resume`, `stop`, `restart`, `seek`
   - `autoplay`: `on`, `off`, `status`, `mode`, `scope`
   - `narrate`: `on`, `off`, `status`, `stop`, `config`
   - `hook`: `stop`, `narrator`, `bootstrap`
   - `doctor`, `update`
4. **Complete Specification Delivered**:
   - The full, granular specification with parameter schemas, exit codes, and implementation blueprints is documented in:
     `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_1/analysis.md`

---

## 5. Verification Method

To independently verify the survey findings:

1. **Verify Baseline Test Suite**:
   ```bash
   bash tests/run_all.sh
   ```
   *Expected outcome*: 49 suites pass cleanly with 0 failures.
2. **Verify Control Subsystem Exit Code Contract**:
   ```bash
   .venv/bin/python tests/test_replay_control.py
   ```
   *Expected outcome*: All tests pass, confirming exit code 2 for absent session.
3. **Verify Portability & Symlink Invariants**:
   ```bash
   bash tests/test_install_plugin.sh
   ```
   *Expected outcome*: 10/10 tests pass, confirming no hardcoded absolute paths.
4. **Verify User PATH & Local Bin**:
   ```bash
   ls -la ~/.local/bin
   ```
   *Expected outcome*: Confirms `~/.local/bin` exists and contains user developer tools.
