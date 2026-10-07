# Concrete Implementation Strategy: Unified Core CLI Dispatcher (`cli.py`) for Milestone M1

**Author:** `explorer_m1_1` (Teamwork Explorer)  
**Timestamp:** 2026-10-06T21:12:00Z  
**Target:** Milestone M1 — Core Python CLI Dispatcher (`plugin/scripts/python/cli.py`) & Bash Trampoline (`bin/auto-speech`)  
**Workspace:** Isolated Git Worktree `/Users/joshua/Developer/auto-speech-worktree` (`worktree/cli-mcp-parity`)  

---

## 1. Executive Summary & Objective

The objective of Milestone M1 is to establish the unified command-line entrypoint `auto-speech` for the entire `auto-speech` subsystem. Currently, user and hook operations are fragmented across standalone scripts (`speak.py`, `replay.py`, `control.py`, `doctor.py`, `self_update.py`, shell scripts, and 21 slash commands).

Milestone M1 unifies these operations under a two-tier dispatch architecture:
1. **Executable Trampoline (`bin/auto-speech`)**: A robust bash trampoline that resolves symlinks, locates the virtual environment python interpreter (`.venv/bin/python`), and delegates to the core Python dispatcher.
2. **Core Python Dispatcher (`plugin/scripts/python/cli.py`)**: A pure-Python CLI router using `argparse` implementing top-level options (`--help`, `--version`) and **12 authoritative subcommands** (`speak`, `replay`, `pause`, `resume`, `stop`, `restart`, `seek`, `autoplay`, `narrate`, `hook`, `doctor`, `update`).

This document provides the complete, concrete implementation blueprint for `cli.py`, detailing the argument parser hierarchy, input resolution, collaborator integration, exact exit codes, error diagnostics, and testing strategy.

---

## 2. Argument Parsing Architecture (`argparse`)

### 2.1 Top-Level Parser Configuration

```python
parser = argparse.ArgumentParser(
    prog="auto-speech",
    description="Unified CLI dispatcher for auto-speech: speech synthesis, playback transport, session management, and diagnostics.",
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
parser.add_argument(
    "--version",
    action="version",
    version="auto-speech 0.2.0",
)
subparsers = parser.add_subparsers(
    dest="subcommand",
    metavar="<subcommand>",
    help="Subcommand to execute. Run 'auto-speech <subcommand> --help' for details.",
)
```

**Top-Level Invocation Semantics**:
- `auto-speech --help` or `auto-speech -h`: Exits `0`, prints formatted help listing all 12 subcommands.
- `auto-speech --version`: Exits `0`, prints version string.
- `auto-speech` (no arguments): Prints help to `sys.stderr` and exits `2` (`EXIT_INVALID_INPUT`).
- `auto-speech <unknown_subcommand>`: `argparse` prints error to `sys.stderr` and exits `2`.

---

### 2.2 The 12 Subcommands Specification

```
auto-speech
├── speak       [words ...] [--source-hash H] [--session-id ID] [--priority P] [--voice V] [--speed S] [--socket-path PATH] [--ordinal N]
├── replay      [--ordinal N] [--keep-artifacts]
├── pause
├── resume
├── restart
├── stop
├── seek        TARGET
├── autoplay    {on, off, status, mode, scope}
│   ├── on      [--session-id ID]
│   ├── off     [--session-id ID]
│   ├── status  [--session-id ID]
│   ├── mode    [MODE]
│   └── scope   [SCOPE] [--session-id ID]
├── narrate     {on, off, status, stop, config}
│   ├── on      [--session-id ID]
│   ├── off     [--session-id ID]
│   ├── status  [--session-id ID]
│   ├── stop
│   └── config  [ACTION]
├── hook        {stop, narrator, bootstrap}
│   ├── stop
│   ├── narrator
│   └── bootstrap [--force]
├── doctor      [--json]
└── update      [--force]
```

---

### 2.3 Subcommand Specifications & Handlers

#### 1. `speak`
*Synthesize words or piped text into audio speech, or play pre-cached audio by hash.*

