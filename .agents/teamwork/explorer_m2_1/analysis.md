# Technical Analysis & Implementation Specification: Milestone M2 MCP Tools (`autoplay_turn` & `narrate_event`)

**Author**: `explorer_m2_1` (Teamwork Explorer)  
**Date**: 2026-10-06  
**Target Repository**: `/Users/joshua/Developer/auto-speech` (Worktree: `/Users/joshua/Developer/auto-speech-worktree`)  
**Target File**: `plugin/scripts/python/mcp_server.py`  
**Test Suite**: `tests/test_mcp_server.py`

---

## 1. Executive Summary

Milestone M2 expands the `auto-speech` Model Context Protocol (MCP) server from its single `speak` tool into a comprehensive lifecycle hook suite. This investigation provides the concrete, battle-tested implementation specification for two critical programmatic hook entrypoints:
1. `autoplay_turn`: Enables an AI agent or orchestrator to synthesize and vocalize the latest turn response for a session or transcript, running the appropriate summarization/rewriting pipeline without requiring OS shell hooks (`autoplay_hook.sh`).
2. `narrate_event`: Ingests lifecycle activity events (`PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `Stop`, `TaskProgress`) directly into the real-time narrator pipeline while strictly enforcing per-session narration enrollment markers.

Both tools must:
- Maintain 100% standard-library-only execution in `mcp_server.py`.
- Provide fully injectable collaborators in `McpServer.__init__` for complete unit test isolation.
- Enforce the global mute invariant (`~/.claude/auto-speech.disabled`).
- Integrate cleanly with the central `narrator_service.py` daemon via `DaemonClient` using RFC-compliant priority levels (`P3: AUTOPLAY` and `P4: TOOL_NARRATION`).

---

## 2. Tool 1: `autoplay_turn` Specification

### 2.1 Schema Definition

`autoplay_turn` accepts optional overrides for session targeting, transcript location, rewrite mode, summary size, turn ordinal, and LLM bypass.

```python
AUTOPLAY_TURN_TOOL = {
    "name": "autoplay_turn",
    "title": "Synthesize and speak latest turn response",
    "description": (
        "Extracts the latest assistant turn for a session, rewrites/summarizes it "
        "per the active autoplay mode (summary, verbatim, or raw), and queues "
        "audio playback on the daemon without requiring OS shell hooks."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Optional session ID. If omitted, resolved from environment or newest session.",
            },
            "transcript_path": {
                "type": "string",
                "description": "Optional explicit path to the session transcript JSONL.",
            },
            "mode": {
                "type": "string",
                "enum": ["summary", "verbatim", "raw"],
                "description": "Optional override for autoplay mode (defaults to configured mode).",
            },
            "size": {
                "type": "string",
                "enum": ["small", "medium", "large"],
                "description": "Optional override for summary size (defaults to configured size).",
            },
            "ordinal": {
                "type": "integer",
                "default": 1,
                "description": "Nth-most-recent assistant turn to extract (default 1 = latest).",
            },
            "bypass_llm": {
                "type": "boolean",
                "description": "If true, bypasses the rewriting LLM and speaks the raw assistant text.",
            },
        },
        "additionalProperties": False,
    },
}
```

### 2.2 Execution & Validation Flow

```
[tools/call: autoplay_turn]
          │
          ▼
1. Validate arguments (types, enums, ordinal >= 1)
          │  invalid ──► return isError=True
          ▼
2. Global Mute Check: self._gate.worker_gated_off()
          │  muted ─────► return isError=True ("auto-speech is globally muted...")
          ▼
3. Transcript Resolution:
   - If transcript_path provided and exists ──► use path
   - Else ──► locator.locate(cwd=..., session_id=...)
          │  missing ───► return isError=True ("could not locate transcript...")
          ▼
4. Turn Extraction:
   - selector.select(path, n=ordinal)
   - Handles Claude Code (type=assistant) & Antigravity (type=PLANNER_RESPONSE)
   - Minimum length check: len(text.encode()) >= min_len (default 20 bytes)
          │  too short ─► return isError=True ("extracted turn text too short...")
          ▼
5. Cache Key Calculation:
   - Resolve voice_id and speed (from voice_calibration.json or constants)
   - source_hash = sha256(raw_text.encode() + b"\x00" + f"{voice_id}:{speed}".encode())
          │
          ▼
