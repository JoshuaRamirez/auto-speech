# Handoff Report: Milestone M1 CLI Dispatcher Implementation Strategy

**Author:** `explorer_m1_1` (Teamwork Explorer)  
**Date:** 2026-10-06T21:15:00Z  
**Recipient:** `parent` (`4db9af0e-498d-49a5-831a-0009fd31326e`)  
**Scope:** Milestone M1 — Core Python CLI Dispatcher (`plugin/scripts/python/cli.py`) and Bash Trampoline (`bin/auto-speech`)

---

## 1. Observation

1. **Current Codebase State**:
   - The isolated git worktree exists at `/Users/joshua/Developer/auto-speech-worktree` on branch `worktree/cli-mcp-parity` (`git worktree list`).
   - The test baseline suite (`bash tests/run_all.sh`) executed cleanly across all 49 test suites with 0 failures (`ran: 49, failed: 0, all tests passed`).
   - Neither `bin/auto-speech` nor `plugin/scripts/python/cli.py` currently exists on disk in either `/Users/joshua/Developer/auto-speech` or `/Users/joshua/Developer/auto-speech-worktree`.

2. **Existing Subsystem Collaborators**:
   - `speak.py:120-213`: Uses `argparse` for `--ordinal`, `--keep-artifacts`, `--source-hash`, `--socket-path`, `--session-id`, `--priority`. Connects to `/tmp/auto-speech-daemon.sock` via `DaemonClient` or fallback socket; checks 64-hex hash and exits 2 if invalid.
   - `replay.py:37-118`: Accepts `--ordinal N` (default 1); checks `CacheStore.list_by_recency()`; exits 0 on success, 2 on invalid ordinal or empty cache, 6 on playback failure, 130 on SIGINT.
   - `control.py:1-66`: Subcommands `pause`, `resume`, `restart`, `end`, `seek TARGET`. Uses constants `EXIT_OK = 0`, `EXIT_NO_SESSION = 2`, `EXIT_IPC_FAIL = 3`, `EXIT_BAD_ARG = 4`.
   - `mpv_ipc.py:22-78`: `MpvIpc.send(command, socket_path)` handles one-shot JSON IPC over UNIX domain socket, raising `MpvIpcError` on failure.
   - `native_audio_sink.py:22-173`: Synchronous audio playback via `mpv` with PID written to `/tmp/auto-speech-mpv.pid` and clean process interruption via `interrupt()`.
   - `autoplay_gate.py:28-61`: `AutoplayGate().worker_gated_off()` checks global mute marker `~/.claude/auto-speech.disabled`.
   - `autoplay_scope.py:29-87`: `SoloScope()` manages spotlight marker `~/.claude/auto-speech-autoplay-solo`.
   - `autoplay_enrollment.py:40-95`: `SessionEnrollment()` manages per-session opt-in markers `~/.claude/auto-speech-autoplay-enabled/<session_id>`.
   - `autoplay_config.py:24-123`: Loads TOML config (`~/.config/auto-speech/autoplay.toml`). Valid modes: `raw`, `bypass`, `verbatim`, `small`, `medium`, `large`.
   - `narrator_config.py:35-70`: Loads TOML config (`~/.config/auto-speech/narrator.toml`). Exposes `load_config()`.
   - `doctor.py:60-287`: Runs 10 diagnostic health probes, returns `report.exit_code` (0 on OK/WARN, 1 on FAIL), formats via `--json` or text.
   - `self_update.py:28-55` & `setup/bootstrap.sh:1-72`: Compares `uv.lock` hash to `setup/.synced` stamp; `--force` performs synchronous `uv sync`.

3. **Requirement Specifications**:
   - `ORIGINAL_REQUEST.md` (header `## 2026-10-06T20:47:50Z`) §R1 defines the unified CLI dispatcher with subcommands: `speak`, `replay`, `pause`, `resume`, `stop`, `restart`, `seek`, `autoplay`, `narrate`, `hook`, `doctor`, `update`.
   - `PROJECT.md:88-96` specifies exact exit codes: `0` (OK/muted), `1` (general failure), `2` (no session/invalid input), `3` (IPC fail), `4` (bad arg), `6` (playback fail), `130` (interrupted).

---

## 2. Logic Chain

1. **Subcommand Architecture**:
   - Starting from Observation (3), the CLI requires 12 top-level subcommands.
   - Three subcommands have nested action hierarchies: `autoplay` (`on`, `off`, `status`, `mode`, `scope`), `narrate` (`on`, `off`, `status`, `stop`, `config`), and `hook` (`stop`, `narrator`, `bootstrap`).
   - Using standard library `argparse.ArgumentParser` with `add_subparsers()` satisfies all nesting and flag requirements cleanly without third-party dependencies.