- **Parser Configuration**:
  ```python
  p_speak = subparsers.add_parser("speak", help="Synthesize and play speech.")
  p_speak.add_argument("words", nargs="*", default=[], help="Words to speak.")
  p_speak.add_argument("--source-hash", type=str, default=None, help="64-hex SHA-256 hash for cache lookup/promotion.")
  p_speak.add_argument("--session-id", type=str, default=None, help="Session ID for tracking speech events.")
  p_speak.add_argument("--priority", type=int, default=2, help="Priority (1=USER_INTERRUPT, 2=EXPLICIT_MCP, 3=AUTOPLAY, 4=TOOL_NARRATION; default 2).")
  p_speak.add_argument("--voice", type=str, default="system", help="Voice identifier (default: system).")
  p_speak.add_argument("--speed", type=float, default=1.0, help="Speech speed multiplier (default: 1.0).")
  p_speak.add_argument("--socket-path", type=str, default=None, help="Override path to auto-speech daemon UNIX socket.")
  p_speak.add_argument("--ordinal", type=int, default=1, help="1-indexed ordinal position (compatibility flag).")
  p_speak.add_argument("--keep-artifacts", action="store_true", help="Preserve temporary audio files (compatibility flag).")
  ```

- **Execution Flow**:
  1. **Global Mute Check**: Instantiate `AutoplayGate()`. If `gate.worker_gated_off()` is `True` (`~/.claude/auto-speech.disabled` exists):
     - Print notice to `sys.stderr`: `auto-speech: globally muted (~/.claude/auto-speech.disabled present); speech suppressed.`
     - Return `0` (`EXIT_OK`).
  2. **Hash Validation**: If `--source-hash` is provided:
     - Normalize: `sh = args.source_hash.strip().lower()`.
     - If `len(sh) != 64` or contains non-hex characters:
       - Print error to `sys.stderr`: `auto-speech speak: --source-hash must be 64 hex chars, got {args.source_hash!r}`.
       - Return `2` (`EXIT_INVALID_INPUT`).
  3. **Input Resolution (Positional vs. Stdin)**:
     - If `args.words` is non-empty: `text = " ".join(args.words)`.
     - Else:
       - If `not getattr(sys.stdin, "isatty", lambda: False)()` (piped or redirected stdin):
         `text = sys.stdin.read()`.
       - Else (interactive TTY):
         - If `args.source_hash`: `text = ""` (cached playback mode).
         - Else:
           - Print error to `sys.stderr`: `auto-speech speak: no text provided on command line or stdin`.
           - Return `2` (`EXIT_INVALID_INPUT`).
  4. **Cached Audio Playback Branch** (`not text.strip()` and `args.source_hash`):
     - Attempt `DaemonClient(socket_path=args.socket_path).play_cache(sh, priority=Priority(args.priority), session_id=args.session_id)`.
     - If daemon returns `status in ("ok", "queued")`: return `0`.
     - If daemon offline or call fails: check local cache store `config/cache/<hash_prefix>/full.wav`.
       - If file exists: play via `NativeAudioSink().play(wav_path)`. Return `0`.
       - If playback fails: print error to `sys.stderr`, return `1`.
  5. **Speech Synthesis Branch** (`text.strip()` non-empty):
     - Call `client = DaemonClient(socket_path=args.socket_path)`.
     - Call `client.ensure_daemon_running()`.
     - Call `resp = client.speak(text, priority=Priority(args.priority), source_hash=args.source_hash, session_id=args.session_id, voice_id=args.voice, speed=args.speed)`.
     - If `resp.status in ("ok", "queued")`: return `0`.
     - If `ConnectionError` or failure:
       - Fallback to legacy `send_speech_request` or direct synthesis.
       - If unrecoverable: print `auto-speech speak: speech request failed: {exc}` to `sys.stderr`, return `1`.
     - Catch `KeyboardInterrupt`: return `130`.

---

#### 2. `replay`
*Replay cached audio from recent speech synthesis runs by recency ordinal.*

- **Parser Configuration**:
  ```python
  p_replay = subparsers.add_parser("replay", help="Replay recent cached speech.")
  p_replay.add_argument("--ordinal", type=int, default=1, help="1-indexed recency ordinal (1 = newest, default: 1).")
  p_replay.add_argument("--keep-artifacts", action="store_true", help="Preserve artifacts (compatibility flag).")
  ```

