# Architectural Survey & Specification: Unified `auto-speech` CLI Dispatcher (R1)

**Timestamp:** 2026-10-06T21:00:00Z  
**Author:** Teamwork Explorer Survey Agent (`explorer_survey_1`)  
**Target Requirement:** R1: Unified `auto-speech` CLI Dispatcher  
**Context:** Worktree-based CLI & MCP feature parity expansion for `auto-speech`

---

## 1. Executive Summary & Objective

The `auto-speech` subsystem has evolved from early standalone bash/python scripts through the M1–M4 architectural sublimation (which established the unified daemon `narrator_service.py`, in-memory priority queue, closed-loop `CacheStore`, and `NativeAudioSink`). However, client interactions are currently fragmented across multiple distinct entrypoints:
- **Loose Python scripts**: `speak.py`, `replay.py`, `control.py`, `doctor.py`, `autoplay_config.py`, `autoplay_scope.py`, `self_update.py`.
- **Shell wrappers & hooks**: `run_control.sh`, `run_replay.sh`, `autoplay_hook.sh`, `narrator_hook.sh`, `narrator_service_start.sh`, `narrator_service_stop.sh`, `narrator_service_status.sh`, `bootstrap.sh`.
- **Slash command markdown files**: 8 curated commands in `plugin/commands/` and 13 extra commands in `plugin/commands-extra/`, each executing ad-hoc bash snippets.
- **External event routing**: `agent-event-router` in `~/.local/bin/` calling direct shell scripts.

The objective of **Requirement 1 (R1)** is to build a single, authoritative command-line executable named `auto-speech` (linked into `~/.local/bin/auto-speech`) providing a cohesive, consistent interface for all user, agent, and hook operations.

This document presents a comprehensive forensic survey of all existing scripts, state files, sockets, and entrypoints in `/Users/joshua/Developer/auto-speech`, followed by a complete architectural specification for the unified CLI dispatcher.

---

## 2. Forensic Inventory of Existing Scripts & Entrypoints

### 2.1 Python Scripts in `plugin/scripts/python/`

A forensic scan of the 70 Python files in `plugin/scripts/python/` reveals the following command handlers and domain collaborators:

| File | Primary Role & Invocation Contract | Key Inputs / Options | Output & Exit Codes |
|---|---|---|---|
| `speak.py` | Thin CLI client for text-to-speech synthesis via daemon socket | Reads transcript text from `sys.stdin`; accepts `--source-hash`, `--session-id`, `--priority`, `--socket-path`, `--ordinal`, `--keep-artifacts` | Exits 0 on success/empty; 1 on socket failure; 2 on bad hash |
| `replay.py` | Plays recent cached speech by recency ordinal | `--ordinal N` (1-indexed, default 1); `--keep-artifacts` | Exits 0 on success; 2 if no cache entry or out of bounds; 6 on playback fail; 130 on SIGINT |
| `control.py` | Playback transport controller for pause/resume/restart/end/seek | Subcommands: `pause`, `resume`, `restart`, `end`, `seek TARGET` | Exits 0 on success; 2 on `EXIT_NO_SESSION`; 3 on IPC fail; 4 on bad argument |
| `daemon_client.py` | High-performance, zero-subprocess Python client for daemon socket | Connects to `/tmp/auto-speech-daemon.sock`; methods: `speak()`, `play_cache()`, `interrupt()`, `status()`, `ensure_daemon_running()` | Raises `ConnectionError` on communication failure; returns `DaemonResponse` |
| `native_audio_sink.py` | Synchronous, blocking audio sink using `mpv` with PID tracking | `play(wav_path, timeout)` records PID in `/tmp/auto-speech-mpv.pid`; `interrupt()` terminates active playback | Raises `AudioSinkError`, `MpvNotInstalledError`, `PlaybackError` |
| `autoplay_worker.py` | End-of-turn autoplay orchestrator | Invoked by `autoplay_worker.sh`; accepts `beacon_mtime`, `transcript_path`, `session_id` | ALWAYS exits 0; logs failures to `/tmp/auto-speech-autoplay.log` |
| `autoplay_config.py` | Autoplay configuration loader (mode & summary size) | Reads TOML from env, `~/.config/auto-speech/autoplay.toml`, or fallback | Returns dict with mode, summary_size, prompt_path, coalesce, etc. Exits 0 |
| `autoplay_scope.py` | Manages autoplay solo spotlight marker (`~/.claude/auto-speech-autoplay-solo`) | Methods: `current()`, `mode()`, `session_allowed()`, `set_solo()`, `clear()`, `format_status()` | Exits 0; outputs status string |
| `autoplay_enrollment.py` | Manages per-session opt-in enrollment markers (`~/.claude/auto-speech-autoplay-enabled/<sid>`) | Methods: `is_enrolled()`, `enroll()`, `withdraw()`, `enrolled_ids()`, `format_status()` | Exits 0 |
| `autoplay_gate.py` | Evaluates global disable marker (`~/.claude/auto-speech.disabled`) | `worker_gated_off()`, `cli_gated_off()` | Returns bool |
| `narrator_service.py` | Central audio daemon & real-time tool narration server | Listens on UNIX socket `/tmp/auto-speech-daemon.sock`; owns `NativeAudioSink` and `PriorityArbiter`; tails events from `/tmp/auto-speech-narrator-events.jsonl` | Daemon loop; PID written to `/tmp/auto-speech-narrator-daemon.pid` |
| `narrator_config.py` | Narrator configuration loader (`~/.config/auto-speech/narrator.toml`) | Loads TOML config; exposes `load_config()`, `resolve_config_path()` | Exits 0; dumps JSON |
| `narrator_state.py` | Thread-safe engine state machine (IDLE, SYNTHESIZING, PLAYING, PURGING) | Enforces valid state transitions | Raises `InvalidStateTransitionError` on illegal jump |
| `doctor.py` | One-shot system health probe | Options: `--json`; probes binaries, venv, disk, logs, daemon, queue, scope, MCP, configs, updates | Exits 0 on OK/WARN; 1 on FAIL |
| `health_report.py` | Structured diagnostic report data model for `doctor.py` | `HealthReport`, `CheckItem`, `Status` (OK, WARN, FAIL) | Formats to text or JSON |
| `self_update.py` | Standard-library lockfile sync checker | Subcommands: `check <lock> <stamp>`, `record <lock> <stamp>` | Exits 0 if sync needed (check) / recorded; 1 if up-to-date; 2 on bad arg |
| `mcp_server.py` | JSON-RPC 2.0 MCP server over stdio | Exposes `speak(text)` tool; dispatches to `DaemonClient.speak()` | Exits 0 |
| `mpv_ipc.py` | One-shot JSON IPC client for `mpv` domain socket | Sends JSON commands to socket; returns JSON response | Raises `MpvIpcError` |

---

### 2.2 Shell Scripts in `plugin/scripts/shell/`

| Script | Functionality | Calling Context |
|---|---|---|
| `autoplay_hook.sh` | Stop hook entrypoint: checks global mute, solo scope, enrollment, touches beacon, detaches `autoplay_worker.sh` | Called by Claude Code Stop hook / `agent-event-router --event Stop --action autoplay` |
| `autoplay_worker.sh` | Thin bash wrapper launching `autoplay_worker.py` under `.venv` | Spawned detached by `autoplay_hook.sh` |
| `autoplay_status.sh` | Formats multi-line report on enrollment, scope, global mute, mpv status, and log tail | Called by `/auto-speech-autoplay-status` slash command |
| `narrator_hook.sh` | PreToolUse/PostToolUse/Stop/UserPromptSubmit hook: appends JSONL event to `/tmp/auto-speech-narrator-events.jsonl` | Called by Claude Code hooks for tool narration |
| `narrator_service_start.sh` | Double-fork daemon spawner; checks pid liveness, rotates logs, launches `narrator_service.py` | Called by `/auto-speech-narrate-on` and lazy daemon auto-spawners |
| `narrator_service_stop.sh` | Sends `SIGTERM` (escalating to `SIGKILL`) to daemon PID after verifying PID identity | Called by `/auto-speech-narrate-stop` |
| `narrator_service_status.sh` | Inspects daemon PID, queue depth, markers, and log tail | Called by `/auto-speech-narrate-status` |
| `run_control.sh` | Trampoline activating `.venv` and calling `control.py "$@"` | Called by slash commands `/auto-speech-pause`, `/auto-speech-resume`, etc. |
| `run_replay.sh` | Trampoline activating `.venv` and calling `replay.py "$@"` | Called by `/auto-speech-replay` |
| `run_mcp.sh` | Trampoline activating `.venv` and launching `mcp_server.py` | Configured in `.mcp.json` or `~/.claude.json` |