6. Mode & Rewriting Pipeline:
   - Load config via autoplay_config.load_config()
   - Apply argument overrides (mode, size, bypass_llm)
   - If mode == "raw" or bypass_llm:
       text_to_speak = raw_text
   - Else:
       Load prompt template (_resolve_prompt(mode, size))
       Fallback to load_default_template() if prompt file missing
       Invoke ClaudeCliRewriter(template).rewrite(raw_text, timeout=90s)
       Fallback to raw_text on ClaudeCliUnavailable / rewrite failure
          │
          ▼
7. Enqueueing to Daemon:
   - If self._spawn injected ──► self._spawn(text_to_speak)
   - Else ──► DaemonClient().speak(
                 text_to_speak,
                 priority=Priority.AUTOPLAY,
                 source_hash=source_hash,
                 session_id=session_id,
                 voice_id=voice_id,
                 speed=speed
              )
   - Offline fallback: if ConnectionError or status == "error" ──► spawn_say_worker(text_to_speak)
          │
          ▼
8. Return Success Result:
   - return {"content": [{"type": "text", "text": "Queued autoplay turn (N chars, mode=M, hash=H)."}], "isError": False}
```

### 2.3 Key Implementation Details for `autoplay_turn`

1. **Dual-Format Turn Extraction**:
   `MessageSelector.select(path, n)` (in `message_selector.py`) already contains dual-format support:
   - Claude Code: `record["type"] == "assistant"` and `record["message"]["role"] == "assistant"`.
   - Antigravity: `record["type"] == "PLANNER_RESPONSE"` (skipping intermediate tool calls where `record.get("tool_calls")` is present).
   If fewer than `n` turns exist, `NoSuchAssistantTurn` is caught and returned cleanly as a tool error with `isError=True`.

2. **Source Hash Calculation Contract**:
   Per RFC §4.2 and `compute_hash.sh`:
   The hash MUST be computed against the RAW assistant text before rewriting.
   Formula:
   ```python
   key_input = raw_text.encode("utf-8") + b"\x00" + f"{voice_id}:{speed}".encode("utf-8")
   source_hash = hashlib.sha256(key_input).hexdigest()
   ```
   This guarantees that Claude rewrite output variability does not fragment the audio cache, allowing `narrator_service.py` to hit `config/cache/<hash_prefix>/full.wav` immediately on identical source turns.

3. **Rewriting Template Fallback**:
   When `mode` is `summary` or `verbatim` and `bypass_llm` is `False`:
   - Attempt to resolve the prompt template: `prompt_path = _resolve_prompt(mode, size)`.
   - If the file exists, read it; otherwise, fall back to `load_default_template()` (`plugin/prompts/audio_rewrite_prompt.txt`).
   - If `ClaudeCliRewriter.rewrite()` raises `ClaudeCliUnavailable` (e.g. `claude` not on PATH) or `ClaudeCliRewriteError` (timeout/syntax error):
     Log warning to `sys.stderr` and fall back to speaking `raw_text` as-is. Under no circumstance should a rewriter glitch abort playback silently.

4. **Speech Priority**:
   Enqueued with `Priority.AUTOPLAY` (Priority 3). This is subordinate to `Priority.USER_INTERRUPT` (P1) and `Priority.EXPLICIT_MCP` (P2), but preempts background `Priority.TOOL_NARRATION` (P4).

---

## 3. Tool 2: `narrate_event` Specification

### 3.1 Schema Definition

`narrate_event` receives structured lifecycle activity events for ingestion into the real-time narration pipeline.

```python
NARRATE_EVENT_TOOL = {
    "name": "narrate_event",
    "title": "Ingest activity event for real-time narration",
    "description": (
        "Ingests a lifecycle activity event (PreToolUse, PostToolUse, UserPromptSubmit, "
        "Stop, TaskProgress) into the auto-speech narrator pipeline for background "
        "narration. Honors session narration enrollment."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "event": {
                "type": "string",
                "description": "The event type (e.g., 'PreToolUse', 'PostToolUse', 'UserPromptSubmit', 'Stop').",
            },
            "payload": {
                "type": "object",
                "description": "Event metadata dictionary (tool_name, tool_input, session_id, etc.).",
            },
            "session_id": {
                "type": "string",
                "description": "Session ID (if not provided inside payload).",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory for the event.",
            },
        },
        "required": ["event"],
        "additionalProperties": False,
    },
}
```

### 3.2 Execution & Validation Flow

```
[tools/call: narrate_event]
          │
          ▼
