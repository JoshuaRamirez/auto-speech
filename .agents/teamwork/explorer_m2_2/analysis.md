# Analysis & Specification: Milestone M2 MCP Tools (`playback_control`, `session_configure`, `doctor`)

**Investigator**: `explorer_m2_2` (Teamwork Explorer)  
**Date**: 2026-10-06  
**Target Codebase**: `plugin/scripts/python/mcp_server.py` & `tests/test_mcp_server.py`  
**Execution Context**: Isolated worktree at `/Users/joshua/Developer/auto-speech-worktree`  
**Parent Orchestrator**: `4db9af0e-498d-49a5-831a-0009fd31326e`  

---

## 1. Executive Summary & Problem Boundary

Milestone M2 elevates the `auto-speech` Model Context Protocol (MCP) server from a single-purpose utility (`speak`) into a complete 6-tool programmatic control surface. This enables AI agent swarms and orchestrators in Claude Code and Antigravity to interact with speech synthesis, audio transport, session enrollment, and diagnostics without relying on external bash scripts or shell lifecycle hooks.

This specification establishes the concrete, implementation-ready design for three specific tools assigned to `explorer_m2_2`:
1. **`playback_control`**: Authoritative transport management (`pause`, `resume`, `stop`, `restart`, `seek`) via `/tmp/auto-speech-mpv.sock` and `DaemonClient.interrupt()`, plus cached audio `replay` via `CacheStore` and `DaemonClient.play_cache()`.
2. **`session_configure`**: Configuration of session enrollment markers (`~/.claude/auto-speech-autoplay-enabled/<session_id>`), narration markers (`~/.claude/auto-speech-narrate-sessions/<session_id>`), solo scope markers (`~/.claude/auto-speech-autoplay-solo`), and autoplay TOML settings (`~/.config/auto-speech/autoplay.toml`).
3. **`doctor`**: Health diagnostics probe invoking `Doctor().run()`, rendering structured text or JSON, and signaling degraded system health via `isError: True`.
4. **Injectable Collaborators Pattern**: Refactoring `McpServer.__init__` so every subsystem dependency (`home`, `client`, `gate`, `mpv_sock_path`, `mpv_ipc_sender`, `cache_store`, `enrollment`, `scope`, `doctor`) can be hermetically mocked in unit tests without touching real user directories, killing processes, or binding sockets.

---

## 2. Architectural Collaborator Ecosystem

All three tools operate within the zero-subprocess, standard-library-only execution model of `mcp_server.py`. They interact with established, battle-tested components already present in `plugin/scripts/python/`:

```
               ┌────────────────────────────────────────────────────────┐
               │              mcp_server.py: McpServer                  │
               │  - handle(msg) -> JSON-RPC 2.0 Dispatch                │
               │  - _call_playback_control()                            │
               │  - _call_session_configure()                           │
               │  - _call_doctor()                                      │
               └────────────┬─────────────┬─────────────┬───────────────┘
                            │             │             │
        ┌───────────────────┴──┐          │          ┌──┴────────────────────┐
        ▼                      ▼          │          ▼                       ▼
 ┌───────────────┐     ┌──────────────┐   │   ┌──────────────┐      ┌────────────────┐
 │ DaemonClient  │     │   MpvIpc     │   │   │ CacheStore   │      │ AutoplayGate   │
 │ - interrupt() │     │ - send()     │   │   │ - list_by_   │      │ - worker_      │
 │ - play_cache()│     │   to mpv sock│   │   │   recency()  │      │   gated_off()  │
 └──────┬────────┘     └──────┬───────┘   │   └──────────────┘      └────────────────┘
        │                     │           │
        ▼                     ▼           │
 ┌──────────────┐      ┌──────────────┐   │
 │ Daemon Sock  │      │   mpv IPC    │   │
 │ (/tmp/auto-  │      │ (/tmp/auto-  │   │
 │ speech-      │      │ speech-mpv.  │   │
 │ daemon.sock) │      │ sock)        │   │
 └──────────────┘      └──────────────┘   │
                                          ▼
                      ┌───────────────────────────────────────┐
                      │ Markers & Configuration Layer         │
                      │ - SessionEnrollment (~/.claude/...)   │
                      │ - SoloScope (~/.claude/autoplay-solo) │
                      │ - Doctor & HealthReport               │
                      │ - autoplay.toml (~/.config/...)       │
                      └───────────────────────────────────────┘
```