- **Execution Flow**:
  1. **Ordinal Range Check**: If `args.ordinal < 1`:
     - Print error to `sys.stderr`: `auto-speech replay: --ordinal must be >= 1, got {args.ordinal}`.
     - Return `2` (`EXIT_INVALID_INPUT`).
  2. **Query CacheStore**: Initialize `store = CacheStore(cache_root)`. Fetch `entries = store.list_by_recency()`.
     - If `not entries`:
       - Print error to `sys.stderr`: `auto-speech replay: no cache entries found. Run speak at least once first.`.
       - Return `2` (`EXIT_NO_CACHE_ENTRY`).
     - If `args.ordinal > len(entries)`:
       - Print error to `sys.stderr`: `auto-speech replay: only {len(entries)} cache entries; you asked for #{args.ordinal}`.
       - Return `2` (`EXIT_NO_CACHE_ENTRY`).
  3. **Locate Target**: `wav_path, entry = entries[args.ordinal - 1]`.
     - Print metadata to `sys.stderr`: `[replay] ordinal={args.ordinal} voice={entry.voice_id} dur={entry.duration_seconds:.1f}s created={entry.created_at} path={wav_path}`.
  4. **Global Mute Check**: If `AutoplayGate().worker_gated_off()`:
     - Print notice to `sys.stderr`: `auto-speech: globally muted (~/.claude/auto-speech.disabled present); replay suppressed.`.
     - Return `0` (`EXIT_OK`).
  5. **Playback Execution**:
     - If daemon socket `/tmp/auto-speech-daemon.sock` is active:
       - Route via `DaemonClient.play_cache(entry.source_hash)`.
       - If `status in ("ok", "queued")`: return `0`.
     - Fallback to synchronous playback via `NativeAudioSink().play(wav_path)`:
       - Catch `AudioSinkError` / `PlaybackError`: print error to `sys.stderr`, return `6` (`EXIT_PLAYBACK_FAIL`).
       - Catch `KeyboardInterrupt`: call `sink.interrupt()`, return `130` (`EXIT_INTERRUPTED`).
     - Return `0` (`EXIT_OK`).

---

#### 3. `pause`, 4. `resume`, 5. `restart`
*Transport controls communicating with mpv over `/tmp/auto-speech-mpv.sock`.*

- **Parser Configuration**:
  ```python
  subparsers.add_parser("pause", help="Pause active mpv audio playback.")
  subparsers.add_parser("resume", help="Resume paused mpv audio playback.")
  subparsers.add_parser("restart", help="Restart active mpv audio playback from start.")
  ```

- **Shared IPC Helper**:
  ```python
  MPV_SOCK_PATH = Path("/tmp/auto-speech-mpv.sock")

  def _send_mpv_command(cmd: list) -> tuple[int, str]:
      if not MPV_SOCK_PATH.is_socket():
          return 2, "control: no active playback session (unified daemon uses synchronous audio)."
      try:
          reply = MpvIpc.send(cmd, MPV_SOCK_PATH)
      except MpvIpcError as exc:
          return 3, f"control: IPC failure: {exc}"
      if reply.get("error") != "success":
          return 3, f"control: mpv command returned error: {reply.get('error')}"
      return 0, ""
  ```

- **Execution Flows**:
  - `pause`: Call `_send_mpv_command(["set_property", "pause", True])`.
    - If `code != 0`: print error to `sys.stderr`, return `code`.
    - Else: print `paused` to `sys.stdout`, return `0`.
  - `resume`: Call `_send_mpv_command(["set_property", "pause", False])`.
    - If `code != 0`: print error to `sys.stderr`, return `code`.
    - Else: print `resumed` to `sys.stdout`, return `0`.
  - `restart`: Call `_send_mpv_command(["seek", 0, "absolute"])`.
    - If `code != 0`: print error to `sys.stderr`, return `code`.
    - Else: print `restarted` to `sys.stdout`, return `0`.

---

#### 6. `stop`
*Immediately stop active playback and purge pending audio items.*

- **Parser Configuration**:
  ```python
  subparsers.add_parser("stop", help="Stop playback immediately and purge pending queue.")
  ```

- **Execution Flow**:
  1. Call `DaemonClient().interrupt()`. This tells the daemon's `NativeAudioSink` to terminate active `mpv` and purge in-memory P3/P4 queue items.
  2. Also inspect `/tmp/auto-speech-mpv.pid`. If file exists and contains a live PID:
     - Signal `SIGTERM` to the process.
     - Wait up to 0.3s; escalate to `SIGKILL` if still alive.
     - Remove PID file.
  3. Print `stopped` to `sys.stdout`.
  4. Return `0` (`EXIT_OK`).

---

#### 7. `seek`
*Seek to a relative or absolute timestamp within the active audio playback.*

- **Parser Configuration**:
  ```python
  p_seek = subparsers.add_parser("seek", help="Seek active playback (+N, -N, N, or 'end').")
  p_seek.add_argument("target", type=str, help="Target offset: '+N', '-N', absolute seconds 'N', or 'end'.")
  ```

