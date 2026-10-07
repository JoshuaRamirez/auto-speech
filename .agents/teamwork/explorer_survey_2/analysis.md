# Analysis: R2 Full Lifecycle Hook Parity via MCP Server

**Survey Date**: 2026-10-06  
**Investigator**: `explorer_survey_2` (Teamwork Explorer)  
**Objective**: Comprehensive technical survey and architectural design for R2: Full Lifecycle Hook Parity via MCP Server in `/Users/joshua/Developer/auto-speech`.

---

## Executive Summary

The `auto-speech` Model Context Protocol (MCP) server currently exposes a single tool: `speak(text: str)`, which queues verbatim speech to the unified daemon. However, extensive lifecycle automation (end-of-turn autoplay, tool event narration, playback transport controls, session enrollment, solo scoping, and health checks) is currently split between shell hooks (`autoplay_hook.sh`, `narrator_hook.sh`), the system multiplexer (`agent-event-router`), and CLI slash commands.

To satisfy **Requirement R2** of the unified CLI and MCP milestone, the MCP server (`plugin/scripts/python/mcp_server.py`) must be extended into a 6-tool suite providing complete lifecycle hook parity:
1. `speak`: Verbatim speech synthesis (existing, retained).
2. `autoplay_turn`: Process and synthesize the latest turn response for a session or transcript using the active rewrite mode (summary-small/medium/large or verbatim) without requiring OS shell hooks.
3. `narrate_event`: Ingest activity lifecycle events (PreToolUse, PostToolUse, UserPromptSubmit, Stop) into the real-time narrator pipeline while respecting session enrollment.
4. `playback_control`: Manage transport operations (pause, resume, stop, restart, seek, replay).
5. `session_configure`: Configure session enrollment markers, solo vs. all scoping, and autoplay rewrite modes.
6. `doctor`: Run system health checks and return structured diagnostic findings.

All speech-producing tools must strictly enforce the global mute check (`~/.claude/auto-speech.disabled`), route through `DaemonClient` with priority arbitration, and remain 100% hermetic (standard library only) to preserve compatibility across the 42 unit/regression test suites.

---

## 1. Current MCP Server Architecture (`mcp_server.py`)

### 1.1 Transport and Protocol Handling
- **Transport**: Standard I/O (`sys.stdin`, `sys.stdout`), newline-delimited JSON-RPC 2.0.
- **Server Metadata**:
  - `SERVER_NAME = "auto-speech"`
  - `SERVER_VERSION = "0.2.0"`
  - `SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")`
- **Dependency Envelope**: Pure Python standard library (`json`, `subprocess`, `sys`, `pathlib`, `time`, `os`). Does not import MLX, torch, or heavy third-party packages. Runs on any bare Python 3.10+ interpreter.
- **Dispatch Flow**:
  - `serve()` iterates over `sys.stdin` lines, delegating to `McpServer.handle_line(line)`.
  - JSON-RPC requests (`initialize`, `ping`, `tools/list`, `tools/call`) return framed `{"jsonrpc": "2.0", "id": req_id, "result": ...}`.
  - Notifications (e.g. `notifications/initialized`) return `None` (silent).
  - Protocol errors emit standard JSON-RPC 2.0 error envelopes:
    - `-32700`: Parse error
    - `-32600`: Invalid request
    - `-32601`: Method not found
    - `-32602`: Invalid params

### 1.2 Existing `speak` Tool
- **Schema**:
  ```json
  {
    "name": "speak",
    "title": "Speak text aloud (local text-to-speech)",
    "description": "Speak text aloud: say, read out loud, voice, narrate, or vocalize the given words...",
    "inputSchema": {
      "type": "object",
      "properties": {
        "text": {
          "type": "string",
          "description": "The words to say (1-20000 characters)."
        }
      },
      "required": ["text"],
      "additionalProperties": false
    }
  }
  ```
- **Execution Flow**:
  1. Validates text: non-empty string, length <= 20,000 chars.
  2. Checks global mute: `self._gate.worker_gated_off()`. If `True`, returns tool error: `"auto-speech is globally muted (~/.claude/auto-speech.disabled); nothing was spoken"`.
  3. Dispatches via `DaemonClient`:
     `client.speak(text, priority=Priority.EXPLICIT_MCP)` (Priority 2).
  4. Offline fallback: If `ConnectionError`, invokes `spawn_say_worker(text)` which launches `speak.py` detached.
  5. Returns tool result: `{"content": [{"type": "text", "text": "Queued N characters to speak."}], "isError": false}`.