---

### 2.3 Slash Commands & Hook Configurations

1. **Curated Slash Commands (`plugin/commands/`)**:
   - `auto-speech-speak.md`: invokes `speak.py`
   - `auto-speech-replay.md`: invokes `run_replay.sh`
   - `auto-speech-app.md`: launches `start_webapp.sh`
   - `auto-speech-autoplay-on.md`: touches `~/.claude/auto-speech-autoplay-enabled/$SESSION_ID`
   - `auto-speech-autoplay-off.md`: removes enrollment marker
   - `auto-speech-autoplay-mode.md`: shows/sets mode in `autoplay.toml`
   - `auto-speech-scope.md`: sets/clears `~/.claude/auto-speech-autoplay-solo`
   - `auto-speech-doctor.md`: runs `doctor.py`

2. **Extra Slash Commands (`plugin/commands-extra/`)**:
   - `auto-speech-pause.md`: calls `run_control.sh pause`
   - `auto-speech-resume.md`: calls `run_control.sh resume`
   - `auto-speech-restart.md`: calls `run_control.sh restart`
   - `auto-speech-end.md`: calls `run_control.sh end`
   - `auto-speech-seek.md`: calls `run_control.sh seek TARGET`
   - `auto-speech-autoplay-status.md`: calls `autoplay_status.sh`
   - `auto-speech-narrate-on.md`: touches session marker, calls `narrator_service_start.sh`
   - `auto-speech-narrate-off.md`: removes session marker
   - `auto-speech-narrate-status.md`: calls `narrator_service_status.sh`
   - `auto-speech-narrate-stop.md`: calls `narrator_service_stop.sh`
   - `auto-speech-narrate-config.md`: shows/edits `narrator.toml`
   - `auto-speech-update.md`: calls `setup/bootstrap.sh --force`

3. **External Multiplexing (`agent-event-router`)**:
   - Located at `/Users/joshua/.local/bin/agent-event-router`.
   - Wired in `~/.claude/settings.json` under hooks (`Stop`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `SessionStart`).
   - Under `Stop` with `--action autoplay`: routes to `~/Developer/auto-speech/plugin/scripts/shell/autoplay_hook.sh`.

---

## 3. Subsystem Deep-Dive & Current Mechanics

### 3.1 Speech Synthesis & Replay
- **Synthesis (`speak.py`)**:
  - Thin client. In steady state, connects to `/tmp/auto-speech-daemon.sock`.
  - Supports dual-wire protocol:
    - Wire 1: Framed JSON line `{"action": "speak", "text": "...", "priority": 2, ...}` -> replies with JSON.
    - Wire 2 (Legacy): Raw UTF-8 byte stream closed with `SHUT_WR`.
  - If daemon is offline, automatically launches `narrator_service_start.sh` and polls socket readiness (up to 25 attempts @ 40ms = 1s).
  - Handles empty text with `--source-hash` by playing pre-cached audio via `play_cache` action.
- **Replay (`replay.py`)**:
  - Queries `CacheStore` in `config/cache/<hash_prefix>/full.wav`.
  - Sorts entries by `created_at` timestamp.
  - Takes 1-indexed `--ordinal N` (1 = newest).
  - Routes playback through `DaemonClient.play_cache(entry.source_hash)` so audio hardware remains strictly owned by the daemon.

### 3.2 Playback Transport Controls
- Current `control.py` was stubbed during daemon consolidation:
  `control._ensure_session()` returns `EXIT_NO_SESSION = 2` ("control: no active playback session (unified daemon uses synchronous audio)").
- Current `native_audio_sink.py` spawns `mpv` synchronously with arguments:
  `[mpv_bin, "--really-quiet", "--no-video", "--keep-open=no", "--idle=no", str(wav_path)]`
  and writes the PID to `/tmp/auto-speech-mpv.pid`.