- **Target Parsing & Validation**:
  - Target must be non-empty.
  - Parse `target.strip()`:
    - `"end"`: `cmd = ["seek", 999999, "absolute"]`
    - Starts with `+`:
      ```python
      try:
          val = float(target[1:])
          cmd = ["seek", val, "relative"]
      except ValueError:
          print(f"control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
          return 4
      ```
    - Starts with `-`:
      ```python
      try:
          val = float(target)
          cmd = ["seek", val, "relative"]
      except ValueError:
          print(f"control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
          return 4
      ```
    - Pure number:
      ```python
      try:
          val = float(target)
          cmd = ["seek", val, "absolute"]
      except ValueError:
          print(f"control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
          return 4
      ```
  - If validation fails: return `4` (`EXIT_BAD_ARG`).
  - If `/tmp/auto-speech-mpv.sock` is absent:
    - Print `control: no active playback session (unified daemon uses synchronous audio).` to `sys.stderr`.
    - Return `2` (`EXIT_NO_SESSION`).
  - Send command via `MpvIpc.send(cmd, MPV_SOCK_PATH)`:
    - On IPC error: print error to `sys.stderr`, return `3` (`EXIT_IPC_FAIL`).
    - On success: print `seek ok` to `sys.stdout`, return `0` (`EXIT_OK`).

---

#### 8. `autoplay`
*Manage end-of-turn autoplay enrollment markers, solo scopes, and rewrite modes.*

- **Parser Configuration**:
  ```python
  p_autoplay = subparsers.add_parser("autoplay", help="Autoplay subsystem controls.")
  sub_autoplay = p_autoplay.add_subparsers(dest="autoplay_action", metavar="<action>")

  # on
  p_ap_on = sub_autoplay.add_parser("on", help="Enable autoplay for this session.")
  p_ap_on.add_argument("--session-id", type=str, default=None, help="Session ID to enroll.")

  # off
  p_ap_off = sub_autoplay.add_parser("off", help="Disable autoplay for this session (or globally).")
  p_ap_off.add_argument("--session-id", type=str, default=None, help="Session ID to withdraw.")

  # status
  p_ap_status = sub_autoplay.add_parser("status", help="Show autoplay enrollment, scope, and log status.")
  p_ap_status.add_argument("--session-id", type=str, default=None, help="Session ID to inspect.")

  # mode
  p_ap_mode = sub_autoplay.add_parser("mode", help="Show or set autoplay rewrite mode.")
  p_ap_mode.add_argument("mode", nargs="?", default=None, help="Mode: raw | verbatim | small | medium | large.")

  # scope
  p_ap_scope = sub_autoplay.add_parser("scope", help="Show or set autoplay solo scope.")
  p_ap_scope.add_argument("scope", nargs="?", default=None, help="Scope: all | solo.")
  p_ap_scope.add_argument("--session-id", type=str, default=None, help="Session ID for solo scope.")
  ```

- **Action Handlers**:
  - `autoplay on`:
    - Resolve session ID: `args.session_id or os.environ.get("CLAUDE_CODE_SESSION_ID") or None`.
    - Clear global mute `~/.claude/auto-speech.disabled` if present.
    - If session ID present:
      - Call `SessionEnrollment().enroll(session_id)`.
      - Print `autoplay enabled for session {session_id}`.
    - Else:
      - Print `global mute cleared, but CLAUDE_CODE_SESSION_ID not set — autoplay is opt-in per session`.
    - Return `0`.
  - `autoplay off`:
    - Resolve session ID.
    - If session ID present:
      - Call `SessionEnrollment().withdraw(session_id)`.
      - Print `autoplay disabled for session {session_id}`.
    - Else:
      - Touch `~/.claude/auto-speech.disabled`.
      - Print `no session id — set global mute marker instead (~/.claude/auto-speech.disabled)`.
    - Return `0`.
  - `autoplay status`:
    - Resolve session ID.
    - Print multi-line diagnostic report: session ID, enrollment status, enroll dir count, global disable state, solo scope state, mpv pid liveness, and log tail.
    - Return `0`.
  - `autoplay mode [MODE]`:
    - If `mode` argument is omitted:
      - Load config via `autoplay_config.load_config()`.
      - Print JSON or structured summary: mode, summary_size, bypass_llm, prompt_path, config_path.
      - Return `0`.
    - If `mode` argument provided:
      - Normalize: `m = args.mode.strip().lower()`.
      - If `m in ("raw", "bypass")`: `mode_val = "raw"`; `size_val = "small"`
      - Elif `m == "verbatim"`: `mode_val = "verbatim"`; `size_val = "small"`
      - Elif `m == "small"`: `mode_val = "summary"`; `size_val = "small"`
      - Elif `m == "medium"`: `mode_val = "summary"`; `size_val = "medium"`
      - Elif `m == "large"`: `mode_val = "summary"`; `size_val = "large"`
      - Else:
        - Print `autoplay mode: unknown value {args.mode} (expected raw | verbatim | small | medium | large)` to `sys.stderr`.
        - Return `4` (`EXIT_BAD_ARG`).
      - Write to `~/.config/auto-speech/autoplay.toml` under `[autoplay]`.
      - Print `wrote ~/.config/auto-speech/autoplay.toml: mode={mode_val} summary_size={size_val}`.
      - Return `0`.
  - `autoplay scope [SCOPE]`:
    - Scope model: `scope = SoloScope()`.
    - Resolve session ID.
    - If `scope` argument omitted:
      - Print `format_status(scope, session_id)` to `sys.stdout`.
      - Return `0`.
    - `s = args.scope.strip().lower()`.
    - If `s == "all"`:
      - `scope.clear()`.
      - Print `autoplay scope: ALL — every enrolled session reads (default)`.
      - Return `0`.
    - Elif `s in ("solo", "this", "one", "only")`:
      - If not session ID:
        - Print `autoplay scope solo: requires a session id (pass --session-id or set CLAUDE_CODE_SESSION_ID)` to `sys.stderr`.
        - Return `2` (`EXIT_INVALID_INPUT`).
      - `scope.set_solo(session_id)`.
      - `SessionEnrollment().enroll(session_id)`.
      - Print `autoplay scope: SOLO — only THIS session reads (spotlight={session_id})`.
      - Return `0`.
    - Else:
      - Print `autoplay scope: unknown scope {args.scope} (expected all | solo)` to `sys.stderr`.
      - Return `4` (`EXIT_BAD_ARG`).