---

## 2. The 6-Tool MCP Suite Specification (R2)

To enable agents (in Claude Code, Antigravity, or custom swarms) to execute full speech lifecycle actions without relying on shell hooks, `tools/list` must advertise the following 6 tools:

| Tool Name | Purpose | Speech Priority | Primary Collaborators |
| :--- | :--- | :--- | :--- |
| `speak` | Verbatim text playback | P2 (Explicit MCP) | `DaemonClient`, `spawn_say_worker` |
| `autoplay_turn` | Synthesize turn response per active rewrite mode | P3 (Autoplay) | `TranscriptLocator`, `MessageSelector`, `ClaudeCliRewriter`, `DaemonClient` |
| `narrate_event` | Ingest real-time activity events into narrator pipeline | P4 (Narration) | `~/.claude/auto-speech-narrate-sessions/`, `/tmp/auto-speech-narrator-events.jsonl` |
| `playback_control` | Audio transport (pause, resume, stop, restart, seek, replay) | P1 / P2 | `DaemonClient.interrupt`, `MpvIpc`, `CacheStore`, `NativeAudioSink` |
| `session_configure` | Enrollment markers, solo scoping, autoplay modes | N/A (State) | `SessionEnrollment`, `SoloScope`, `autoplay_config` |
| `doctor` | Diagnostics and health probe | N/A (Diagnostics) | `Doctor`, `HealthReport` |

---

## 3. Tool Details & Interaction Chains

### 3.1 `autoplay_turn`
**Mission**: Eliminate reliance on `autoplay_hook.sh` and the background double-fork worker. Allows the agent (or orchestrator) to command the speech system to vocalize the latest response turn directly.

#### Input Schema:
```json
{
  "name": "autoplay_turn",
  "title": "Synthesize and speak latest turn response",
  "description": "Extracts the latest assistant turn for a session, rewrites/summarizes it per the active autoplay mode (verbatim, summary-small, summary-medium, summary-large, or raw), and queues audio playback without requiring OS shell hooks.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "session_id": {
        "type": "string",
        "description": "Optional session ID. If omitted, resolved from environment or newest session."
      },
      "transcript_path": {
        "type": "string",
        "description": "Optional explicit path to the session transcript JSONL."
      },
      "mode": {
        "type": "string",
        "enum": ["summary", "verbatim", "raw"],
        "description": "Optional override for autoplay mode (defaults to configured mode)."
      },
      "size": {
        "type": "string",
        "enum": ["small", "medium", "large"],
        "description": "Optional override for summary size (defaults to configured size)."
      },
      "ordinal": {
        "type": "integer",
        "default": 1,
        "description": "Nth-most-recent assistant turn to extract (default 1 = latest)."
      },
      "bypass_llm": {
        "type": "boolean",
        "description": "If true, bypasses the rewriting LLM and speaks the raw assistant text."
      }
    },
    "additionalProperties": false
  }
}
```

#### Detailed Execution Chain:
1. **Global Mute Check**: Verify `not self._gate.worker_gated_off()`. If muted, return tool error immediately.
2. **Transcript Resolution**:
   - If `transcript_path` is given and exists, use it.
   - Otherwise, resolve via `TranscriptLocator().locate(cwd=Path.cwd(), session_id=session_id)`.
   - If not found, return `isError: True` with informative message.
3. **Turn Extraction**:
   - Call `MessageSelector().select(transcript_path, n=ordinal)`.
   - Supports both Claude Code format (`type == "assistant"`, `role == "assistant"`) and Antigravity format (`type == "PLANNER_RESPONSE"`).
   - If message length < `min_len` (default 20 bytes), return error/notice.
4. **Cache Key Calculation**:
   - Calculate source hash:
     ```python
     key_input = msg.text.encode("utf-8") + b"\x00" + f"{voice_id}:{speed}".encode("utf-8")
     source_hash = hashlib.sha256(key_input).hexdigest()
     ```