### Module Location & Path Resolution
In `plugin/scripts/python/mcp_server.py`:
- `_PYTHON_DIR = Path(__file__).resolve().parent`
- `_PROJECT_ROOT = Path(__file__).resolve().parents[3]` (matches `cli.py` and `doctor.py`)
- `_DEFAULT_CACHE_ROOT = _PROJECT_ROOT / "config" / "cache"`
- `_DEFAULT_MPV_SOCK = Path("/tmp/auto-speech-mpv.sock")`
- `_DEFAULT_MPV_PID = Path("/tmp/auto-speech-mpv.pid")`

---

## 3. Tool Specification: `playback_control`

### 3.1 Tool Schema
```python
PLAYBACK_CONTROL_TOOL = {
    "name": "playback_control",
    "title": "Manage audio playback transport and replay",
    "description": (
        "Controls audio playback: pause, resume, stop (halt and purge queue), "
        "restart, seek, or replay recent cached audio."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["pause", "resume", "stop", "restart", "seek", "replay"],
                "description": "The transport action to execute.",
            },
            "target": {
                "type": "string",
                "description": (
                    "Target position for seek (e.g. '+15', '-10', '0', 'end'). "
                    "Required when action is 'seek'."
                ),
            },
            "ordinal": {
                "type": "integer",
                "default": 1,
                "description": (
                    "1-indexed recency ordinal for replay (1 = newest cached speech, "
                    "default: 1)."
                ),
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}
```

### 3.2 Action Semantics & Execution Matrix

| Action | Target Required? | Mute Checked? | Primary Target | Fallback / Cleanup | Output on Success |
|---|---|---|---|---|---|
| `stop` | No | No | `DaemonClient.interrupt()` | `MpvIpc.send(["stop"])` + kill PID in `/tmp/auto-speech-mpv.pid` | `"Playback stopped and queue cleared."` |
| `pause` | No | No | `MpvIpc.send(["set_property", "pause", True])` | Returns error if socket not alive | `"Playback paused."` |
| `resume` | No | **Yes** | `MpvIpc.send(["set_property", "pause", False])` | Returns error if socket not alive | `"Playback resumed."` |
| `restart` | No | No | `MpvIpc.send(["seek", 0, "absolute"])` | Returns error if socket not alive | `"Playback restarted from beginning."` |
| `seek` | **Yes** | No | `MpvIpc.send(["seek", val, mode])` | Validates target syntax; error if socket not alive | `"Seeked to <target>."` |
| `replay` | No | **Yes** | `CacheStore.list_by_recency()` -> `DaemonClient.play_cache(hash)` | Validates ordinal (1 <= ordinal <= len) | `"Replaying cached audio (ordinal=N, voice=V, duration=X.Xs)."` |

### 3.3 Detailed Handler Logic (`_call_playback_control`)