- **Target Transport Architecture**:
  1. `NativeAudioSink` launches `mpv` with `--input-ipc-server=/tmp/auto-speech-mpv.sock`.
  2. While audio is actively playing, `/tmp/auto-speech-mpv.sock` exists and accepts JSON commands via `MpvIpc.send()`.
  3. Commands map as follows:
     - `pause`: `["set_property", "pause", true]` -> returns exit 0 ("paused").
     - `resume`: `["set_property", "pause", false]` -> returns exit 0 ("resumed").
     - `restart`: `["seek", 0, "absolute"]` -> returns exit 0 ("restarted").
     - `seek TARGET`: parses `+N`, `-N`, `N`, or `'end'` -> `["seek", N, "relative"|"absolute"]` -> returns exit 0.
     - `stop`: calls `DaemonClient.interrupt()`, which immediately terminates `mpv` process and purges queued audio items -> returns exit 0 ("stopped").
  4. When no audio is playing, `/tmp/auto-speech-mpv.sock` is absent -> commands return exit code 2 ("no active playback session"), preserving 100% backward compatibility with `tests/test_replay_control.py`.

### 3.3 Autoplay Lifecycle, Scopes, and Modes
- **Session Enrollment (`autoplay_enrollment.py`)**:
  - Opt-in model: markers are files in `~/.claude/auto-speech-autoplay-enabled/<session_id>`.
  - Global mute marker: `~/.claude/auto-speech.disabled`. If present, all sessions are muted.
- **Solo Scope Spotlight (`autoplay_scope.py`)**:
  - Marker file: `~/.claude/auto-speech-autoplay-solo` containing single session ID.
  - Scope `ALL`: marker absent -> all enrolled sessions play.
  - Scope `SOLO`: marker present -> only the specified session plays; all others muted.
- **Autoplay Mode & Rewrite (`autoplay_config.py`, `cli_rewrite.py`)**:
  - Config file: `~/.config/auto-speech/autoplay.toml`.
  - Modes:
    - `raw` / `bypass`: bypasses LLM rewriting; speaks raw assistant text.
    - `verbatim`: full audio-friendly phonetic/text rewrite prompt.
    - `small`: summary with small size constraint (~1-2 sentences).
    - `medium`: summary with medium size constraint (~3-4 sentences).
    - `large`: comprehensive summary.

### 3.4 Narration Subsystem
- **Daemon Lifecycle**:
  - Managed by `narrator_service_start.sh` (double-fork detached python process) and `narrator_service_stop.sh` (identity-checked PID termination).
  - Process PID written to `/tmp/auto-speech-narrator-daemon.pid`.
  - Idle shutdown timeout defaults to 600 seconds.
- **Session Enrollment**:
  - Per-session markers: `~/.claude/auto-speech-narrate-sessions/<session_id>`.
  - Deprecated legacy per-project marker: `<cwd>/.claude/narrate.enabled`.
- **Configuration**:
  - File: `~/.config/auto-speech/narrator.toml`.
  - Configurable parameters: `idle_shutdown_seconds`, `max_queue_depth`, `summarizer_model`, `temperature`, etc.

### 3.5 Lifecycle Hooks & Event Ingestion
- **Stop Hook (`autoplay_hook.sh`)**:
  - Reads hook payload JSON from stdin.
  - Extracts `session_id` and `transcript_path`.
  - Verifies: `AUTO_SPEECH_SUPPRESS_HOOKS != 1`, global mute absent, solo scope permits session, session is enrolled.
  - Touches beacon `/tmp/auto-speech-last-stop.<session_id>`, captures sub-second mtime.
  - Spawns `autoplay_worker.sh` detached.
- **Narrator Hook (`narrator_hook.sh`)**:
  - Receives `PreToolUse`, `PostToolUse`, `Stop`, or `UserPromptSubmit` payload on stdin.
  - Filters by session marker.
  - Appends decorated event record `{ts, cwd, pid, event, payload}` to `/tmp/auto-speech-narrator-events.jsonl`.
- **Bootstrap Hook (`setup/bootstrap.sh`)**:
  - Executed on `SessionStart`.
  - Checks if `uv.lock` has changed compared to `setup/.synced` hash via `self_update.py`.
  - Runs background `uv sync` if changed; instant no-op if unchanged.

### 3.6 Diagnostics & Updates
- **Doctor (`doctor.py`)**:
  - Evaluates 10 distinct health probes: binaries (`mpv`, `uv`, `jq`), venv interpreter, `/tmp` disk free space, log file sizes and rotation, narrator daemon process liveness, narration queue depth, autoplay enrollment and scope, MCP server registration, config TOML syntax, and lockfile synchronization.
  - Supports `--json` for programmatic consumption.
  - Exits 0 on all OK or WARN; exits 1 if any probe is FAIL.