5. **Mode & Rewriting Pipeline**:
   - Load config via `autoplay_config.load_config()`.
   - Apply argument overrides (`mode`, `size`, `bypass_llm`).
   - If `mode == "raw"` or `bypass_llm`:
     `text_to_speak = msg.text`
   - Else:
     - Resolve prompt: `prompt_path = _resolve_prompt(mode, size)` (e.g. `audio_summary_small_prompt.txt`).
     - Load template: `template = Path(prompt_path).read_text(encoding="utf-8")`.
     - Invoke `ClaudeCliRewriter(template).rewrite(msg.text, timeout_seconds=90)`.
     - Fallback: If `ClaudeCliUnavailable` or `ClaudeCliRewriteError`, fall back gracefully to `msg.text`.
6. **Daemon Enqueue**:
   - Invoke `DaemonClient().speak(text_to_speak, priority=Priority.AUTOPLAY, source_hash=source_hash, session_id=session_id)`.
   - Note: The daemon automatically checks `CacheStore.lookup(source_hash)` on arrival. On hit, it plays pre-synthesized `full.wav` instantly without running Kokoro synthesis.
   - If daemon offline (`ConnectionError`), fallback to `spawn_say_worker` or `speak.py`.
7. **Result**: Return character count, mode, size, and source hash.

---

### 3.2 `narrate_event`
**Mission**: Replaces `narrator_hook.sh` for real-time background narration. Enables an agent or orchestrator to stream lifecycle milestones directly into the narrator service.

#### Input Schema:
```json
{
  "name": "narrate_event",
  "title": "Ingest activity event for real-time narration",
  "description": "Ingests a lifecycle activity event (PreToolUse, PostToolUse, UserPromptSubmit, Stop, TaskProgress) into the auto-speech narrator pipeline for background narration. Honors session narration enrollment.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "event": {
        "type": "string",
        "description": "The event type (e.g., 'PreToolUse', 'PostToolUse', 'UserPromptSubmit', 'Stop')."
      },
      "payload": {
        "type": "object",
        "description": "Event metadata dictionary (tool_name, tool_input, session_id, etc.)."
      },
      "session_id": {
        "type": "string",
        "description": "Session ID (if not provided inside payload)."
      },
      "cwd": {
        "type": "string",
        "description": "Working directory for the event."
      }
    },
    "required": ["event"],
    "additionalProperties": false
  }
}
```

#### Detailed Execution Chain:
1. **Global Mute Check**: Verify `not self._gate.worker_gated_off()`.
2. **Session Enrollment Check**:
   - Extract `session_id` from argument, payload, or environment (`CLAUDE_CODE_SESSION_ID`).
   - In accordance with `narrator_hook.sh` and `narrator_service.py` line 732:
     Check `~/.claude/auto-speech-narrate-sessions/<session_id>`.
   - If marker does not exist: return result `"Session <session_id> is not enrolled in narration; event ignored."`
3. **Daemon Liveness**:
   - Invoke `DaemonClient().ensure_daemon_running()`. If daemon is idle or stopped, this launches `narrator_service_start.sh` in the background.
4. **Event Record Creation**:
   - Format JSON line:
     ```json
     {
       "ts": 1775510000,
       "cwd": "/path/to/cwd",
       "pid": 12345,
       "event": "PreToolUse",
       "payload": {"session_id": "...", "tool_name": "...", ...}
     }
     ```
5. **Atomic Write to Pipeline**:
   - Append line to `/tmp/auto-speech-narrator-events.jsonl`.
   - `narrator_service.py` tails this file with offset tracking, passes records to `NarratorPhaseClassifier`, flushes phases upon tool completion or silence timeout, and enqueues summaries at Priority 4 (Tool Narration).
6. **Result**: Return confirmation of event ingestion.

---

### 3.3 `playback_control`
**Mission**: Consolidates transport operations and audio replay into an authoritative MCP interface.

#### Input Schema:
```json
{
  "name": "playback_control",
  "title": "Manage audio playback transport and replay",
  "description": "Controls audio playback: pause, resume, stop (halt and purge queue), restart, seek, or replay recent cached audio.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["pause", "resume", "stop", "restart", "seek", "replay"],
        "description": "The transport action to execute."
      },
      "target": {
        "type": "string",
        "description": "Target position for seek (e.g. '+15', '-10', '0', 'end'). Required for seek."
      },
      "ordinal": {
        "type": "integer",
        "default": 1,
        "description": "Recency index for replay (1 = most recent cached speech)."
      }
    },
    "required": ["action"],
    "additionalProperties": false
  }
}
```