```python
def _call_playback_control(self, req_id: Any, args: dict) -> dict:
    action = args.get("action")
    if not isinstance(action, str) or action not in (
        "pause", "resume", "stop", "restart", "seek", "replay"
    ):
        return _ok(
            req_id,
            _tool_result(
                f"invalid action: {action!r} (expected pause, resume, stop, restart, seek, replay)",
                is_error=True,
            ),
        )

    # 1. STOP
    if action == "stop":
        client = self._client or DaemonClient()
        try:
            client.interrupt()
        except Exception:
            pass

        if self._is_mpv_socket_alive():
            try:
                self._mpv_ipc_send(["stop"], self._mpv_sock_path)
            except Exception:
                pass

        mpv_pid_file = Path("/tmp/auto-speech-mpv.pid")
        if mpv_pid_file.is_file():
            try:
                pid = int(mpv_pid_file.read_text().strip())
                os.kill(pid, 15)
                time.sleep(0.05)
                try:
                    os.kill(pid, 0)
                    os.kill(pid, 9)
                except OSError:
                    pass
            except (ValueError, OSError):
                pass
            finally:
                try:
                    mpv_pid_file.unlink()
                except OSError:
                    pass

        return _ok(req_id, _tool_result("Playback stopped and queue cleared."))

    # 2. PAUSE / RESUME / RESTART
    if action in ("pause", "resume", "restart"):
        if action == "resume" and self._gate.worker_gated_off():
            return _ok(
                req_id,
                _tool_result(
                    "auto-speech is globally muted (~/.claude/auto-speech.disabled); resume suppressed.",
                    is_error=True,
                ),
            )

        if not self._is_mpv_socket_alive():
            return _ok(req_id, _tool_result("No active playback session.", is_error=True))

        if action == "pause":
            cmd = ["set_property", "pause", True]
            msg = "Playback paused."
        elif action == "resume":
            cmd = ["set_property", "pause", False]
            msg = "Playback resumed."
        else:  # restart
            cmd = ["seek", 0, "absolute"]
            msg = "Playback restarted from beginning."

        try:
            reply = self._mpv_ipc_send(cmd, self._mpv_sock_path)
        except Exception as exc:
            return _ok(req_id, _tool_result(f"mpv IPC failure: {exc}", is_error=True))

        if reply.get("error") != "success":
            return _ok(
                req_id,
                _tool_result(f"mpv command returned error: {reply.get('error')}", is_error=True),
            )
        return _ok(req_id, _tool_result(msg))

    # 3. SEEK
    if action == "seek":
        target = args.get("target")
        if not target or not isinstance(target, str) or not target.strip():
            return _ok(
                req_id,
                _tool_result("seek requires a target (+N, -N, N, or 'end')", is_error=True),
            )
        target = target.strip()

        if target == "end":
            cmd = ["seek", 999999, "absolute"]
        elif target.startswith("+"):
            try:
                val = float(target[1:])
                if not math.isfinite(val):
                    raise ValueError
                cmd = ["seek", val, "relative"]
            except ValueError:
                return _ok(
                    req_id,
                    _tool_result(f"invalid seek target: {target!r} (expected +N, -N, N, or 'end')", is_error=True),
                )
        elif target.startswith("-"):
            try:
                val = float(target)
                if not math.isfinite(val):
                    raise ValueError
                cmd = ["seek", val, "relative"]
            except ValueError:
                return _ok(
                    req_id,
                    _tool_result(f"invalid seek target: {target!r} (expected +N, -N, N, or 'end')", is_error=True),
                )
        else:
            try:
                val = float(target)
                if not math.isfinite(val) or val < 0:
                    raise ValueError
                cmd = ["seek", val, "absolute"]
            except ValueError:
                return _ok(
                    req_id,
                    _tool_result(f"invalid seek target: {target!r} (expected +N, -N, N, or 'end')", is_error=True),
                )

        if not self._is_mpv_socket_alive():
            return _ok(req_id, _tool_result("No active playback session.", is_error=True))

        try:
            reply = self._mpv_ipc_send(cmd, self._mpv_sock_path)
        except Exception as exc:
            return _ok(req_id, _tool_result(f"mpv IPC failure: {exc}", is_error=True))

        if reply.get("error") != "success":
            return _ok(
                req_id,
                _tool_result(f"mpv command returned error: {reply.get('error')}", is_error=True),
            )
        return _ok(req_id, _tool_result(f"Seeked to {target}."))

    # 4. REPLAY
    if action == "replay":
        if self._gate.worker_gated_off():
            return _ok(
                req_id,
                _tool_result(
                    "auto-speech is globally muted (~/.claude/auto-speech.disabled); replay suppressed.",
                    is_error=True,
                ),
            )

        ordinal = args.get("ordinal", 1)
        if not isinstance(ordinal, int) or ordinal < 1:
            return _ok(
                req_id,
                _tool_result(f"ordinal must be an integer >= 1, got {ordinal!r}", is_error=True),
            )

        entries = self._cache_store.list_by_recency()
        if not entries:
            return _ok(
                req_id,
                _tool_result("No cached audio entries found to replay.", is_error=True),
            )
        if ordinal > len(entries):
            return _ok(
                req_id,
                _tool_result(
                    f"Only {len(entries)} cache entries available; requested #{ordinal}.",
                    is_error=True,
                ),
            )

        wav_path, entry = entries[ordinal - 1]
        client = self._client or DaemonClient()
        try:
            resp = client.play_cache(entry.source_hash)
            if resp.status not in ("ok", "queued"):
                return _ok(
                    req_id,
                    _tool_result(f"Replay request rejected by daemon: {resp.message}", is_error=True),
                )
        except Exception as exc:
            return _ok(
                req_id,
                _tool_result(f"Could not connect to daemon for replay: {exc}", is_error=True),
            )

        return _ok(
            req_id,
            _tool_result(
                f"Replaying cached audio (ordinal={ordinal}, voice={entry.voice_id}, duration={entry.duration_seconds:.1f}s)."
            ),
        )
```

