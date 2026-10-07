# Specification & Assertion Suite: Auto-Speech 6-Tool MCP Server (M2)

**Document ID**: Auto-Speech-M2-MCP-Test-Spec-2026-10-06  
**Author**: `teamwork_preview_spec_miner` (`spec_miner_m2`)  
**Status**: Ratified Specification  
**Scope**: `tests/test_mcp_server.py` and `plugin/scripts/python/mcp_server.py`  

---

## 1. Executive Summary & Specification Scope

This document specifies the exhaustive behavioral contracts, JSON-RPC 2.0 schemas, edge cases, error conditions, and programmatic test assertions required to expand the `auto-speech` MCP server from a single-tool stub (`speak`) into a complete 6-tool lifecycle automation suite providing full parity with shell hooks and CLI operations:

1. **`speak`**: Direct, verbatim speech synthesis via daemon client (retained with 100% backward compatibility).
2. **`autoplay_turn`**: Automated assistant turn extraction, rewriting, and speech synthesis without requiring OS shell hooks.
3. **`narrate_event`**: Real-time lifecycle event ingestion (tool use, prompts, completions) into the background narrator pipeline.
4. **`playback_control`**: Transport management (`pause`, `resume`, `stop`, `restart`, `seek`, `replay`) via mpv IPC, daemon interrupts, and audio cache.
5. **`session_configure`**: Programmatic session enrollment, autoplay scoping (`solo` vs. `all`), and speech rewrite modes.
6. **`doctor`**: One-shot system health checks returning structured JSON or human-readable diagnostics.

All tools adhere strictly to the JSON-RPC 2.0 protocol over newline-delimited stdio, depend solely on Python standard library primitives, and enforce hermetic execution via injectable collaborators and isolated filesystem paths.

---

## 2. Authoritative Specification Sources

1. **`ORIGINAL_REQUEST.md`** (§2026-10-06T20:47:50Z):
   - R2: Full Lifecycle Hook Parity via MCP Server (`speak`, `autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`, `doctor`).
   - Global mute enforcement across all speech-producing tools (`~/.claude/auto-speech.disabled`).
   - Parity with Claude Code hooks and Antigravity lifecycle events.
2. **`PROJECT.md`**:
   - MCP Server Interface Contract (§Interface Contracts).
   - Tool schemas, arguments, return formats, and error handling.
3. **Reference Implementations**:
   - `plugin/scripts/python/cli.py`: Canonical handlers for `speak`, `replay`, `pause`, `resume`, `restart`, `stop`, `seek`, `autoplay`, `narrate`, `hook`, `doctor`.
   - `plugin/scripts/python/mcp_server.py`: Existing JSON-RPC 2.0 framing, `speak` implementation, and error code conventions.
   - `plugin/scripts/python/autoplay_worker.py`: Message selection, min-length gating, hashing, prompt resolution, and rewriter invocation.
   - `plugin/scripts/python/daemon_client.py`: Priority queue levels (`P1 USER_INTERRUPT`, `P2 EXPLICIT_MCP`, `P3 AUTOPLAY`, `P4 TOOL_NARRATION`) and socket commands.
   - `plugin/scripts/python/doctor.py` & `health_report.py`: 10-probe diagnostics, JSON serialization, and health status semantics.
   - `tests/test_mcp_server.py`: Baseline 17 unit tests verifying JSON-RPC 2.0 framing, protocol version negotiation, and `speak` mechanics.

---

## 3. Features Discovered