#### Detailed Execution Chain:
1. **Mute Check**: If `action in ("replay", "resume")`, verify `not self._gate.worker_gated_off()`.
2. **Action Handling**:
   - **`stop`**:
     - Call `client.interrupt()` (P1 Barge-in).
     - Daemon transitions engine state to `INTERRUPTED`, terminates active `NativeAudioSink` process via `proc.terminate()`, and purges lower-priority queue items.
     - Unlinks `/tmp/auto-speech-mpv.pid`.
     - Returns `"Playback stopped and queue cleared."`
   - **`pause` / `resume` / `restart` / `seek`**:
     - Check if `/tmp/auto-speech-mpv.sock` exists:
       - If not: return `"No active playback session."`
       - If yes: send JSON command via `MpvIpc.send(cmd, socket_path)`:
         - `pause`: `["set_property", "pause", True]`
         - `resume`: `["set_property", "pause", False]`
         - `restart`: `["seek", 0, "absolute"]`
         - `seek`: `["seek", target, "relative" if ("+" in target or "-" in target) else "absolute"]`
   - **`replay`**:
     - Look up cache entries via `CacheStore(default_cache_root()).list_by_recency()`.
     - Select entry at `ordinal - 1`.
     - Call `client.play_cache(entry.source_hash)` (or fallback to `NativeAudioSink().play(wav_path)`).
     - Returns `"Replaying cached audio (ordinal=N, duration=X.Xs)."`

---

### 3.4 `session_configure`
**Mission**: Programmatic configuration of session markers and autoplay behavior without manual filesystem manipulation or shell commands.

#### Input Schema:
```json
{
  "name": "session_configure",
  "title": "Configure session enrollment, mode, and scoping",
  "description": "Updates session enrollment for autoplay and narration, sets autoplay rewriting modes, and adjusts solo vs all scoping.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "session_id": {
        "type": "string",
        "description": "Target session ID. Defaults to current session if unset."
      },
      "autoplay": {
        "type": "string",
        "enum": ["on", "off", "status"],
        "description": "Enable, disable, or query autoplay enrollment for this session."
      },
      "narrate": {
        "type": "string",
        "enum": ["on", "off", "status"],
        "description": "Enable, disable, or query narration enrollment for this session."
      },
      "mode": {
        "type": "string",
        "enum": ["summary", "verbatim", "raw"],
        "description": "Autoplay speech rewrite mode."
      },
      "size": {
        "type": "string",
        "enum": ["small", "medium", "large"],
        "description": "Autoplay summary length."
      },
      "scope": {
        "type": "string",
        "enum": ["all", "solo", "status"],
        "description": "Autoplay scope: 'solo' (only this session speaks) or 'all' (every enrolled session speaks)."
      }
    },
    "additionalProperties": false
  }
}
```

#### Detailed Execution Chain:
1. **Session ID Resolution**: Uses provided `session_id` or falls back to `CLAUDE_CODE_SESSION_ID`.
2. **Autoplay Enrollment**:
   - Uses `SessionEnrollment` (`plugin/scripts/python/autoplay_enrollment.py`).
   - `"on"`: `enroll(session_id)` touches `~/.claude/auto-speech-autoplay-enabled/<session_id>`.
   - `"off"`: `withdraw(session_id)` deletes the marker.
3. **Narration Enrollment**:
   - Marker directory: `~/.claude/auto-speech-narrate-sessions/`.
   - `"on"`: creates `~/.claude/auto-speech-narrate-sessions/<session_id>`.
   - `"off"`: unlinks `~/.claude/auto-speech-narrate-sessions/<session_id>`.
4. **Autoplay Mode & Size**:
   - Modifies or queries user configuration at `~/.config/auto-speech/autoplay.toml`.
5. **Autoplay Scoping**:
   - Uses `SoloScope` (`plugin/scripts/python/autoplay_scope.py`).
   - `"solo"`: `set_solo(session_id)` writes `~/.claude/auto-speech-autoplay-solo`.
   - `"all"`: `clear()` removes the marker.