---

## 4. Tool Specification: `session_configure`

### 4.1 Tool Schema
```python
SESSION_CONFIGURE_TOOL = {
    "name": "session_configure",
    "title": "Configure session enrollment, mode, and scoping",
    "description": (
        "Updates session enrollment for autoplay and narration, sets "
        "autoplay rewrite modes, and configures solo vs all scoping."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": (
                    "Target session ID. Defaults to environment "
                    "(CLAUDE_CODE_SESSION_ID) or current session."
                ),
            },
            "autoplay": {
                "type": "string",
                "enum": ["on", "off", "status"],
                "description": "Enable, disable, or query autoplay enrollment for this session.",
            },
            "narrate": {
                "type": "string",
                "enum": ["on", "off", "status"],
                "description": "Enable, disable, or query narration enrollment for this session.",
            },
            "mode": {
                "type": "string",
                "enum": ["summary", "verbatim", "raw"],
                "description": "Autoplay speech rewrite mode.",
            },
            "size": {
                "type": "string",
                "enum": ["small", "medium", "large"],
                "description": "Autoplay summary length (used when mode is 'summary').",
            },
            "scope": {
                "type": "string",
                "enum": ["all", "solo", "status"],
                "description": (
                    "Autoplay scope: 'solo' (only this session speaks), 'all' (every "
                    "enrolled session speaks), or 'status'."
                ),
            },
        },
        "additionalProperties": False,
    },
}
```

### 4.2 Parameter & Marker Interaction Matrix