| # | Category | Feature | Description | Inputs | Outputs | Error Behavior | Discovered Via |
|---|----------|---------|-------------|--------|---------|----------------|----------------|
| 1 | MCP Protocol | `tools/list` Catalog | Advertises 6 tools with valid JSON schemas, parameter types, descriptions, and required fields. | JSON-RPC `tools/list` | Array of 6 tool definitions in `result.tools` | Always succeeds (`isError: false`) | `PROJECT.md`, `mcp_server.py` |
| 2 | Speech | `speak` Verbatim | Enqueues raw text for synthesis via `DaemonClient.speak` at Priority 2 (`EXPLICIT_MCP`). | `{"text": str}` (1–20,000 chars) | Confirmation string with character count; `isError: false` | Rejects non-string, blank, or >20,000 chars; honors global mute (`isError: true`) | `mcp_server.py:41` |
| 3 | Speech | `speak` Offline Fallback | Spawns detached `speak.py` worker via `spawn_say_worker` if daemon connection fails or errors. | `{"text": str}` | Queued confirmation; `isError: false` | Catches `ConnectionError` and daemon error status | `mcp_server.py:202` |
| 4 | Autoplay | `autoplay_turn` Turn Extraction | Resolves transcript JSONL via `TranscriptLocator` and extracts latest assistant message via `MessageSelector`. | `session_id`, `transcript_path`, `ordinal` (default 1) | Queued speech confirmation; `isError: false` | Raises error if transcript not found or no qualifying turn exists | `autoplay_worker.py:38`, `cli.py` |
| 5 | Autoplay | `autoplay_turn` Min Length Check | Suppresses synthesis if assistant text is shorter than threshold (20 characters). | Assistant turn text < 20 chars | Tool error indicating text too short | `isError: true` with character count | `autoplay_worker.py:8` |
| 6 | Autoplay | `autoplay_turn` Rewriting Pipeline | Invokes `ClaudeCliRewriter` with template for active `mode` and `size` (verbatim, summary small/med/large). | `mode`, `size`, `transcript_path` | Synthesizes rewritten text; `isError: false` | Falls back to raw text on `ClaudeCliUnavailable` / `ClaudeCliRewriteError` | `cli_rewrite.py:26` |
| 7 | Autoplay | `autoplay_turn` Raw Bypass | Bypasses rewriter entirely when `mode="raw"` or `bypass_llm=true`, synthesizing verbatim assistant turn. | `mode="raw"` or `bypass_llm=true` | Verbatim assistant speech; `isError: false` | Bypasses rewriter; enforces min length & mute | `explorer_survey_2:127` |
| 8 | Autoplay | `autoplay_turn` Hash Caching | Computes SHA256 source hash from raw text and passes to `DaemonClient` for cache hit lookup. | Assistant message text, voice, speed | Passes `source_hash` to `DaemonClient.speak` | Misses fall back to synthesis and auto-promotion | `autoplay_worker.py:26` |
| 9 | Narration | `narrate_event` Ingestion | Ingests lifecycle activity events (PreToolUse, PostToolUse, UserPromptSubmit, Stop) into narrator JSONL pipeline. | `event` (str, req), `payload` (dict), `session_id` (str), `cwd` (str) | Ingestion confirmation; `isError: false` | Rejects missing/blank `event` (`INVALID_PARAMS` or `isError: true`) | `cli.py:731`, `narrator_hook.sh` |
| 10 | Narration | `narrate_event` Enrollment Gate | Verifies `~/.claude/auto-speech-narrate-sessions/<session_id>` before logging. | `session_id` | If not enrolled, returns clean notice with `isError: false`; does not append to log | Clean notice; no write to file | `cli.py:722`, `narrator_service.py:732` |
| 11 | Narration | `narrate_event` Daemon Liveness | Ensures narrator daemon is running via `DaemonClient.ensure_daemon_running()`. | Valid enrolled event | Starts background daemon if stopped | Transparent background start | `cli.py:726` |
| 12 | Transport | `playback_control` Stop | Halts playback immediately, terminates `NativeAudioSink` process, and clears queue via `DaemonClient.interrupt()`. | `{"action": "stop"}` | `"Playback stopped and queue cleared."` | Sockets cleaned up; operates even if daemon idle | `cli.py:363`, `daemon_client.py:146` |
| 13 | Transport | `playback_control` Pause & Resume | Toggles playback pause state via mpv IPC socket (`["set_property", "pause", bool]`). | `{"action": "pause"}` / `{"action": "resume"}` | `"Playback paused."` / `"Playback resumed."` | If mpv socket missing: `isError: true`, `"No active playback session."` | `cli.py:283`, `mpv_ipc.py` |
| 14 | Transport | `playback_control` Restart | Rewinds current playback to start via mpv IPC socket (`["seek", 0, "absolute"]`). | `{"action": "restart"}` | `"Playback restarted."` | If mpv socket missing: `isError: true` | `cli.py:301` |
| 15 | Transport | `playback_control` Seek | Seeks playback position relative (`+N`, `-N`) or absolute (`N`, `'end'`). | `{"action": "seek", "target": str}` | `"Seek to <target> successful."` | If target missing or non-numeric: `isError: true` | `cli.py:310` |
| 16 | Transport | `playback_control` Replay | Replays cached audio by recency ordinal via `CacheStore` and `DaemonClient.play_cache`. | `{"action": "replay", "ordinal": int}` (default 1) | Replay confirmation with source hash & duration | If cache empty: `isError: true`; if muted: `isError: true` | `cli.py:228`, `cache_store.py` |
| 17 | Configuration | `session_configure` Autoplay | Enrolls (`"on"`), withdraws (`"off"`), or inspects (`"status"`) per-session autoplay marker. | `session_id`, `autoplay`: `"on"`\|`"off"`\|`"status"` | Formatted enrollment status | `"on"` unlinks global mute; `"off"` without session sets global mute | `autoplay_enrollment.py`, `cli.py:414` |
| 18 | Configuration | `session_configure` Narration | Enrolls (`"on"`), withdraws (`"off"`), or inspects (`"status"`) session narration marker. | `session_id`, `narrate`: `"on"`\|`"off"`\|`"status"` | Formatted narration status | Missing session ID on `"on"` returns error | `cli.py:519` |
| 19 | Configuration | `session_configure` Scope | Adjusts autoplay spotlight (`"solo"`, `"all"`, `"status"`). | `session_id`, `scope`: `"solo"`\|`"all"`\|`"status"` | Formatted scope report | `"solo"` requires session ID; `"all"` clears spotlight | `autoplay_scope.py`, `cli.py:485` |
| 20 | Configuration | `session_configure` Mode/Size | Configures autoplay rewrite mode and summary size in `~/.config/auto-speech/autoplay.toml`. | `mode`: `"summary"`\|`"verbatim"`\|`"raw"`, `size`: `"small"`\|`"medium"`\|`"large"` | Confirmation of updated configuration | Invalid mode or size returns validation error | `cli.py:450`, `autoplay_config.py` |
| 21 | Diagnostics | `doctor` Human Report | Runs 10-probe health check suite; returns formatted human-readable text report. | `{"as_json": false}` | Multi-line text report with header and check tags | `healthy == false` sets `isError: true` | `doctor.py:275`, `cli.py:765` |
| 22 | Diagnostics | `doctor` JSON Report | Runs health checks; returns parseable JSON report (`{"healthy": bool, "checks": [...]}`). | `{"as_json": true}` | JSON string with check details | Valid JSON parseable; `healthy == false` sets `isError: true` | `doctor.py:276`, `health_report.py:51` |
| 23 | Safety | Global Mute Enforcement | Blocks audio synthesis/playback across all speech-producing tools when `~/.claude/auto-speech.disabled` exists. | Checked in `speak`, `autoplay_turn`, `narrate_event`, `playback_control` (`replay`/`resume`) | Returns tool error explaining auto-speech is muted | `isError: true`; zero audio, zero worker spawn, zero daemon enqueues | `autoplay_gate.py:54`, `PROJECT.md` |