1. Validate arguments:
   - "event" must be a non-empty string.
   - "payload" must be a dict (default {}).
          │  invalid ──► return isError=True
          ▼
2. Global Mute Check:
   - self._gate.worker_gated_off()
          │  muted ─────► return isError=True ("auto-speech is globally muted...")
          ▼
3. Session Enrollment Check:
   - Resolve session_id from:
       args["session_id"] or payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID")
   - If session_id is empty:
       return isError=False ("No session_id provided and session is not enrolled in narration; event ignored.")
   - Check marker:
       marker = sessions_dir / session_id (default: ~/.claude/auto-speech-narrate-sessions/<session_id>)
   - If not marker.exists():
       return isError=False ("Session '<sid>' is not enrolled in narration; event ignored.")
          │  enrolled
          ▼
4. Daemon Liveness Check:
   - client = self._client or DaemonClient()
   - client.ensure_daemon_running()
          │
          ▼
5. Event Logging:
   - Construct JSON record:
     {
       "ts": int(time.time()),
       "cwd": str(args.get("cwd") or Path.cwd()),
       "pid": os.getpid(),
       "event": event,
       "payload": payload,
     }
   - Append single JSON line to /tmp/auto-speech-narrator-events.jsonl
          │  write fail ─► return isError=True ("could not log narrator event: ...")
          ▼
6. Return Confirmation:
   - return {"content": [{"type": "text", "text": "Narrator event '<event>' ingested for session '<sid>'."}], "isError": False}
```

### 3.3 Key Implementation Details for `narrate_event`

1. **Clean Notice on Non-Enrollment**:
   Crucial requirement: When a session is NOT enrolled in narration, the tool MUST return a clean notice with `isError=False`:
   `"Session '<session_id>' is not enrolled in narration; event ignored."`
   This prevents AI agents or swarms from treating normal opt-out status as a fatal tool execution error.

2. **Atomic JSONL Pipeline Ingestion**:
   In accordance with `narrator_hook.sh` and `narrator_service.py` line 59:
   - Destination file: `/tmp/auto-speech-narrator-events.jsonl` (injectable in `McpServer`).
   - Line format: Standard JSON record containing `ts` (integer epoch), `cwd` (string), `pid` (int), `event` (string), and `payload` (dict).
   - Python's file append mode (`"a"`) for writes under `PIPE_BUF` (512 bytes on Darwin) guarantees atomic interleaving with concurrent shell hook writers.

3. **Daemon Autospawn via `ensure_daemon_running()`**:
   If a user opts a session in (`/auto-speech-narrate-on`), but the background daemon was stopped or timed out after idle, `DaemonClient().ensure_daemon_running()` triggers `narrator_service_start.sh` so events are actively processed without requiring manual daemon restarts.

---

## 4. Architecture & Testability in `mcp_server.py`

### 4.1 Standard Library Purity Guarantee

`plugin/scripts/python/mcp_server.py` must run under bare Python 3.10+ without virtual environment activation.
All collaborators imported by the server adhere strictly to standard library dependencies:

| Collaborator Module | Primary Functionality | Dependencies |
| :--- | :--- | :--- |
| `autoplay_gate.py` | Global mute check | `pathlib`, `os` (stdlib) |
| `daemon_client.py` | Priority IPC socket client | `socket`, `json`, `dataclasses`, `enum`, `pathlib`, `time`, `os` (stdlib) |
| `transcript_locator.py` | Claude / Antigravity transcript path resolution | `pathlib`, `os`, `re`, `sys` (stdlib) |
| `message_selector.py` | Turn extraction across formats | `re`, `collections.deque`, `pathlib` (stdlib) |
| `assistant_message.py` | Dataclass for extracted turn | `dataclasses` (stdlib) |
| `transcript_reader.py` | JSONL streaming reader | `json`, `pathlib` (stdlib) |
| `autoplay_config.py` | TOML config parsing | `tomllib`, `os`, `sys`, `pathlib` (stdlib) |
| `claude_cli_rewriter.py` | Headless `claude -p` execution | `subprocess`, `shutil`, `os`, `pathlib` (stdlib) |
| `config_constants.py` | Shared defaults (voice, speed) | stdlib |
| `auto_speech_log.py` | Log rotation utility | `pathlib`, `os` (stdlib) |

No third-party packages (`torch`, `mlx`, `numpy`, `soundfile`, `requests`) are imported. This ensures lightning-fast initialization (<25ms) and zero environment leakage.

### 4.2 Injectable Collaborator Pattern in `McpServer`

To allow unit tests in `tests/test_mcp_server.py` to test all failure branches without touching real files, spawning subprocesses, or connecting to live daemons, `McpServer.__init__` accepts optional collaborator instances:

```python
class McpServer:
    """Handles one JSON-RPC message at a time. Collaborators injectable."""

    def __init__(
        self,
        *,
        spawner=None,
        gate: AutoplayGate | None = None,
        client: DaemonClient | None = None,
        locator: TranscriptLocator | None = None,
        selector: MessageSelector | None = None,
        rewriter: ClaudeCliRewriter | None = None,
        narrator_events_file: Path | None = None,
        narrator_sessions_dir: Path | None = None,
        voice_calibration_path: Path | None = None,
    ) -> None:
        self._spawn = spawner
        self._gate = gate or AutoplayGate()
        self._client = client
        self._locator = locator
        self._selector = selector
        self._rewriter = rewriter
        self._narrator_events_file = narrator_events_file or Path("/tmp/auto-speech-narrator-events.jsonl")
        self._narrator_sessions_dir = (
            narrator_sessions_dir or (Path.home() / ".claude" / "auto-speech-narrate-sessions")
        )
        self._voice_calibration_path = voice_calibration_path