| Parameter | Value | Target Path / Mechanism | Action Performed |
|---|---|---|---|
| `autoplay` | `"on"` | `~/.claude/auto-speech-autoplay-enabled/<sid>` | Creates marker via `SessionEnrollment.enroll(sid)`. Unlinks `~/.claude/auto-speech.disabled`. Requires `session_id`. |
| `autoplay` | `"off"` | `~/.claude/auto-speech-autoplay-enabled/<sid>` | Deletes marker via `SessionEnrollment.withdraw(sid)`. If no `session_id`, touches global mute `~/.claude/auto-speech.disabled`. |
| `autoplay` | `"status"` | N/A | Formats enrollment status via `format_enrollment_status(enrollment, sid)`. |
| `narrate` | `"on"` | `~/.claude/auto-speech-narrate-sessions/<sid>` | Creates marker file. Unlinks legacy marker. Calls `DaemonClient.ensure_daemon_running()`. Requires `session_id`. |
| `narrate` | `"off"` | `~/.claude/auto-speech-narrate-sessions/<sid>` | Deletes marker file. |
| `narrate` | `"status"` | N/A | Checks marker existence, counts total enrolled sessions, checks daemon liveness. |
| `scope` | `"solo"` | `~/.claude/auto-speech-autoplay-solo` | Calls `SoloScope.set_solo(sid)`. Auto-enrolls session in autoplay. Requires `session_id`. |
| `scope` | `"all"` | `~/.claude/auto-speech-autoplay-solo` | Calls `SoloScope.clear()` (removes marker). |
| `scope` | `"status"` | N/A | Formats scope status via `format_scope_status(scope, sid)`. |
| `mode` / `size` | Valid enums | `~/.config/auto-speech/autoplay.toml` | Writes `[autoplay]\nmode = "..."\nsummary_size = "..."\n`. |

### 4.3 Detailed Handler Logic (`_call_session_configure`)