---

#### 9. `narrate`
*Control real-time tool narration session markers, daemon process, and configuration.*

- **Parser Configuration**:
  ```python
  p_narrate = subparsers.add_parser("narrate", help="Narration subsystem controls.")
  sub_narrate = p_narrate.add_subparsers(dest="narrate_action", metavar="<action>")

  # on
  p_nr_on = sub_narrate.add_parser("on", help="Enable narration for this session.")
  p_nr_on.add_argument("--session-id", type=str, default=None, help="Session ID to enroll.")

  # off
  p_nr_off = sub_narrate.add_parser("off", help="Disable narration for this session.")
  p_nr_off.add_argument("--session-id", type=str, default=None, help="Session ID to withdraw.")

  # status
  p_nr_status = sub_narrate.add_parser("status", help="Show narration daemon and marker status.")
  p_nr_status.add_argument("--session-id", type=str, default=None, help="Session ID to inspect.")

  # stop
  sub_narrate.add_parser("stop", help="Stop the narrator daemon process.")

  # config
  p_nr_cfg = sub_narrate.add_parser("config", help="Show, edit, or print narrator config path.")
  p_nr_cfg.add_argument("action", nargs="?", default="show", choices=["show", "path", "edit"], help="Config action (default: show).")
  ```

- **Action Handlers**:
  - `narrate on`:
    - Resolve session ID. If absent:
      - Print `narrate on: requires session ID (pass --session-id or set CLAUDE_CODE_SESSION_ID)` to `sys.stderr`.
      - Return `2` (`EXIT_INVALID_INPUT`).
    - Touch marker: `~/.claude/auto-speech-narrate-sessions/<session_id>`.
    - Clean legacy marker `<cwd>/.claude/narrate.enabled` if present.
    - Call `DaemonClient().ensure_daemon_running()`.
    - Print `narrate on — session {session_id}, daemon running`.
    - Return `0`.
  - `narrate off`:
    - Resolve session ID.
    - If present, unlink `~/.claude/auto-speech-narrate-sessions/<session_id>`.
    - Unlink legacy `<cwd>/.claude/narrate.enabled` if present.
    - Print `narrate off — session {session_id or '<none>'}`.
    - Return `0`.
  - `narrate status`:
    - Resolve session ID.
    - Output formatted status: session ID, marker state, legacy marker presence, other enrolled sessions count, daemon liveness (pid, running/stale/absent), queue depth, and log tail.
    - Return `0`.
  - `narrate stop`:
    - Read `/tmp/auto-speech-narrator-daemon.pid`.
    - If absent: print `narrator daemon: not running`, return `0`.
    - If PID exists and `_pid_is_our_daemon(pid)`:
      - Send `SIGTERM`, wait up to 3s.
      - If still alive, send `SIGKILL`.
      - Unlink PID file.
      - Print `narrator daemon: stopped (pid={pid})`.
    - Else: unlink PID file, print `narrator daemon: stale pid file cleaned up`.
    - Return `0`.
  - `narrate config [ACTION]`:
    - `action = args.action.strip().lower()`.
    - Load config via `narrator_config.load_config()`. `cfg_path = conf.get("config_path")`.
    - If `action == "path"`:
      - If `cfg_path`: print `cfg_path`.
      - Else: print `no narrator config file loaded (using shipped defaults)`.
      - Return `0`.
    - Elif `action == "show"`:
      - If `cfg_path` and `Path(cfg_path).is_file()`: print file contents.
      - Else: print JSON dump of `conf`.
      - Return `0`.
    - Elif `action == "edit"`:
      - If `not cfg_path`: create `~/.config/auto-speech/narrator.toml` from example.
      - Editor: `os.environ.get("EDITOR") or os.environ.get("VISUAL") or "open -t"`.
      - Run editor subprocess. Print `opened {cfg_path} in {editor}`.
      - Return `0`.
    - Else:
      - Print `narrate config: unknown action {args.action} (expected show | path | edit)` to `sys.stderr`.
      - Return `4` (`EXIT_BAD_ARG`).