- **Update (`setup/bootstrap.sh --force`)**:
  - Runs synchronous `uv sync`, maintaining `--extra narrate` if MLX is present.
  - Updates `setup/.synced` stamp on success.

---

## 4. Architectural Specification for Unified `auto-speech` CLI Dispatcher

### 4.1 Dispatcher Binary Layout & Environment Trampoline

To guarantee reliable execution across interactive terminals, non-interactive shells, Claude Code subshells, and background launchd processes, the entrypoint uses a two-tier layout:

```
/Users/joshua/Developer/auto-speech/
├── bin/
│   └── auto-speech                  # Executable bash trampoline (resolves project root & venv)
├── plugin/scripts/python/
│   └── cli.py                       # Pure Python unified dispatcher & argument router
└── ~/.local/bin/
    └── auto-speech -> /Users/joshua/Developer/auto-speech/bin/auto-speech
```

#### Why the Trampoline Pattern is Required
macOS default Python (`/usr/bin/python3`) is Xcode's Python 3.9, which lacks required runtime dependencies (`mlx-audio`, `flask`, `tomllib`). A Python shebang `#!/usr/bin/env python3` would resolve to whatever Python happens to be on the user's `PATH`.

The `bin/auto-speech` bash trampoline guarantees deterministic execution:
1. Resolves symlinks using a `readlink` loop to identify true `PROJECT_ROOT`.
2. Locates `$PROJECT_ROOT/.venv/bin/python`.
3. If `.venv` is missing, checks `$HOME/.config/auto-speech/root` as fallback.
4. If still missing, prints a clear diagnostic on stderr and exits 1.
5. Uses `exec "$VENV_PYTHON" "$PROJECT_ROOT/plugin/scripts/python/cli.py" "$@"` to hand off control cleanly without residual shell processes.

### 4.2 CLI Command Hierarchy

```
auto-speech [-h] [--version] <subcommand> [<args>]

Core Subcommands:
  speak       [text ...] [--ordinal N] [--source-hash H] [--session-id ID] [--priority P] [--voice V] [--speed S]
  replay      [--ordinal N]
  pause
  resume
  stop
  restart
  seek        TARGET
  autoplay    {on, off, status, mode, scope}
  narrate     {on, off, status, stop, config}
  hook        {stop, narrator, bootstrap}
  doctor      [--json]
  update      [--force]
```

### 4.3 Detailed Subcommand Routing & Contracts

#### 1. `speak`
- **Arguments**:
  - `text` (positional, `nargs="*"`): Words to synthesize.
  - `--source-hash HASH`: 64-hex SHA-256 hash for cache lookup/promotion.
  - `--session-id ID`: Session ID for tracking speech events.
  - `--priority P`: Integer priority (1=USER_INTERRUPT, 2=EXPLICIT_MCP, 3=AUTOPLAY, 4=TOOL_NARRATION; default 2).
  - `--voice VOICE`: Voice ID (default from `config_constants.DEFAULT_VOICE_ID`).
  - `--speed SPEED`: Speed float (default from `config_constants.DEFAULT_SPEED`).
  - `--socket-path PATH`: Socket override.
  - `--ordinal N`: Ordinal compatibility flag.
- **Input Resolution**:
  - If positional text args are provided: `text = " ".join(args.text)`.
  - If no positional text args:
    - If `sys.stdin` is not a TTY (pipe or redirection): `text = sys.stdin.read()`.
    - If `sys.stdin` is a TTY and `--source-hash` is present: `text = ""` (triggers cache playback).
    - If `sys.stdin` is a TTY and no text/hash: outputs error to stderr, exits 2.
- **Routing**:
  - Checks global mute `~/.claude/auto-speech.disabled`. If muted, prints informational note to stderr and exits 0.
  - If text is empty and `--source-hash` is set: dispatches `play_cache` via `DaemonClient`.
  - If text is non-empty: dispatches `speak` via `DaemonClient`.
  - Fallback: if daemon is unreachable, falls back to direct offline playback via `NativeAudioSink` or `speak.py` logic.
- **Exit Codes**:
  - `0`: Success (queued or played, or cleanly muted).
  - `1`: Connection or playback failure.
  - `2`: Bad input or invalid argument.
  - `130`: Interrupted (SIGINT).

#### 2. `replay`
- **Arguments**:
  - `--ordinal N`: 1-indexed cache recency ordinal (default 1).