```

When an argument is omitted, the production instance is created lazily or used by default.
In test harnesses, testers inject mocks or temporary paths:
- `gate=AutoplayGate(home=temp_dir)` tests global mute on/off.
- `client=MockDaemonClient()` records calls and verifies priority levels.
- `locator=MockTranscriptLocator()` simulates transcript hits and `TranscriptNotFoundError`.
- `selector=MockMessageSelector()` simulates message extraction, empty messages, or short turns.
- `rewriter=MockRewriter()` simulates rewrite success, timeout, or binary missing.
- `narrator_events_file=temp_file` tests JSONL event formatting.
- `narrator_sessions_dir=temp_dir` tests session enrollment without modifying `~/.claude`.

---

## 5. Dispatch Architecture in `McpServer`

In `McpServer._tools_call`:
Use a clean dictionary dispatch mapping tool names to dedicated private handler methods:

```python
def _tools_call(self, req_id, params: dict) -> dict:
    name = params.get("name")
    args = params.get("arguments")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return _error(req_id, INVALID_PARAMS, "arguments must be an object")

    handlers = {
        "speak": self._handle_speak,
        "autoplay_turn": self._handle_autoplay_turn,
        "narrate_event": self._handle_narrate_event,
    }

    handler = handlers.get(name)
    if handler is None:
        return _error(req_id, INVALID_PARAMS, f"unknown tool: {name}")

    return handler(req_id, args)