---

#### 10. `hook`
*Simulate and execute Claude Code and Antigravity lifecycle hooks.*

- **Parser Configuration**:
  ```python
  p_hook = subparsers.add_parser("hook", help="Lifecycle hook execution.")
  sub_hook = p_hook.add_subparsers(dest="hook_action", metavar="<action>")

  # stop
  sub_hook.add_parser("stop", help="Handle Stop hook (end-of-turn autoplay).")

  # narrator
  sub_hook.add_parser("narrator", help="Handle narrator tool activity events.")

  # bootstrap
  p_hk_boot = sub_hook.add_parser("bootstrap", help="Handle SessionStart bootstrap hook.")
  p_hk_boot.add_argument("--force", action="store_true", help="Force dependency synchronization.")
  ```

- **Action Handlers**:
  - `hook stop`:
    - Ingest JSON payload from `sys.stdin`.
    - Parse JSON: extract `session_id` (`.session_id // .conversationId`), `transcript_path` (`.transcript_path // .transcriptPath`).
    - Gate 1: `AUTO_SPEECH_SUPPRESS_HOOKS == 1` -> exit `0`.
    - Gate 2: `AutoplayGate().worker_gated_off()` -> exit `0`.
    - Gate 3: `SoloScope().session_allowed(session_id)` is False -> exit `0`.
    - Gate 4: `SessionEnrollment().is_enrolled(session_id)` is False -> exit `0`.
    - Ensure daemon is running: `DaemonClient().ensure_daemon_running()`.
    - Touch beacon `/tmp/auto-speech-last-stop.{session_id}`, capture `beacon_mtime`.
    - Launch detached worker `autoplay_worker.sh` (or in-process runner) via double-fork / daemon thread.
    - Return `0`.
  - `hook narrator`:
    - Ingest JSON payload from `sys.stdin`.
    - Parse JSON: extract `session_id`, `hook_event_name`.
    - Gate 1: `AUTO_SPEECH_SUPPRESS_HOOKS == 1` -> exit `0`.
    - Gate 2: Check marker `~/.claude/auto-speech-narrate-sessions/<session_id>`. If absent, exit `0`.
    - Ensure daemon running: `DaemonClient().ensure_daemon_running()`.
    - Build JSONL record: `{"ts": int(time.time()), "cwd": os.getcwd(), "pid": os.getpid(), "event": payload.get("hook_event_name", "unknown"), "payload": payload}`.
    - Append to `/tmp/auto-speech-narrator-events.jsonl`.
    - Return `0`.
  - `hook bootstrap`:
    - Check if `uv.lock` changed via `needs_sync(uv_lock, stamp_path)`.
    - If `args.force` or sync needed:
      - Call `setup/bootstrap.sh` (`--force` if specified).
    - Return `0`.

---

#### 11. `doctor`
*Execute 10-probe system diagnostic health inspection.*

- **Parser Configuration**:
  ```python
  p_doctor = subparsers.add_parser("doctor", help="System diagnostic health check.")
  p_doctor.add_argument("--json", action="store_true", help="Output health report in structured JSON format.")
  ```

- **Execution Flow**:
  1. Retrieve `max_queue_depth` from `narrator_config.load_config()` (default 32).
  2. Instantiate `doc = Doctor(max_queue_depth=max_q)`.
  3. Execute `report = doc.run()`.
  4. If `args.json`: print `report.to_json()` to `sys.stdout`.
  5. Else: print `report.to_text()` to `sys.stdout`.
  6. Return `report.exit_code`:
     - `0` if all checks are `OK` or `WARN`.
     - `1` if any check is `FAIL`.

---

#### 12. `update`
*Synchronize virtual environment dependencies against committed `uv.lock`.*