---

## 4. Edge Cases

| # | Feature | Input | Observed Behavior |
|---|---------|-------|-------------------|
| 1 | `speak` | `text = ""` or whitespace only | Returns `isError: true` with `"text must be a non-empty string"`. No spawner or daemon called. |
| 2 | `speak` | `text = "a" * 20001` (exceeds `MAX_CHARS`) | Returns `isError: true` with `"text exceeds 20000 characters"`. |
| 3 | `speak` | Global mute active (`~/.claude/auto-speech.disabled` present) | Returns `isError: true` with `"auto-speech is globally muted (~/.claude/auto-speech.disabled); nothing was spoken"`. |
| 4 | `speak` | Daemon raises `ConnectionError` | Transparently invokes `spawn_say_worker(text)` fallback, returns `isError: false` with character count. |
| 5 | `speak` | Daemon returns `status="error"` | Transparently invokes `spawn_say_worker(text)` fallback, returns `isError: false`. |
| 6 | `autoplay_turn` | Global mute active | Returns `isError: true` with `"auto-speech is globally muted... nothing was spoken"`. Transcript is NOT read. |
| 7 | `autoplay_turn` | Explicit `transcript_path` does not exist | Returns `isError: true` with `"transcript not found: /path/to/missing.jsonl"`. |
| 8 | `autoplay_turn` | No session ID and no matching slug in `~/.claude/projects/` | `TranscriptLocator` raises `TranscriptNotFoundError`; tool returns `isError: true` with `"no transcript found for session"`. |
| 9 | `autoplay_turn` | Transcript has only user messages or non-assistant records | `MessageSelector` raises `NoSuchAssistantTurn`; tool returns `isError: true` with `"no qualifying assistant turn found"`. |
| 10 | `autoplay_turn` | Assistant turn text has length < 20 characters (e.g. `"ok"`, `"done"`) | Tool returns `isError: true` with `"assistant turn text too short (N chars < 20 chars); nothing to speak"`. |
| 11 | `autoplay_turn` | Assistant turn text exactly 20 characters | Message qualifies; synthesized or enqueued successfully (`isError: false`). |
| 12 | `autoplay_turn` | `mode="raw"` or `bypass_llm=true` | Rewriter template loading and `ClaudeCliRewriter` are completely bypassed; raw text is forwarded directly to `DaemonClient.speak`. |
| 13 | `autoplay_turn` | Rewriter raises `ClaudeCliUnavailable` or `ClaudeCliRewriteError` | Tool catches exception and gracefully falls back to raw assistant text without failing. |
| 14 | `autoplay_turn` | Daemon offline (`ConnectionError`) | Falls back to `spawn_say_worker(text)`; returns `isError: false`. |
| 15 | `autoplay_turn` | Antigravity transcript format (`type="PLANNER_RESPONSE"`) | `MessageSelector` successfully extracts text from planner response content; tool executes cleanly. |
| 16 | `narrate_event` | Missing `event` parameter or empty string | JSON-RPC error `-32602 (INVALID_PARAMS)` or tool error `isError: true` with `"missing or invalid event"`. |
| 17 | `narrate_event` | Global mute active | Returns `isError: true` with `"auto-speech is globally muted; event not narrated"`. Log file is NOT written. |
| 18 | `narrate_event` | Session unenrolled (no marker in `~/.claude/auto-speech-narrate-sessions/`) | Returns clean notice: `"Session <id> is not enrolled in narration; event ignored."` with `isError: false`. Log file is NOT modified. |
| 19 | `narrate_event` | Session enrolled (marker exists in `~/.claude/auto-speech-narrate-sessions/`) | Calls `DaemonClient.ensure_daemon_running()`, formats JSON record, appends atomically to `/tmp/auto-speech-narrator-events.jsonl`, returns `isError: false`. |
| 20 | `narrate_event` | `session_id` inside `payload` as `conversationId` (Antigravity) | Extracts `conversationId` fallback; correctly verifies narration enrollment. |
| 21 | `playback_control` | Missing `action` or unknown action (`action="fly"`) | Returns `isError: true` with `"unknown action: fly (expected pause, resume, stop, restart, seek, replay)"`. |
| 22 | `playback_control` | `action="stop"` with mpv socket missing | Calls `DaemonClient.interrupt()`; unlinks PID file if present; returns `isError: false` with `"Playback stopped and queue cleared."`. |
| 23 | `playback_control` | `action="stop"` with mpv socket alive | Sends `["stop"]` to mpv socket, calls `DaemonClient.interrupt()`, unlinks PID file, returns `isError: false`. |
| 24 | `playback_control` | `action="pause"`, `resume`, `restart`, `seek` with mpv socket missing | Returns `isError: true` with `"No active playback session."`. |
| 25 | `playback_control` | `action="resume"` when globally muted | Returns `isError: true` with `"auto-speech is globally muted; cannot resume playback"`. |
| 26 | `playback_control` | `action="seek"` without `target` parameter | Returns `isError: true` with `"seek requires a target (+N, -N, N, or 'end')"`. |
| 27 | `playback_control` | `action="seek"` with invalid target (`target="abc"`, `target="+inf"`) | Returns `isError: true` with `"seek requires a target (+N, -N, N, or 'end')"`. |
| 28 | `playback_control` | `action="seek"` with `target="end"` | Sends `["seek", 999999, "absolute"]` over mpv socket; returns `isError: false`. |
| 29 | `playback_control` | `action="seek"` with `target="+15"` | Sends `["seek", 15.0, "relative"]` over mpv socket; returns `isError: false`. |
| 30 | `playback_control` | `action="seek"` with `target="-10"` | Sends `["seek", -10.0, "relative"]` over mpv socket; returns `isError: false`. |
| 31 | `playback_control` | `action="seek"` with `target="45"` | Sends `["seek", 45.0, "absolute"]` over mpv socket; returns `isError: false`. |
| 32 | `playback_control` | `action="replay"` when globally muted | Returns `isError: true` with `"auto-speech is globally muted; replay suppressed"`. |
| 33 | `playback_control` | `action="replay"` when cache directory is empty | Returns `isError: true` with `"No cached audio available for replay (ordinal=1)."`. |
| 34 | `playback_control` | `action="replay"` with `ordinal=3` when cache has only 1 entry | Returns `isError: true` with `"No cached audio available for replay (ordinal=3)."`. |
| 35 | `playback_control` | `action="replay"` with cached entry present | Finds entry at index `ordinal - 1`, calls `DaemonClient.play_cache(entry.source_hash)`, returns `isError: false`. |
| 36 | `session_configure` | `autoplay="on"` without session ID | Returns `isError: true` with `"autoplay on requires a session_id"`. |
| 37 | `session_configure` | `autoplay="on"` with session ID | Removes global mute marker `auto-speech.disabled`, creates marker `~/.claude/auto-speech-autoplay-enabled/<session_id>`, returns `isError: false`. |
| 38 | `session_configure` | `autoplay="off"` with session ID | Removes marker `~/.claude/auto-speech-autoplay-enabled/<session_id>`, returns `isError: false`. |
| 39 | `session_configure` | `autoplay="off"` without session ID | Touches global mute marker `~/.claude/auto-speech.disabled`, returns `isError: false` noting global mute set. |
| 40 | `session_configure` | `autoplay="status"` | Inspects enrollment and solo scope for session, returns formatted status string with `isError: false`. |
| 41 | `session_configure` | `narrate="on"` with session ID | Creates marker in `~/.claude/auto-speech-narrate-sessions/<session_id>`, calls `ensure_daemon_running()`, returns `isError: false`. |
| 42 | `session_configure` | `narrate="off"` with session ID | Removes marker from `~/.claude/auto-speech-narrate-sessions/<session_id>`, returns `isError: false`. |
| 43 | `session_configure` | `narrate="status"` | Checks if session marker exists and reports total enrolled sessions count. |
| 44 | `session_configure` | `scope="solo"` without session ID | Returns `isError: true` with `"scope solo requires a session_id"`. |
| 45 | `session_configure` | `scope="solo"` with session ID | Writes session ID to `~/.claude/auto-speech-autoplay-solo`, enrolls session, returns `isError: false`. |
| 46 | `session_configure` | `scope="all"` | Unlinks `~/.claude/auto-speech-autoplay-solo`, returns `isError: false` confirming all enrolled sessions play. |
| 47 | `session_configure` | `mode="summary"`, `size="large"` | Writes updated settings to `~/.config/auto-speech/autoplay.toml`, returns `isError: false`. |
| 48 | `doctor` | `as_json=false` on healthy system | Returns text report starting with `"auto-speech doctor — HEALTHY"`, `isError: false`. |
| 49 | `doctor` | `as_json=true` on healthy system | Returns valid JSON with `"healthy": true` and checks array, `isError: false`. |
| 50 | `doctor` | `as_json=false` or `true` on unhealthy system (e.g. disk full or mpv missing) | Text or JSON reports `"HEALTHY": false` / `"UNHEALTHY"`, and tool returns `isError: true`. |