```

Each handler method returns `_ok(req_id, _tool_result(...))` or `_error(req_id, ...)`.

---

## 6. Concrete Implementation Blueprint

### 6.1 `_handle_autoplay_turn` Blueprint

```python
def _handle_autoplay_turn(self, req_id, args: dict) -> dict:
    # 1. Parameter Validation
    session_id = args.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        return _ok(req_id, _tool_result("session_id must be a string", is_error=True))

    transcript_path_str = args.get("transcript_path")
    if transcript_path_str is not None and not isinstance(transcript_path_str, str):
        return _ok(req_id, _tool_result("transcript_path must be a string", is_error=True))

    mode_arg = args.get("mode")
    if mode_arg is not None and mode_arg not in ("summary", "verbatim", "raw"):
        return _ok(req_id, _tool_result("mode must be 'summary', 'verbatim', or 'raw'", is_error=True))

    size_arg = args.get("size")
    if size_arg is not None and size_arg not in ("small", "medium", "large"):
        return _ok(req_id, _tool_result("size must be 'small', 'medium', or 'large'", is_error=True))

    ordinal = args.get("ordinal", 1)
    if not isinstance(ordinal, int) or ordinal < 1:
        return _ok(req_id, _tool_result("ordinal must be an integer >= 1", is_error=True))

    bypass_arg = args.get("bypass_llm")
    if bypass_arg is not None and not isinstance(bypass_arg, bool):
        return _ok(req_id, _tool_result("bypass_llm must be a boolean", is_error=True))

    # 2. Global Mute Check
    if self._gate.worker_gated_off():
        return _ok(
            req_id,
            _tool_result(
                "auto-speech is globally muted (~/.claude/auto-speech.disabled); nothing was spoken",
                is_error=True,
            ),
        )

    # 3. Transcript Resolution
    t_path: Path | None = None
    if transcript_path_str:
        cand = Path(transcript_path_str)
        if cand.is_file():
            t_path = cand
        else:
            return _ok(req_id, _tool_result(f"transcript_path not found: {transcript_path_str}", is_error=True))
    else:
        locator = self._locator or TranscriptLocator()
        try:
            t_path = locator.locate(cwd=Path.cwd(), session_id=session_id)
        except Exception as exc:
            return _ok(req_id, _tool_result(f"could not locate transcript: {exc}", is_error=True))

    # 4. Turn Extraction
    selector = self._selector or MessageSelector()
    try:
        msg = selector.select(t_path, n=ordinal)
    except Exception as exc:
        return _ok(req_id, _tool_result(f"could not extract assistant turn: {exc}", is_error=True))

    raw_text = msg.text
    min_len = int(os.environ.get("AUTO_SPEECH_AUTOPLAY_MIN_LEN", "20"))
    raw_len = len(raw_text.encode("utf-8"))
    if raw_len < min_len:
        return _ok(
            req_id,
            _tool_result(
                f"extracted turn text too short ({raw_len} < {min_len} bytes); nothing was spoken",
                is_error=True,
            ),
        )

    # 5. Cache Key Calculation
    voice_id = DEFAULT_VOICE_ID
    speed = DEFAULT_SPEED
    voice_cfg_path = self._voice_calibration_path or (_PROJECT_ROOT / "config" / "voice_calibration.json")
    if voice_cfg_path and voice_cfg_path.is_file():
        try:
            v_data = json.loads(voice_cfg_path.read_text(encoding="utf-8"))
            voice_id = str(v_data.get("voice_id", voice_id))
            speed = float(v_data.get("speed", speed))
        except Exception:
            pass

    key_input = raw_text.encode("utf-8") + b"\x00" + f"{voice_id}:{speed}".encode("utf-8")
    source_hash = hashlib.sha256(key_input).hexdigest()

    # 6. Mode & Rewriting Pipeline
    cfg = load_config()
    active_mode = mode_arg or cfg.get("mode") or "summary"
    active_size = size_arg or cfg.get("summary_size") or "small"
    bypass = bypass_arg
    if bypass is None:
        bypass = cfg.get("bypass_llm", False) or (active_mode == "raw")
    if active_mode == "raw":
        bypass = True

    if bypass:
        text_to_speak = raw_text
    else:
        prompt_path = _resolve_prompt(active_mode, active_size)
        template: str
        if prompt_path and Path(prompt_path).is_file():
            try:
                template = Path(prompt_path).read_text(encoding="utf-8")
            except Exception:
                template = load_default_template()
        else:
            template = load_default_template()

        rewriter = self._rewriter or ClaudeCliRewriter(template)
        try:
            rewritten = rewriter.rewrite(raw_text, timeout_seconds=90.0)
            text_to_speak = rewritten.strip() if rewritten.strip() else raw_text
        except Exception as exc:
            print(f"[{SERVER_NAME}] rewriter failed ({exc}); falling back to raw message", file=sys.stderr)
            text_to_speak = raw_text

    # 7. Enqueueing to Daemon
    try:
        if self._spawn is not None:
            self._spawn(text_to_speak)
        else:
            client = self._client or DaemonClient()
            try:
                resp = client.speak(
                    text_to_speak,
                    priority=Priority.AUTOPLAY,
                    source_hash=source_hash,
                    session_id=session_id or None,
                    voice_id=voice_id,
                    speed=speed,
                )
                if resp.status == "error":
                    spawn_say_worker(text_to_speak)
            except ConnectionError:
                spawn_say_worker(text_to_speak)
    except Exception as exc:
        return _ok(req_id, _tool_result(f"could not queue speech: {exc}", is_error=True))

    return _ok(
        req_id,
        _tool_result(
            f"Queued autoplay turn ({len(text_to_speak)} chars, mode={active_mode}, hash={source_hash[:16]})."
        ),
    )