- **Parser Configuration**:
  ```python
  p_update = subparsers.add_parser("update", help="Update and synchronize virtual environment dependencies.")
  p_update.add_argument("--force", action="store_true", help="Force sync regardless of lockfile stamp.")
  ```

- **Execution Flow**:
  1. Locate `bootstrap.sh` at `<project_root>/setup/bootstrap.sh`.
  2. Construct command: `["bash", str(bootstrap_script)]`.
  3. If `args.force`: append `"--force"`.
  4. Run subprocess synchronously:
     `res = subprocess.run(cmd, check=False)`.
  5. If `res.returncode == 0`:
     - Print `auto-speech: dependencies updated and in sync.` to `sys.stdout`.
     - Return `0` (`EXIT_OK`).
  6. Else:
     - Print `auto-speech: update failed (exit code {res.returncode}). See /tmp/auto-speech-sync.log.` to `sys.stderr`.
     - Return `1` (`EXIT_FAILURE`).

---

## 3. Standardized Exit Code Matrix

The CLI dispatcher implements the strict exit code contract defined in `PROJECT.md` and inherited from `control.py`, `replay.py`, `speak.py`, and `doctor.py`:

| Exit Code | Constant | Meaning | Typical Trigger | Output Channel |
| :---: | :--- | :--- | :--- | :--- |
| **`0`** | `EXIT_OK` | Success / cleanly muted | Normal completion; global mute silencing speech; empty text. | `sys.stdout` / silent |
| **`1`** | `EXIT_FAILURE` | General failure | Daemon communication failure; `doctor` check `FAIL`; `update` failure. | `sys.stderr` |
| **`2`** | `EXIT_NO_SESSION` / `EXIT_INVALID_INPUT` | Target resource absent / invalid input | No active playback session for `pause`/`resume`/`restart`/`seek`; `--source-hash` invalid length/hex; replay cache empty or ordinal out of range; required session ID missing; missing subcommand. | `sys.stderr` |
| **`3`** | `EXIT_IPC_FAIL` | mpv IPC failure | Socket exists but `MpvIpc.send()` times out, raises error, or mpv returns command error. | `sys.stderr` |
| **`4`** | `EXIT_BAD_ARG` | Bad argument syntax | `seek` target not valid `+N`, `-N`, `N`, or `'end'`; `autoplay mode` invalid mode name; `autoplay scope` invalid scope name. | `sys.stderr` |
| **`6`** | `EXIT_PLAYBACK_FAIL` | Audio playback error | `NativeAudioSink` raises `AudioSinkError` or `PlaybackError` during replay. | `sys.stderr` |
| **`130`** | `EXIT_INTERRUPTED` | Interrupted (SIGINT) | User pressed Ctrl+C (`KeyboardInterrupt`). | `sys.stderr` |

---

## 4. Error Handling & Diagnostic Principles

1. **Clear Diagnostic Prefix**:
   All diagnostic errors emitted to `sys.stderr` follow the format:
   `auto-speech <subcommand>: <explanatory message>` or `control: <explanatory message>`.
2. **Standard Stream Separation**:
   - Machine data and positive confirmations (`paused`, `resumed`, `stopped`, JSON reports) go exclusively to `sys.stdout`.
   - Informational diagnostics (e.g. replay cache entry metadata `[replay] ordinal=...`, global mute suppression notices, errors) go exclusively to `sys.stderr`.
3. **No Silent Failures**:
   Per the *Defensive Incompleteness* and *Anti-Ungrounded Logic* rules, no failed operation will return a synthetic exit 0. If a command cannot complete, it must emit a descriptive error to `sys.stderr` and exit with its designated non-zero code.
4. **Hermetic Standard Library Isolation**:
   `cli.py` will not import heavy non-standard packages (`mlx`, `torch`) directly. All third-party interactions occur via the isolated daemon process or standard library socket/subprocess bridges.

---

## 5. Trampoline Integration (`bin/auto-speech`)

The executable trampoline `bin/auto-speech` ensures that invocation from any shell environment correctly resolves `.venv/bin/python`:

```bash
#!/usr/bin/env bash
# auto-speech — unified CLI dispatcher trampoline
set -euo pipefail

# Resolve symlinks to locate the true project root
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
PROJECT_ROOT="$(cd -P "$(dirname "$SOURCE")/.." && pwd)"

VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
if [ ! -x "$VENV_PYTHON" ]; then
  # Fallback: check recorded project root from ~/.config/auto-speech/root
  RECORDED_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  if [ -n "$RECORDED_ROOT" ] && [ -x "$RECORDED_ROOT/.venv/bin/python" ]; then
    VENV_PYTHON="$RECORDED_ROOT/.venv/bin/python"
    PROJECT_ROOT="$RECORDED_ROOT"
  else
    echo "auto-speech: virtual environment not found at $VENV_PYTHON. Run setup/install.sh first." >&2
    exit 1
  fi
fi

exec "$VENV_PYTHON" "$PROJECT_ROOT/plugin/scripts/python/cli.py" "$@"
```