```python
def _call_session_configure(self, req_id: Any, args: dict) -> dict:
    raw_sid = args.get("session_id")
    session_id = (
        raw_sid.strip() if isinstance(raw_sid, str) else ""
    ) or os.environ.get("CLAUDE_CODE_SESSION_ID", "")

    results: list[str] = []

    # 1. AUTOPLAY
    if "autoplay" in args:
        ap_act = args.get("autoplay")
        if ap_act not in ("on", "off", "status"):
            return _ok(
                req_id,
                _tool_result(f"invalid autoplay action: {ap_act!r} (expected on, off, status)", is_error=True),
            )
        if ap_act == "on":
            if not session_id:
                return _ok(req_id, _tool_result("autoplay on requires a session_id", is_error=True))
            disabled_marker = self._home / ".claude" / "auto-speech.disabled"
            try:
                disabled_marker.unlink(missing_ok=True)
            except OSError:
                pass
            self._enrollment.enroll(session_id)
            results.append(f"autoplay: enabled for session {session_id}")
        elif ap_act == "off":
            if session_id:
                self._enrollment.withdraw(session_id)
                results.append(f"autoplay: disabled for session {session_id}")
            else:
                mute_path = self._home / ".claude" / "auto-speech.disabled"
                mute_path.parent.mkdir(parents=True, exist_ok=True)
                mute_path.touch()
                results.append("autoplay: no session_id provided — set global mute marker (~/.claude/auto-speech.disabled)")
        elif ap_act == "status":
            results.append(format_enrollment_status(self._enrollment, session_id or None))

    # 2. NARRATE
    if "narrate" in args:
        narrate_act = args.get("narrate")
        if narrate_act not in ("on", "off", "status"):
            return _ok(
                req_id,
                _tool_result(f"invalid narrate action: {narrate_act!r} (expected on, off, status)", is_error=True),
            )
        narrate_dir = self._home / ".claude" / "auto-speech-narrate-sessions"
        if narrate_act == "on":
            if not session_id:
                return _ok(req_id, _tool_result("narrate on requires a session_id", is_error=True))
            narrate_dir.mkdir(parents=True, exist_ok=True)
            (narrate_dir / session_id).touch()
            try:
                (Path.cwd() / ".claude" / "narrate.enabled").unlink(missing_ok=True)
            except OSError:
                pass
            client = self._client or DaemonClient()
            client.ensure_daemon_running()
            results.append(f"narrate: enabled for session {session_id}")
        elif narrate_act == "off":
            if session_id:
                marker = narrate_dir / session_id
                try:
                    marker.unlink(missing_ok=True)
                except OSError:
                    pass
                results.append(f"narrate: disabled for session {session_id}")
            else:
                results.append("narrate off: no session_id provided")
        elif narrate_act == "status":
            is_enrolled = (narrate_dir / session_id).is_file() if session_id and narrate_dir.is_dir() else False
            total = len(list(narrate_dir.glob("*"))) if narrate_dir.is_dir() else 0
            client = self._client or DaemonClient()
            daemon_alive = client.is_alive()
            results.append(
                f"narrate: session={session_id or '<none>'}, enrolled={is_enrolled} (total enrolled: {total}), daemon_alive={daemon_alive}"
            )

    # 3. SCOPE
    if "scope" in args:
        scope_act = args.get("scope")
        if scope_act not in ("all", "solo", "status"):
            return _ok(
                req_id,
                _tool_result(f"invalid scope action: {scope_act!r} (expected all, solo, status)", is_error=True),
            )
        if scope_act == "solo":
            if not session_id:
                return _ok(req_id, _tool_result("scope solo requires a session_id", is_error=True))
            self._scope.set_solo(session_id)
            self._enrollment.enroll(session_id)
            results.append(f"autoplay scope: SOLO — only THIS session reads (spotlight={session_id})")
        elif scope_act == "all":
            self._scope.clear()
            results.append("autoplay scope: ALL — every enrolled session reads (default)")
        elif scope_act == "status":
            results.append(format_scope_status(self._scope, session_id or None))

    # 4. MODE / SIZE
    if "mode" in args or "size" in args:
        mode_val = args.get("mode")
        size_val = args.get("size")
        if mode_val is not None and mode_val not in ("summary", "verbatim", "raw"):
            return _ok(
                req_id,
                _tool_result(f"invalid mode: {mode_val!r} (expected summary, verbatim, raw)", is_error=True),
            )
        if size_val is not None and size_val not in ("small", "medium", "large"):
            return _ok(
                req_id,
                _tool_result(f"invalid size: {size_val!r} (expected small, medium, large)", is_error=True),
            )

        cfg = load_autoplay_config()
        cur_mode = mode_val or cfg.get("mode", "summary")
        cur_size = size_val or cfg.get("summary_size", "small")
        if cur_mode in ("verbatim", "raw") and size_val is None:
            cur_size = "small"

        config_dir = self._home / ".config" / "auto-speech"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "autoplay.toml"
        config_file.write_text(
            f'[autoplay]\nmode = "{cur_mode}"\nsummary_size = "{cur_size}"\n',
            encoding="utf-8",
        )
        results.append(f"wrote {config_file}: mode={cur_mode} summary_size={cur_size}")

    # 5. NO ARGS -> RETURN SUMMARY
    if not results:
        results.append(format_enrollment_status(self._enrollment, session_id or None))
        results.append(format_scope_status(self._scope, session_id or None))
        cfg = load_autoplay_config()
        results.append(f"mode={cfg.get('mode')} summary_size={cfg.get('summary_size')}")

    return _ok(req_id, _tool_result("\n".join(results)))
```

---

## 5. Tool Specification: `doctor`

### 5.1 Tool Schema
```python
DOCTOR_TOOL = {
    "name": "doctor",
    "title": "Run system health checks and diagnostics",
    "description": (
        "Runs comprehensive health checks across binaries (mpv, uv, jq), "
        "Python virtual environment, disk headroom in /tmp, log rotation, "
        "narrator daemon status, queue depths, scopes, MCP config, and "
        "uv.lock synchronization."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "as_json": {
                "type": "boolean",
                "default": False,
                "description": (
                    "If true, returns structured JSON output. "
                    "If false, returns human-readable text report."
                ),
            },
        },
        "additionalProperties": False,
    },
}
```