- **Routing**:
  - Invokes `replay.main(["--ordinal", str(args.ordinal)])` or native domain logic.
- **Exit Codes**:
  - `0`: Success.
  - `2`: No cache entries found or ordinal out of bounds.
  - `6`: Playback failure.
  - `130`: Interrupted.

#### 3. `pause`
- **Arguments**: None.
- **Routing**:
  - Connects to `/tmp/auto-speech-mpv.sock` and issues `["set_property", "pause", true]`.
  - If socket is absent or connection refused: prints `control: no active playback session` to stderr, exits 2.
  - If success: prints `paused` to stdout, exits 0.
- **Exit Codes**:
  - `0`: Paused.
  - `2`: No active playback session.
  - `3`: IPC failure.

#### 4. `resume`
- **Arguments**: None.
- **Routing**:
  - Connects to `/tmp/auto-speech-mpv.sock` and issues `["set_property", "pause", false]`.
  - If socket absent: prints `control: no active playback session` to stderr, exits 2.
  - If success: prints `resumed` to stdout, exits 0.
- **Exit Codes**:
  - `0`: Resumed.
  - `2`: No active playback session.
  - `3`: IPC failure.

#### 5. `stop`
- **Arguments**: None.
- **Routing**:
  - Calls `DaemonClient.interrupt()`.
  - The daemon immediately terminates active `mpv` process and purges P3/P4 queue items.
  - Also falls back to signaling `/tmp/auto-speech-mpv.pid` if daemon is not running.
  - Prints `stopped` to stdout, exits 0.
- **Exit Codes**:
  - `0`: Stopped.
  - `2`: No active playback session.

#### 6. `restart`
- **Arguments**: None.
- **Routing**:
  - Connects to `/tmp/auto-speech-mpv.sock` and issues `["seek", 0, "absolute"]`.
  - If socket absent: prints `control: no active playback session` to stderr, exits 2.
  - If success: prints `restarted` to stdout, exits 0.
- **Exit Codes**:
  - `0`: Restarted.
  - `2`: No active playback session.
  - `3`: IPC failure.

#### 7. `seek`
- **Arguments**:
  - `target` (positional): Seek target string (`+N`, `-N`, absolute seconds `N`, or `'end'`).
- **Validation**:
  - Target must not be empty. Validates format (`+float`, `-float`, `float`, or `end`).
  - If invalid: prints error to stderr, exits 4 (`EXIT_BAD_ARG`).
- **Routing**:
  - Connects to `/tmp/auto-speech-mpv.sock` and sends mpv seek command.
  - If socket absent: prints `control: no active playback session` to stderr, exits 2.
  - If success: prints `seek ok` to stdout, exits 0.
- **Exit Codes**:
  - `0`: Seek succeeded.
  - `2`: No active playback session.
  - `3`: IPC failure.
  - `4`: Invalid target argument.

#### 8. `autoplay`
Subcommands:
- `autoplay on [--session-id ID]`:
  - Resolves session ID from `--session-id` or `$CLAUDE_CODE_SESSION_ID`.
  - Clears `~/.claude/auto-speech.disabled`.
  - If session ID present: touches `~/.claude/auto-speech-autoplay-enabled/<sid>`; prints `autoplay enabled for session <sid>`.
  - If session ID absent: prints `global mute cleared, but CLAUDE_CODE_SESSION_ID not set — autoplay is opt-in per session`.
  - Exits 0.
- `autoplay off [--session-id ID]`:
  - Resolves session ID.
  - If session ID present: unlinks `~/.claude/auto-speech-autoplay-enabled/<sid>`; prints `autoplay disabled for session <sid>`.
  - If session ID absent: creates `~/.claude/auto-speech.disabled`; prints `no session id — set global mute marker instead`.
  - Exits 0.
- `autoplay status [--session-id ID]`:
  - Reads session ID, checks enrollment, global disable, solo scope, mpv pid, and log tail.
  - Prints formatted status block. Exits 0.
- `autoplay mode [MODE]`:
  - Valid modes: `raw`, `bypass`, `verbatim`, `small`, `medium`, `large`.
  - If mode argument is omitted: prints current active mode, summary size, prompt path, and config path.
  - If mode argument is provided: writes updated `~/.config/auto-speech/autoplay.toml`, prints confirmation.
  - If invalid mode: prints error to stderr, exits 2.