- Linked into `~/.local/bin/auto-speech -> <PROJECT_ROOT>/bin/auto-speech`.
- Permissions: `chmod +x bin/auto-speech`.

---

## 6. Comprehensive Verification Plan (`tests/test_cli_dispatcher.py`)

A dedicated unit and integration test suite `tests/test_cli_dispatcher.py` will be created to verify all 12 subcommands and boundary cases:

### 6.1 Test Suites Matrix

1. **Parser & Help Integrity**:
   - `test_cli_help`: `cli.main(["--help"])` exits `0`, stdout includes all 12 subcommands.
   - `test_cli_version`: `cli.main(["--version"])` exits `0`, stdout includes version.
   - `test_cli_no_args`: `cli.main([])` exits `2`, stderr includes usage.
   - `test_cli_subcommand_help`: Each subcommand with `["<cmd>", "--help"]` exits `0`.
2. **`speak` Tests**:
   - `test_speak_positional_words`: Mocks `DaemonClient.speak`, verifies text concatenation and exit `0`.
   - `test_speak_piped_stdin`: Patches `sys.stdin`, verifies text read and dispatched.
   - `test_speak_global_mute`: Creates `~/.claude/auto-speech.disabled`, verifies immediate exit `0` with stderr note.
   - `test_speak_bad_hash`: Passes `--source-hash invalid`, verifies exit `2` and stderr diagnostic.
   - `test_speak_cached_playback`: Positional words empty, valid `--source-hash`, calls `DaemonClient.play_cache`.
3. **`replay` Tests**:
   - `test_replay_bad_ordinal`: `--ordinal 0` exits `2`.
   - `test_replay_empty_cache`: Empty cache directory exits `2`.
   - `test_replay_out_of_bounds`: Ordinal exceeds cache size exits `2`.
   - `test_replay_playback`: Valid ordinal plays audio via daemon/sink, exits `0`.
4. **Transport Control Tests**:
   - `test_control_no_session`: When socket absent, `pause`, `resume`, `restart`, `seek` exit `2` with `no active playback session`.
   - `test_control_seek_bad_arg`: `seek` with `invalid` or empty target exits `4`.
   - `test_control_seek_parsing`: Tests `+10`, `-5`, `15.0`, `end` format parsing and dispatch.
   - `test_control_stop`: `stop` calls `DaemonClient.interrupt()` and exits `0`.
5. **`autoplay` Tests**:
   - `test_autoplay_on_off`: `autoplay on` creates session marker; `autoplay off` removes it.
   - `test_autoplay_status`: Exits `0`, prints formatted status.
   - `test_autoplay_mode_show_and_set`: `mode small` writes TOML; invalid mode exits `4`.
   - `test_autoplay_scope`: `scope all` clears solo marker; `scope solo` sets marker; invalid scope exits `4`.
6. **`narrate` Tests**:
   - `test_narrate_on_off`: `narrate on` creates session marker; `narrate off` removes it.
   - `test_narrate_status`: Exits `0`.
   - `test_narrate_stop`: Signals daemon PID and unlinks PID file.
   - `test_narrate_config`: Handles `show`, `path`, `edit`.
7. **`hook` Tests**:
   - `test_hook_stop`: Consumes JSON payload, enforces gates, touches beacon.
   - `test_hook_narrator`: Consumes JSON payload, appends event to `/tmp/auto-speech-narrator-events.jsonl`.
   - `test_hook_bootstrap`: Handles lock comparison.
8. **`doctor` & `update` Tests**:
   - `test_doctor_text_and_json`: `doctor` and `doctor --json` return correct exit code (`0` or `1`).
   - `test_update_force`: Passes `--force` to bootstrap script.

---

## 7. Next Implementation Steps for Milestone M1

1. Implement bash trampoline `bin/auto-speech` with executable permissions in worktree.
2. Implement `plugin/scripts/python/cli.py` implementing top-level parser and 12 subcommand handlers.
3. Implement test suite `tests/test_cli_dispatcher.py` covering all parser configurations and exit codes.
4. Verify tests pass via `bash tests/run_all.sh` (must be 50/50 green).
5. Verify `ruff check .` returns 0 violations.
6. Link `~/.local/bin/auto-speech -> bin/auto-speech`.