### 5.2 Diagnostic Pipeline & Probe Mechanics
The `doctor` tool delegates directly to `Doctor().run()`. The probes checked by `Doctor` include:
1. `_check_binaries`: `mpv` (FAIL if missing), `uv` (WARN if missing), `jq` (WARN if missing).
2. `_check_venv`: executable virtual environment Python at `.venv/bin/python`.
3. `_check_disk`: `/tmp` free space (FAIL if < 50 MiB, WARN if < 200 MiB).
4. `_check_logs`: log sizes under 10 MiB rotation limit.
5. `_check_daemon`: narrator daemon PID active and valid.
6. `_check_queue`: `/tmp/auto-speech-narration-depth` under `max_queue_depth` (32).
7. `_check_scope`: global mute and solo scope state.
8. `_check_mcp`: MCP registration in `~/.claude.json` and executable launcher script.
9. `_check_config`: TOML validity of `autoplay.toml` and `narrator.toml`.
10. `_check_updates`: `uv.lock` timestamp vs `setup/.synced`.

### 5.3 Detailed Handler Logic (`_call_doctor`)

```python
def _call_doctor(self, req_id: Any, args: dict) -> dict:
    as_json = bool(args.get("as_json", False))
    try:
        report = self._doctor.run()
    except Exception as exc:
        return _ok(req_id, _tool_result(f"doctor check crashed: {exc}", is_error=True))

    content = report.to_json() if as_json else report.to_text()
    is_error = not report.healthy
    return _ok(req_id, _tool_result(content, is_error=is_error))
```

---

## 6. Injectable Collaborators Pattern in `McpServer.__init__`

### 6.1 Design Goals
1. **Full Hermetic Test Isolation**: Tests must never access the real `~/.claude`, `/tmp`, live UNIX sockets, or real processes.
2. **Explicit Dependency Injection**: Avoid all global monkeypatching where possible; allow tests to supply mock instances or test directories.
3. **Graceful Production Defaults**: Zero-argument `McpServer()` must transparently instantiate standard production collaborators.

### 6.2 Proposed `McpServer.__init__` Structure

```python
class McpServer:
    """Handles one JSON-RPC message at a time. Collaborators injectable."""

    def __init__(
        self,
        *,
        spawner=None,
        gate: AutoplayGate | None = None,
        client: DaemonClient | None = None,
        home: Path | None = None,
        mpv_sock_path: Path | None = None,
        mpv_ipc_sender=None,
        cache_store: CacheStore | None = None,
        enrollment: SessionEnrollment | None = None,
        scope: SoloScope | None = None,
        doctor: Doctor | None = None,
    ) -> None:
        self._home = Path(home) if home is not None else Path(os.path.expanduser("~"))
        self._spawn = spawner
        self._client = client
        self._gate = gate or AutoplayGate(home=self._home)
        self._mpv_sock_path = (
            Path(mpv_sock_path)
            if mpv_sock_path is not None
            else Path("/tmp/auto-speech-mpv.sock")
        )
        self._mpv_ipc_send = mpv_ipc_sender or MpvIpc.send
        self._cache_store = cache_store or CacheStore(_default_cache_root())
        self._enrollment = enrollment or SessionEnrollment(home=self._home)
        self._scope = scope or SoloScope(home=self._home)
        self._doctor = doctor or Doctor(home=self._home)

    def _is_mpv_socket_alive(self) -> bool:
        try:
            return self._mpv_sock_path.is_socket()
        except OSError:
            return False
```

### 6.3 Tool Dispatch Routing (`_tools_call`)

```python
    def _tools_call(self, req_id: Any, params: dict) -> dict:
        name = params.get("name")
        args = params.get("arguments")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return _error(req_id, INVALID_PARAMS, "arguments must be an object")

        if name == "speak":
            return self._call_speak(req_id, args)
        elif name == "playback_control":
            return self._call_playback_control(req_id, args)
        elif name == "session_configure":
            return self._call_session_configure(req_id, args)
        elif name == "doctor":
            return self._call_doctor(req_id, args)
        # Note: autoplay_turn and narrate_event are dispatched here as well
        else:
            return _error(req_id, INVALID_PARAMS, f"unknown tool: {name}")
```