- `autoplay scope [SCOPE] [--session-id ID]`:
  - Valid scopes: `all`, `solo` (aliases: `this`, `one`, `only`).
  - If scope argument is omitted: outputs current scope string (`format_status(SoloScope(), sid)`).
  - If `all`: unlinks `~/.claude/auto-speech-autoplay-solo`; prints `autoplay scope: ALL — every enrolled session reads`.
  - If `solo`: requires session ID; writes session ID to `~/.claude/auto-speech-autoplay-solo`, enrolls session; prints `autoplay scope: SOLO — only this session reads`.
  - If invalid scope: prints error to stderr, exits 2.

#### 9. `narrate`
Subcommands:
- `narrate on [--session-id ID]`:
  - Resolves session ID. If absent, prints error to stderr, exits 1.
  - Touches `~/.claude/auto-speech-narrate-sessions/<sid>`.
  - Cleans legacy `.claude/narrate.enabled` if present.
  - Spawns daemon if not running (`DaemonClient.ensure_daemon_running()`).
  - Prints `narrate on — session <sid>, daemon running`. Exits 0.
- `narrate off [--session-id ID]`:
  - Resolves session ID. If absent, exits 0.
  - Unlinks `~/.claude/auto-speech-narrate-sessions/<sid>`.
  - Prints `narrate off — session <sid>`. Exits 0.
- `narrate status [--session-id ID]`:
  - Resolves session ID. Reports marker status, daemon PID and liveness, queue depth, and log tail. Exits 0.
- `narrate stop`:
  - Signals daemon PID (`narrator_service_stop.sh` logic). Exits 0.
- `narrate config [ACTION]`:
  - Actions: `show` (default), `path`, `edit`.
  - `path`: prints resolved TOML config path.
  - `show`: outputs config contents.
  - `edit`: opens in `$EDITOR` / `$VISUAL` / `open -t`.

#### 10. `hook`
Subcommands:
- `hook stop`:
  - Consumes JSON hook payload from `sys.stdin`.
  - Evaluates suppression, global mute, solo scope, enrollment.
  - Touches session beacon, launches `autoplay_worker.py` detached.
  - Exits 0.
- `hook narrator`:
  - Consumes JSON hook payload from `sys.stdin`.
  - Checks session enrollment.
  - Appends JSONL event to `/tmp/auto-speech-narrator-events.jsonl`.
  - Ensures daemon is running.
  - Exits 0.
- `hook bootstrap [--force]`:
  - Invokes `setup/bootstrap.sh` (`--force` if specified).
  - Reconciles venv if lock changed. Exits 0.

#### 11. `doctor`
- **Arguments**:
  - `--json`: Outputs structured JSON report.
- **Routing**:
  - Instantiates `Doctor()`, executes all probes, returns formatted output.
- **Exit Codes**:
  - `0`: All checks OK or WARN.
  - `1`: One or more checks FAIL.

#### 12. `update`
- **Arguments**:
  - `--force`: Force sync regardless of lock stamp.
- **Routing**:
  - Executes `setup/bootstrap.sh --force`.
  - Reconciles venv with `uv.lock`.
- **Exit Codes**:
  - `0`: Update / sync successful.
  - `1`: Sync failed.

---

## 5. Input Handling, Diagnostics, and Exit Code Matrix

### 5.1 STDIN vs CLI Arguments Matrix

| Command | CLI Argument Given | Stdin Piped / Redirected | Stdin from TTY (Interactive) |
|---|---|---|---|
| `auto-speech speak` | Uses CLI argument text directly | Reads from stdin | If `--source-hash` present: play cache. Else: error to stderr (exit 2). |
| `auto-speech replay` | Uses `--ordinal` flag | Ignores stdin | Ignores stdin |
| `auto-speech hook stop` | Optional override payload | Reads hook JSON from stdin | If stdin empty, uses minimal mock payload `{}` |
| `auto-speech hook narrator` | Optional override payload | Reads hook JSON from stdin | If stdin empty, uses minimal mock payload `{}` |

### 5.2 Standardized Exit Code Conventions