2. **Input Resolution & Fallback Chain for `speak`**:
   - From Observation (2) (`speak.py`), input can come from positional words or piped `sys.stdin`.
   - If positional words are present, join with space.
   - If positional words are empty, check `sys.stdin.isatty()`: if not a TTY, read all of `sys.stdin`.
   - If interactive TTY and `--source-hash` is provided, empty text triggers cached playback via `DaemonClient.play_cache()`.
   - If interactive TTY with neither text nor hash, exit 2 with clear error on `sys.stderr`.
   - Global mute check via `AutoplayGate().worker_gated_off()` runs before any network or synthesis operation, emitting a notice to `sys.stderr` and exiting 0.

3. **Transport & IPC Mechanics**:
   - From Observation (2) (`control.py`, `mpv_ipc.py`), `/tmp/auto-speech-mpv.sock` is the communication channel.
   - If socket is absent, `pause`, `resume`, `restart`, `seek` immediately exit 2 (`EXIT_NO_SESSION`) with `control: no active playback session (unified daemon uses synchronous audio).`
   - If socket is present, `MpvIpc.send()` dispatches the command.
   - `seek` parses `+N`, `-N`, absolute `N`, and `'end'`. Any malformed target syntax immediately exits 4 (`EXIT_BAD_ARG`).
   - `stop` calls `DaemonClient.interrupt()` and also signals `/tmp/auto-speech-mpv.pid` if present, ensuring immediate playback termination and queue purge, exiting 0.

4. **Exit Code Orthogonality**:
   - Mapping each failure mode to its exact exit code guarantees full consistency with legacy CLI tools and test assertions:
     - `0`: Success (or cleanly muted)
     - `1`: Failure (general error / connection failure / doctor FAIL / update failure)
     - `2`: No active session / invalid input / missing target / empty cache
     - `3`: mpv IPC failure
     - `4`: Invalid target argument
     - `6`: Playback failure
     - `130`: Interrupted (SIGINT)

5. **Trampoline Portability**:
   - A bash trampoline `bin/auto-speech` resolving `.venv/bin/python` via symlink traversal prevents reliance on system Xcode Python (`/usr/bin/python3`), ensuring deterministic execution across interactive terminal, background launchd, and subagent shells.

---

## 3. Caveats

1. **Active Playback IPC Precondition**: mpv IPC commands (`pause`, `resume`, `restart`, `seek`) require an actively playing session where mpv was launched with `--input-ipc-server=/tmp/auto-speech-mpv.sock`. When idle, returning exit 2 is the intentional, verified contract.
2. **Terminal vs. Test STDIN**: In test harnesses, `sys.stdin` is patched with `io.StringIO`, which may not implement `.isatty()` or may report `False`. The dispatcher checks `getattr(sys.stdin, "isatty", lambda: False)()` to remain robust under test mocking.
3. **Daemon Auto-start vs. Direct Synthesis**: `DaemonClient.ensure_daemon_running()` automatically starts the narrator service if offline, preserving zero-touch auto-start behavior for `speak` and `narrate on`.

---

## 4. Conclusion

A complete, granular architectural design for `plugin/scripts/python/cli.py` and `bin/auto-speech` has been formulated and recorded in:
`/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_1/analysis.md`.

The specification defines:
- Top-level `argparse` router with `--help`, `--version`, and 12 subcommands (`speak`, `replay`, `pause`, `resume`, `restart`, `stop`, `seek`, `autoplay`, `narrate`, `hook`, `doctor`, `update`).
- Strict exit code compliance (`0`, `1`, `2`, `3`, `4`, `6`, `130`).
- Clear, prefixed diagnostic errors emitted to `sys.stderr`.
- Complete test plan for `tests/test_cli_dispatcher.py` covering all 12 subcommands and edge cases.

The design is ready for immediate implementation by `worker_m1`.

---

## 5. Verification Method

1. **Inspect Analysis**:
   View `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_1/analysis.md` to confirm all 12 subcommands, argument definitions, error cases, and exit codes are specified.
2. **Baseline Regression Verification**:
   Run `bash tests/run_all.sh` inside `/Users/joshua/Developer/auto-speech-worktree` to verify baseline integrity (49/49 tests pass).
3. **Future Implementation Verification**:
   Once implemented in worktree:
   - `python3 plugin/scripts/python/cli.py --help` exits 0.
   - `python3 plugin/scripts/python/cli.py seek invalid` exits 4.
   - `python3 plugin/scripts/python/cli.py pause` exits 2 (when idle).
   - `python3 -m unittest tests/test_cli_dispatcher.py` passes 100%.