6. **Result**: Returns a comprehensive status report reflecting current settings.

---

### 3.5 `doctor`
**Mission**: Replaces `/auto-speech-doctor` CLI execution with structured MCP diagnostic output.

#### Input Schema:
```json
{
  "name": "doctor",
  "title": "Run system health checks and diagnostics",
  "description": "Runs comprehensive health checks across binaries, Python virtual environment, disk headroom, logs, daemon status, queue depths, scopes, and configuration.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "as_json": {
        "type": "boolean",
        "default": false,
        "description": "If true, returns structured JSON output. If false, returns human-readable text report."
      }
    },
    "additionalProperties": false
  }
}
```

#### Detailed Execution Chain:
1. Instantiate `Doctor` from `doctor.py`.
2. Call `report = doctor.run()`.
3. Checks performed:
   - `mpv` binary on PATH
   - `uv` binary on PATH
   - `jq` binary on PATH
   - Virtual environment interpreter executable
   - `/tmp` disk free space (> 50 MiB fail, > 200 MiB warn)
   - Log files below maximum cap (10 MiB)
   - Narrator daemon PID active and valid
   - Queue depth under limit (32)
   - Autoplay global mute and session scope
   - MCP server registration and executable script
   - Configuration file validity (`validate_user_configs`)
   - `uv.lock` synchronization state
4. Return `report.to_json()` or `report.to_text()`.
   If `not report.healthy`, set `isError: True`.

---

## 4. Subsystem Interactions & Invariants

### 4.1 Global Mute Invariant
Every speech-producing tool MUST check the global mute marker before initiating synthesis or audio playback:
```python
if self._gate.worker_gated_off():
    return _tool_result(
        "auto-speech is globally muted (~/.claude/auto-speech.disabled); nothing was spoken",
        is_error=True,
    )
```
- Speech-producing tools: `speak`, `autoplay_turn`, `narrate_event`, `playback_control` (`replay` and `resume` actions).
- Non-speech operations: `playback_control` (`stop`, `pause`), `session_configure`, `doctor` execute normally even when globally muted.

### 4.2 Priority Queue Alignment in `narrator_service.py`
Calls arriving via `DaemonClient` are tagged with explicit priority levels:
- `P1 (USER_INTERRUPT)`: `playback_control` with `action="stop"` or `client.interrupt()`.
- `P2 (EXPLICIT_MCP)`: `speak` tool.
- `P3 (AUTOPLAY)`: `autoplay_turn` tool.
- `P4 (TOOL_NARRATION)`: Narrator phase completions triggered by `narrate_event`.

### 4.3 Closed-Loop CacheStore Integration
`DaemonClient.speak` accepts `source_hash`.
In `autoplay_turn`:
- Source hash is computed from raw assistant text before rewriting.
- `client.speak(text_to_speak, priority=Priority.AUTOPLAY, source_hash=source_hash)` sends the hash to the daemon.
- The daemon checks `CacheStore.lookup(source_hash)`:
  - If a cached `full.wav` exists, playback begins in < 5 ms without invoking TTS.
  - If a cache miss occurs, Kokoro synthesizes `text_to_speak`, promotes it via `CacheStore.promote()`, and then plays it.

---

## 5. Verification Strategy & Test Coverage Plan

To ensure zero regressions across the existing 42 test suites and maintain 100% hermetic isolation:

1. **`tests/test_mcp_server.py`**:
   - Update `test_tools_list` to assert all 6 tools are advertised.
   - Add unit tests for each new tool:
     - `test_autoplay_turn_extracts_and_speaks` (with stubbed `TranscriptLocator` and `MessageSelector`).
     - `test_autoplay_turn_honors_global_mute`.
     - `test_autoplay_turn_raw_bypass`.
     - `test_narrate_event_logs_to_file`.
     - `test_narrate_event_enforces_session_enrollment`.
     - `test_playback_control_stop_invokes_interrupt`.
     - `test_playback_control_replay_plays_cache`.
     - `test_playback_control_pause_resume`.
     - `test_session_configure_autoplay_and_narrate`.
     - `test_session_configure_solo_scope`.
     - `test_doctor_returns_report`.
2. **Runner Verification**:
   - `bash tests/run_all.sh` (all 42 test suites green).
   - `.venv/bin/ruff check .` (0 lint violations).