| Code | Constant / Meaning | Standard Diagnostic Behavior |
|---|---|---|
| `0` | `EXIT_OK` | Success; clean output on stdout (or silent when expected). |
| `1` | `EXIT_FAILURE` | General error; connection failed, daemon crashed, or doctor check FAIL. Diagnostic on stderr. |
| `2` | `EXIT_NO_SESSION` / `EXIT_INVALID_INPUT` | Target resource absent (no active playback session, no cache entry, unknown subcommand). Message on stderr. |
| `3` | `EXIT_IPC_FAIL` | Communication with `mpv` IPC socket failed. Error on stderr. |
| `4` | `EXIT_BAD_ARG` | Malformed subcommand argument (e.g., invalid seek target). Error on stderr. |
| `6` | `EXIT_PLAYBACK_FAIL` | Audio device or mpv binary playback error. Error on stderr. |
| `130` | `EXIT_INTERRUPTED` | User SIGINT / Ctrl+C. |

---

## 6. Symlink, PATH, and Installer Integration

### 6.1 Path Conventions
- **Executable path**: `/Users/joshua/Developer/auto-speech/bin/auto-speech`.
- **Global symlink**: `~/.local/bin/auto-speech -> /Users/joshua/Developer/auto-speech/bin/auto-speech`.
- Joshua's environment already has `~/.local/bin` on PATH (verified containing tools like `itg`, `svc`, `sysobs`, `systemone`).
- Running `auto-speech` anywhere in terminal or scripts will resolve and execute directly.

### 6.2 Installer and Uninstaller Updates
1. **`setup/install-plugin.sh`**:
   - Ensure `~/.local/bin` exists: `mkdir -p "$HOME/.local/bin"`.
   - Create symlink: `ln -sf "$PROJECT_ROOT/bin/auto-speech" "$HOME/.local/bin/auto-speech"`.
   - Update slash command `.md` files to delegate to `auto-speech <subcommand>` using dynamic `$PROJECT_ROOT` resolution.
2. **`setup/uninstall.sh`**:
   - Remove symlink: `rm -f "$HOME/.local/bin/auto-speech"`.
3. **Preservation of Portability Invariant**:
   - Zero hardcoded absolute paths in tracked `.md` or `.sh` files (verified by `tests/test_install_plugin.sh`).

---

## 7. Migration, Hook Parity & Integration Coherence

### 7.1 Integration with `agent-event-router`
In `/Users/joshua/.local/bin/agent-event-router`:
```bash
Stop)
    if [ "$ACTION" == "autoplay" ]; then
        run_hook "auto-speech hook stop"
    ...
```
Or `autoplay_hook.sh` can internally invoke `auto-speech hook stop`, ensuring that regardless of whether an event enters via `agent-event-router`, direct Claude Code hook, or CLI test, it traverses the exact same code path.

### 7.2 Slash Command Upgrades
Existing 21 slash commands (`plugin/commands/` and `plugin/commands-extra/`) will be updated to call `auto-speech <subcommand>`. This collapses dozens of duplicated bash script snippets into single-line dispatcher calls, improving maintainability while retaining 100% user-facing behavioral parity.

---

## 8. Verification Strategy & Test Matrix

A new dedicated test suite `tests/test_cli_dispatcher.py` will be created to verify:
1. **Help & Metadata**:
   - `auto-speech --help` exits 0 and displays all subcommands.
   - `auto-speech <subcommand> --help` exits 0 for all 12 subcommands.
2. **Speech & Replay**:
   - Positional text argument synthesis.
   - Piped stdin synthesis.
   - Cache lookup via `--source-hash`.
   - Replay ordinal validation (boundary check, exit 2 on empty cache).
3. **Transport Controls**:
   - `pause`, `resume`, `restart`, `seek` return exit 2 when no active session.
   - `seek` with invalid target returns exit 4.
   - `stop` halts playback cleanly.
4. **Autoplay Controls**:
   - `on` and `off` properly toggle markers and handle missing session IDs.
   - `mode` displays and modifies TOML config; rejects invalid modes.
   - `scope` toggles `ALL` and `SOLO`.
5. **Narration Controls**:
   - `on`, `off`, `status`, `stop`, `config` operational checks.
6. **Hooks & Diagnostics**:
   - `hook stop`, `hook narrator`, `hook bootstrap` handle stdin JSON payloads.
   - `doctor` returns valid exit code and `--json` structure.
7. **Symlink & PATH Integrity**:
   - `~/.local/bin/auto-speech` resolves to executable and dispatches properly.
8. **Full Regression Gate**:
   - `bash tests/run_all.sh` passes 100% (all 49+ suites green).