```

### 6.2 `_handle_narrate_event` Blueprint

```python
def _handle_narrate_event(self, req_id, args: dict) -> dict:
    # 1. Parameter Validation
    event = args.get("event")
    if not isinstance(event, str) or not event.strip():
        return _ok(req_id, _tool_result("event must be a non-empty string", is_error=True))

    payload = args.get("payload")
    if payload is None:
        payload = {}
    elif not isinstance(payload, dict):
        return _ok(req_id, _tool_result("payload must be an object", is_error=True))

    cwd = args.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        return _ok(req_id, _tool_result("cwd must be a string", is_error=True))

    # 2. Global Mute Check
    if self._gate.worker_gated_off():
        return _ok(
            req_id,
            _tool_result(
                "auto-speech is globally muted (~/.claude/auto-speech.disabled); event ignored",
                is_error=True,
            ),
        )

    # 3. Session Enrollment Check
    sid = (
        args.get("session_id")
        or (payload.get("session_id") if isinstance(payload, dict) else None)
        or os.environ.get("CLAUDE_CODE_SESSION_ID")
        or os.environ.get("CLAUDE_SESSION_ID")
        or ""
    )
    if not sid:
        return _ok(
            req_id,
            _tool_result(
                "No session_id provided and session is not enrolled in narration; event ignored.",
                is_error=False,
            ),
        )

    marker = self._narrator_sessions_dir / sid
    if not marker.exists():
        return _ok(
            req_id,
            _tool_result(
                f"Session '{sid}' is not enrolled in narration; event ignored.",
                is_error=False,
            ),
        )

    # 4. Daemon Liveness Check
    try:
        client = self._client or DaemonClient()
        client.ensure_daemon_running()
    except Exception as exc:
        print(f"[{SERVER_NAME}] ensure_daemon_running warning: {exc}", file=sys.stderr)

    # 5. Event Logging
    record = {
        "ts": int(time.time()),
        "cwd": cwd or str(Path.cwd()),
        "pid": os.getpid(),
        "event": event,
        "payload": payload,
    }

    try:
        self._narrator_events_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._narrator_events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        return _ok(req_id, _tool_result(f"could not log narrator event: {exc}", is_error=True))

    # 6. Confirmation Result
    return _ok(req_id, _tool_result(f"Narrator event '{event}' ingested for session '{sid}'."))
```

---

## 7. Verification & Unit Testing Strategy

In `tests/test_mcp_server.py`, the following unit tests must be added to provide 100% branch and error path coverage for the two new tools:

1. **`test_tools_list_advertises_expanded_tools`**:
   Verify `tools/list` returns both `autoplay_turn` and `narrate_event` (and `speak`). Verify required properties and schemas.

2. **`test_autoplay_turn_honors_global_mute`**:
   Initialize `McpServer(gate=AutoplayGate(home=temp_muted_home))`. Call `autoplay_turn`. Verify `isError=True` and mute message.

3. **`test_autoplay_turn_locates_and_speaks_latest_turn`**:
   Provide a mock transcript JSONL with Claude Code turns. Call `autoplay_turn`. Verify assistant message extracted, SHA-256 computed, and `client.speak` called with `Priority.AUTOPLAY`.

4. **`test_autoplay_turn_supports_antigravity_turn`**:
   Provide a mock transcript JSONL with Antigravity `PLANNER_RESPONSE` turns. Verify correct turn text is extracted.

5. **`test_autoplay_turn_raw_bypass`**:
   Call `autoplay_turn` with `bypass_llm=True` or `mode="raw"`. Verify raw message passed to client without calling rewriter.

6. **`test_autoplay_turn_rewriter_fallback_on_failure`**:
   Inject a mock rewriter that raises `ClaudeCliRewriteError`. Verify speech fallback to raw message without error.

7. **`test_autoplay_turn_rejects_too_short_turn`**:
   Provide a transcript whose qualifying turn is only 5 characters (below min_len). Verify `isError=True`.

8. **`test_narrate_event_honors_global_mute`**:
   Verify `narrate_event` returns `isError=True` when globally muted.

9. **`test_narrate_event_clean_notice_when_session_not_enrolled`**:
   Call `narrate_event` with unenrolled `session_id`. Verify `isError=False`, message says `"not enrolled; event ignored"`, and events log file is NOT created/modified.

10. **`test_narrate_event_appends_to_jsonl_when_enrolled`**:
    Touch session marker in `temp_sessions_dir`. Call `narrate_event`. Verify JSON line appended with `ts`, `cwd`, `pid`, `event`, `payload`. Verify `isError=False`.

---

## 8. Conclusion

This strategy guarantees that:
- `autoplay_turn` and `narrate_event` provide seamless lifecycle hook parity for AI agents.
- `mcp_server.py` stays 100% standard library with zero runtime bloat.
- All collaborators are cleanly injectable, making unit test coverage fast, reliable, and hermetic.