---

## 5. Exhaustive Tool-by-Tool Specification

### 5.1 Tool Catalog & `tools/list` Schema Specification

The `tools/list` response MUST return JSON-RPC 2.0 with a `result.tools` array containing exactly 6 tool definitions:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "tools": [
      {
        "name": "speak",
        "title": "Speak text aloud (local text-to-speech)",
        "description": "Speak text aloud: say, read out loud, voice, narrate, or vocalize the given words as audio on this Mac's speakers. Local text-to-speech (TTS) with Apple neural voice — no cloud service, no API key. The text is spoken verbatim, so write it the way it should sound (no markdown, code, or tables). Returns immediately; speech is queued behind anything already playing.",
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
      },
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
      },
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
      },
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
      },
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
      },
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
    ]
  }
}
```

---

## 6. Hermetic Test Harness & Assertion Design

To satisfy Requirement 8 (Hermetic design) and maintain 100% test isolation without touching real audio hardware, real daemons, or user dotfiles:

### 6.1 Test Harness Setup (`_server`)

```python
class McpTestContext:
    def __init__(self, muted: bool = False, tmp_root: Path | None = None):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="auto-speech-mcp-test-"))
        self.home = self.tmp_dir / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.claude_dir = self.home / ".claude"
        self.claude_dir.mkdir(parents=True, exist_ok=True)
        self.narrate_marker_dir = self.claude_dir / "auto-speech-narrate-sessions"
        self.narrate_marker_dir.mkdir(parents=True, exist_ok=True)
        self.autoplay_marker_dir = self.claude_dir / "auto-speech-autoplay-enabled"
        self.autoplay_marker_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir = self.home / ".config" / "auto-speech"
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.tmp = self.tmp_dir / "tmp"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.events_log = self.tmp / "auto-speech-narrator-events.jsonl"
        self.mpv_sock = self.tmp / "auto-speech-mpv.sock"
        self.mpv_pid = self.tmp / "auto-speech-mpv.pid"

        if muted:
            (self.claude_dir / "auto-speech.disabled").touch()

        self.spoken: list[str] = []
        self.mock_client = MagicMock()
        self.mock_client.speak.return_value = DaemonResponse(status="ok", action="speak")
        self.mock_client.play_cache.return_value = DaemonResponse(status="ok", action="play_cache")
        self.mock_client.interrupt.return_value = DaemonResponse(status="ok", action="interrupt")
        self.mock_client.is_alive.return_value = True
        self.mock_client.ensure_daemon_running.return_value = True

        self.gate = AutoplayGate(home=self.home)
        self.enrollment = SessionEnrollment(home=self.home)
        self.scope = SoloScope(home=self.home)
        
        # McpServer instantiation with injected home and paths
        self.server = McpServer(
            client=self.mock_client,
            gate=self.gate,
            home=self.home,
            events_log=self.events_log,
            mpv_sock=self.mpv_sock,
            mpv_pid=self.mpv_pid,
        )

    def cleanup(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
```

---

## 7. Concrete Test Case Catalog for `tests/test_mcp_server.py`

Below is the concrete list of test functions to be integrated into `tests/test_mcp_server.py`:

### Group 1: Protocol & Advertisement (4 tests)
- `test_initialize_echoes_supported_protocol()`: Verifies supported protocol negotiation.
- `test_initialize_unknown_protocol_falls_back_to_latest()`: Verifies fallback to `2025-06-18`.
- `test_notification_gets_no_response()`: Verifies `notifications/initialized` produces no output.
- `test_tools_list_advertises_all_six_tools()`: Verifies exact 6 tool names, schemas, properties, and required arrays (`["speak", "autoplay_turn", "narrate_event", "playback_control", "session_configure", "doctor"]`).

### Group 2: Backward Compatibility of `speak` (12 tests)
- `test_speak_queues_text_verbatim()`: Verifies text queued via `client.speak`.
- `test_speak_rejects_bad_text()`: Tests `{}`, `{"text": ""}`, `{"text": "  \n"}`, `{"text": 42}`, `{"text": None}`.
- `test_speak_rejects_oversize_text()`: Verifies `MAX_CHARS + 1` rejected, `MAX_CHARS` accepted.
- `test_speak_honors_global_mute()`: Verifies global mute returns `isError: true`.
- `test_speak_invokes_daemon_client_directly()`: Verifies `client.speak(text, priority=Priority.EXPLICIT_MCP)`.
- `test_speak_falls_back_to_spawn_say_worker_on_connection_error()`: Catches `ConnectionError` and spawns say worker.
- `test_speak_falls_back_to_spawn_say_worker_on_daemon_error_status()`: Status `"error"` triggers worker fallback.
- `test_speak_spawn_failure_is_tool_error()`: Worker launch failure returns tool error.
- `test_protocol_errors()`: Verifies -32700, -32600, -32601, -32602 JSON-RPC errors.
- `test_response_messages_are_ignored()`: Ignores foreign responses.
- `test_serve_writes_one_line_per_request()`: Verifies stdio line-delimited dispatch.
- `test_plugin_mcp_manifest_points_at_the_launcher()`: Verifies managed plugin launcher manifest.

### Group 3: `autoplay_turn` Test Suite (8 tests)
- `test_autoplay_turn_happy_path()`:
  - Mocks `MessageSelector.select` returning `AssistantMessage(text="Here is the comprehensive report for the build.")`.
  - Asserts `client.speak.called` with `priority=Priority.AUTOPLAY`, `source_hash` present, and returned `isError: false`.
- `test_autoplay_turn_honors_global_mute()`:
  - Mutes via `home / .claude / auto-speech.disabled`.
  - Asserts response `isError: true`, message contains `"globally muted"`, and `client.speak.call_count == 0`.
- `test_autoplay_turn_missing_transcript_returns_tool_error()`:
  - Provides non-existent path `transcript_path="/tmp/nonexistent.jsonl"`.
  - Asserts `isError: true` with `"transcript not found"`.
- `test_autoplay_turn_no_qualifying_turn_returns_tool_error()`:
  - Synthetic transcript with user messages only.
  - Asserts `isError: true` with `"no qualifying assistant turn found"`.
- `test_autoplay_turn_text_below_min_len_returns_tool_error()`:
  - Assistant turn text is `"short response"` (< 20 chars).
  - Asserts `isError: true` with `"too short"`, `client.speak.call_count == 0`.
- `test_autoplay_turn_mode_raw_bypasses_rewriter()`:
  - Calls with `mode="raw"`.
  - With mock rewriter, asserts rewriter is NEVER called, and raw text is sent to `client.speak`.
- `test_autoplay_turn_bypass_llm_flag_bypasses_rewriter()`:
  - Calls with `bypass_llm=True`.
  - Asserts rewriter is NEVER called, and raw text is sent to `client.speak`.
- `test_autoplay_turn_connection_error_falls_back_cleanly()`:
  - `client.speak` raises `ConnectionError`.
  - Asserts fallback to `spawn_say_worker` and returns `isError: false`.

### Group 4: `narrate_event` Test Suite (6 tests)
- `test_narrate_event_missing_event_returns_parameter_error()`:
  - Arguments without `event` or with empty string.
  - Asserts `isError: true` or `INVALID_PARAMS`.
- `test_narrate_event_honors_global_mute()`:
  - Global mute active.
  - Asserts `isError: true` with `"globally muted"`, no file created.
- `test_narrate_event_unenrolled_session_returns_clean_notice()`:
  - Session ID provided, but marker does NOT exist in `marker_dir`.
  - Asserts `isError: false`, text contains `"not enrolled"`, and events log file is NOT written.
- `test_narrate_event_enrolled_session_appends_jsonl_and_ensures_daemon()`:
  - Touch marker `marker_dir / "sess-123"`.
  - Call `narrate_event` with `event="PreToolUse"`, `session_id="sess-123"`, `payload={"tool_name": "Bash"}`.
  - Asserts `client.ensure_daemon_running.called`.
  - Asserts `events_log.read_text()` contains valid JSON line with `ts`, `event="PreToolUse"`, `session_id="sess-123"`.
  - Asserts `isError: false`.
- `test_narrate_event_antigravity_conversation_id_resolution()`:
  - Event payload has `{"conversationId": "ag-conv-99"}` without top-level `session_id`.
  - Touch marker `marker_dir / "ag-conv-99"`.
  - Asserts event enrolled, written, and returns `isError: false`.
- `test_narrate_event_default_cwd_fallback()`:
  - Omit `cwd`; asserts recorded record populates valid current working directory.

### Group 5: `playback_control` Test Suite (8 tests)
- `test_playback_control_invalid_action_returns_error()`:
  - Call with `action="jump"`.
  - Asserts `isError: true` with `"unknown action"`.
- `test_playback_control_stop_invokes_interrupt()`:
  - Call with `action="stop"`.
  - Asserts `client.interrupt.called`.
  - Asserts `isError: false`, message `"Playback stopped and queue cleared."`.
- `test_playback_control_pause_resume_restart_seek_when_socket_missing()`:
  - For actions in `("pause", "resume", "restart", "seek")`:
  - Socket missing.
  - Asserts `isError: true`, message contains `"No active playback session"`.
- `test_playback_control_pause_resume_restart_when_socket_alive()`:
  - Socket stubbed with mock `MpvIpc.send`.
  - `action="pause"` sends `["set_property", "pause", True]`.
  - `action="resume"` sends `["set_property", "pause", False]`.
  - `action="restart"` sends `["seek", 0, "absolute"]`.
  - Asserts all return `isError: false`.
- `test_playback_control_resume_honors_global_mute()`:
  - Global mute active.
  - `action="resume"` returns `isError: true` with `"globally muted"`.
- `test_playback_control_seek_targets()`:
  - Target `"+10"` sends `["seek", 10.0, "relative"]`.
  - Target `"-5"` sends `["seek", -5.0, "relative"]`.
  - Target `"60"` sends `["seek", 60.0, "absolute"]`.
  - Target `"end"` sends `["seek", 999999, "absolute"]`.
  - Asserts all return `isError: false`.
- `test_playback_control_seek_invalid_target_returns_error()`:
  - Targets `""`, `"bad"`, `"+inf"`.
  - Asserts `isError: true` with `"seek requires a target"`.
- `test_playback_control_replay_cached_audio()`:
  - Cache store stubbed with entry `source_hash="abcd1234" * 8`.
  - `action="replay"`, `ordinal=1`.
  - Asserts `client.play_cache.called` with `"abcd1234" * 8`.
  - Asserts `isError: false`.
  - With empty cache, asserts `isError: true` with `"No cached audio available"`.
  - With global mute active, asserts `isError: true` with `"globally muted"`.

### Group 6: `session_configure` Test Suite (6 tests)
- `test_session_configure_autoplay_on_and_off()`:
  - `autoplay="on"`, `session_id="sess-1"`.
  - Asserts marker `~/.claude/auto-speech-autoplay-enabled/sess-1` exists.
  - `autoplay="off"`, `session_id="sess-1"`.
  - Asserts marker unlinked.
- `test_session_configure_autoplay_off_without_session_sets_global_mute()`:
  - `autoplay="off"`, no session ID.
  - Asserts marker `~/.claude/auto-speech.disabled` touched.
- `test_session_configure_narrate_on_and_off()`:
  - `narrate="on"`, `session_id="sess-2"`.
  - Asserts marker `~/.claude/auto-speech-narrate-sessions/sess-2` exists and `client.ensure_daemon_running.called`.
  - `narrate="off"`, `session_id="sess-2"`.
  - Asserts marker unlinked.
- `test_session_configure_scope_solo_and_all()`:
  - `scope="solo"`, `session_id="sess-3"`.
  - Asserts `~/.claude/auto-speech-autoplay-solo` contains `"sess-3"`.
  - `scope="all"`.
  - Asserts `~/.claude/auto-speech-autoplay-solo` unlinked.
- `test_session_configure_scope_solo_without_session_returns_error()`:
  - `scope="solo"`, no session ID.
  - Asserts `isError: true`.
- `test_session_configure_mode_and_size()`:
  - `mode="summary"`, `size="large"`.
  - Asserts `~/.config/auto-speech/autoplay.toml` updated with `mode = "summary"`, `summary_size = "large"`.

### Group 7: `doctor` Test Suite (3 tests)
- `test_doctor_text_report_healthy()`:
  - Call with `as_json=false` on healthy mocked system.
  - Asserts output text contains `"auto-speech doctor — HEALTHY"`.
  - Asserts `isError: false`.
- `test_doctor_json_report_healthy()`:
  - Call with `as_json=true`.
  - Asserts output parses cleanly as JSON.
  - Asserts `json_data["healthy"] is True` and `isinstance(json_data["checks"], list)`.
  - Asserts `isError: false`.
- `test_doctor_unhealthy_sets_is_error_true()`:
  - Mock Doctor returning `healthy=False` (e.g. `Check(name="disk", status=Status.FAIL, detail="low disk")`).
  - Asserts `isError: true` for both text and JSON modes.

---

## 8. Verification Strategy & Exit Gate

1. **Test Runner Invocation**:
   ```bash
   .venv/bin/python tests/test_mcp_server.py
   ```
   Must execute all 47 test functions and print:
   ```
   mcp_server: 47 tests passed
   ```
2. **Whole Suite Run**:
   ```bash
   bash tests/run_all.sh
   ```
   Must pass all 42+ test suites with 0 failures.
3. **Lint & Style Compliance**:
   ```bash
   .venv/bin/ruff check tests/test_mcp_server.py plugin/scripts/python/mcp_server.py
   ```
   Must return 0 violations.