---

## 7. Comprehensive Verification Strategy & Test Matrix

To guarantee 100% test passing status across all existing suites (`bash tests/run_all.sh` currently 50/50 green), unit tests in `tests/test_mcp_server.py` will verify:

### 7.1 Test Cases for `playback_control`
1. `test_playback_control_invalid_action`: Rejects `"dance"` with `isError: True`.
2. `test_playback_control_stop`: Injects mock `DaemonClient`, asserts `client.interrupt()` called, reports success with `isError: False`.
3. `test_playback_control_pause_no_session`: When socket missing, returns `"No active playback session."` with `isError: True`.
4. `test_playback_control_pause_success`: With stubbed socket & `mpv_ipc_sender`, verifies `["set_property", "pause", True]` sent and returns `isError: False`.
5. `test_playback_control_resume_honors_global_mute`: When `AutoplayGate(muted=True)`, returns mute error.
6. `test_playback_control_resume_success`: Sends `["set_property", "pause", False]`.
7. `test_playback_control_restart_success`: Sends `["seek", 0, "absolute"]`.
8. `test_playback_control_seek_requires_target`: Empty target returns `isError: True`.
9. `test_playback_control_seek_validates_target`: Tests `"+15"` (relative), `"-5"` (relative), `"45"` (absolute), `"end"` (absolute 999999). Rejects `"abc"`, `"+"` alone, `"-abc"`.
10. `test_playback_control_replay_honors_global_mute`: Fails when muted.
11. `test_playback_control_replay_empty_cache`: Returns error when `CacheStore` has 0 entries.
12. `test_playback_control_replay_ordinal_out_of_bounds`: Fails when requesting ordinal 5 of 2 entries.
13. `test_playback_control_replay_success`: Injects mock `CacheStore` with 2 entries, asserts `client.play_cache(entry.source_hash)` called for ordinal 1.

### 7.2 Test Cases for `session_configure`
1. `test_session_configure_autoplay_on`: Touches marker in sandbox `~/.claude/auto-speech-autoplay-enabled/<sid>`, unlinks `disabled` marker, returns success.
2. `test_session_configure_autoplay_off_with_session`: Unlinks marker.
3. `test_session_configure_autoplay_off_without_session`: Creates `~/.claude/auto-speech.disabled`.
4. `test_session_configure_narrate_on`: Creates `~/.claude/auto-speech-narrate-sessions/<sid>`, calls `client.ensure_daemon_running()`.
5. `test_session_configure_narrate_off`: Unlinks marker.
6. `test_session_configure_scope_solo`: Sets solo spotlight in `~/.claude/auto-speech-autoplay-solo`, auto-enrolls session.
7. `test_session_configure_scope_all`: Clears solo spotlight.
8. `test_session_configure_mode_and_size`: Writes `[autoplay]\nmode = "verbatim"\nsummary_size = "small"\n` to `~/.config/auto-speech/autoplay.toml`.
9. `test_session_configure_status_reports`: Returns multi-line status report with enrollment, scope, and mode.

### 7.3 Test Cases for `doctor`
1. `test_doctor_returns_healthy_text`: Stub doctor returns healthy report; tool returns text report with `isError: False`.
2. `test_doctor_returns_healthy_json`: With `as_json=True`, returns valid JSON with `"healthy": true` and `isError: False`.
3. `test_doctor_unhealthy_sets_is_error`: When doctor report has `Status.FAIL`, tool returns output with `isError: True`.

---

## 8. Conclusion & Handoff Readiness

The proposed specifications for `playback_control`, `session_configure`, `doctor`, and the injectable `McpServer.__init__` collaborator pattern provide complete lifecycle hook and CLI feature parity while preserving 100% compliance with existing test harnesses. All dependencies remain strictly standard-library, and execution within the isolated worktree is ready for implementation by the worker agent.
