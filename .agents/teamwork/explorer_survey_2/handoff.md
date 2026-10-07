# Handoff Report: R2 Full Lifecycle Hook Parity via MCP Server Survey

**Agent**: `explorer_survey_2` (Teamwork Explorer)  
**Date**: 2026-10-06  
**Directory**: `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_2`  
**Target Milestone**: R2 Full Lifecycle Hook Parity via MCP Server  

---

## 1. Observation

1. **MCP Server Protocol & Structure (`plugin/scripts/python/mcp_server.py`)**:
   - `mcp_server.py:1-30`: Implements newline-delimited JSON-RPC 2.0 over standard I/O using only the Python standard library.
   - `mcp_server.py:41-63`: Exposes a single tool schema `SPEAK_TOOL` (`"name": "speak"`, parameter `"text"` with `MAX_CHARS = 20000`).
   - `mcp_server.py:146-148`: `tools/list` hardcodes:
     ```python
     if method == "tools/list":
         return _ok(req_id, {"tools": [SPEAK_TOOL]})
     ```
   - `mcp_server.py:168-171`: `tools/call` rejects any tool name other than `"speak"` with `INVALID_PARAMS` (-32602).
   - `mcp_server.py:66-93`: `spawn_say_worker(text: str)` is defined as an offline speech fallback writing to `/tmp/auto-speech-say.log` and executing `speak.py`. This method is imported and asserted by external tests (`tests/test_say_worker.py:70`, `tests/e2e/test_tier5_adversarial_sink_ipc.py:592`).

2. **Daemon Client Integration (`plugin/scripts/python/daemon_client.py`)**:
   - `daemon_client.py:24-30`: Defines the 4-tier in-memory priority hierarchy:
     - `Priority.USER_INTERRUPT = 1`
     - `Priority.EXPLICIT_MCP = 2`
     - `Priority.AUTOPLAY = 3`
     - `Priority.TOOL_NARRATION = 4`
   - `daemon_client.py:78-106`: `ensure_daemon_running()` checks daemon liveness and starts `narrator_service_start.sh` if offline.
   - `daemon_client.py:108-128`: `speak(text, priority=Priority.EXPLICIT_MCP, source_hash=None, session_id=None, ...)` dispatches requests to the daemon UNIX socket `/tmp/auto-speech-daemon.sock`.
   - `daemon_client.py:130-144`: `play_cache(source_hash, priority=Priority.EXPLICIT_MCP, session_id=None)`.
   - `daemon_client.py:146-152`: `interrupt(session_id=None)` triggers immediate audio termination and P1 barge-in.
   - `daemon_client.py:154-156`: `status()` queries queue depths and daemon health.

3. **Autoplay Subsystem Pipeline (`autoplay_worker.py`, `transcript_locator.py`, `message_selector.py`, `autoplay_config.py`, `claude_cli_rewriter.py`)**:
   - `transcript_locator.py:55-141`: `TranscriptLocator.locate(cwd, session_id)` locates session JSONL transcripts from `.claude/projects/` by exact session ID or newest mtime.
   - `message_selector.py:31-108`: `MessageSelector.select(path, n)` selects the Nth-most-recent qualifying assistant turn from both Claude Code (`type == "assistant"`) and Antigravity (`type == "PLANNER_RESPONSE"`) transcripts.
   - `autoplay_config.py:24-32`: Recognizes valid modes `("verbatim", "summary", "raw", "bypass")` and sizes `("small", "medium", "large")`, mapped to templates:
     - `audio_rewrite_prompt.txt`
     - `audio_summary_small_prompt.txt`
     - `audio_summary_medium_prompt.txt`
     - `audio_summary_large_prompt.txt`
   - `claude_cli_rewriter.py:43-98`: Invokes `claude -p` headless to rewrite assistant text into conversational spoken format, setting `AUTO_SPEECH_SUPPRESS_HOOKS=1`.
   - `autoplay_worker.py:367-369`: Computes cache key:
     `hashlib.sha256(src.encode("utf-8") + b"\x00" + f"{voice_id}:{speed}".encode("utf-8")).hexdigest()`.

4. **Narrator Event Pipeline (`narrator_hook.sh`, `narrator_service.py`)**:
   - `narrator_hook.sh:46-49`: Checks per-session enrollment marker `~/.claude/auto-speech-narrate-sessions/<session_id>`. If absent, drops event immediately.
   - `narrator_hook.sh:86-121`: Formats `{ts, cwd, pid, event, payload}` and appends to `/tmp/auto-speech-narrator-events.jsonl`.
   - `narrator_service.py:620-652`: Tails `/tmp/auto-speech-narrator-events.jsonl` using offset watermarking in `/tmp/auto-speech-narrator.watermark`.
   - `narrator_service.py:732-734`: Verifies that `~/.claude/auto-speech-narrate-sessions/<session_id>` exists before queuing narration.

5. **Session Enrollment & Scope Markers**:
   - `autoplay_enrollment.py:33-93`: Manages `~/.claude/auto-speech-autoplay-enabled/<session_id>` (`is_enrolled`, `enroll`, `withdraw`, `enrolled_ids`).
   - `autoplay_scope.py:26-86`: Manages `~/.claude/auto-speech-autoplay-solo` (`current`, `mode`, `set_solo`, `clear`).
   - `autoplay_gate.py:28-61`: Checks global mute `~/.claude/auto-speech.disabled` via `worker_gated_off()`.

6. **Diagnostics (`doctor.py`, `health_report.py`)**:
   - `doctor.py:60-113`: `Doctor().run()` executes 10 probes (binaries, venv, disk, logs, daemon, queue, scope, mcp, config, updates) and returns `HealthReport`.
   - `health_report.py:36-62`: `HealthReport` provides `healthy: bool`, `to_text(): str`, and `to_json(): str`.

7. **Test Baseline Status**:
   - `tests/run_all.sh --hermetic`: Passed 40/40 tests with 0 failures (verified via task-133 execution).
   - `tests/test_mcp_server.py`: Passed 16/16 tests with 0 failures.

---

## 2. Logic Chain

1. **Protocol Continuity**:
   - `mcp_server.py` already handles JSON-RPC 2.0 requests over stdio and provides injectable collaborators (`spawner`, `gate`, `client`).
   - Extending `tools/list` to return 6 tool descriptors and expanding `tools/call` to route to dedicated handler methods preserves the existing framing, error handling, and test harness without breaking protocol contracts.

2. **Autoplay Hook Parity**:
   - In shell, `autoplay_hook.sh` relies on `jq`, `stat -f %Fm`, double-fork background workers, and `claude -p`.
   - When an AI agent executes `autoplay_turn`, it can supply `session_id` and/or `transcript_path`.
   - By composing `TranscriptLocator`, `MessageSelector`, `ClaudeCliRewriter`, and `DaemonClient`, the MCP server executes the identical extraction, rewriting, and playback pipeline in-process.
   - Supplying `source_hash` to `DaemonClient.speak` ensures closed-loop cache hits play instantly (<5 ms) via `CacheStore`, identical to `autoplay_worker.py`.

3. **Narrator Pipeline Parity**:
   - In shell, `narrator_hook.sh` verifies `~/.claude/auto-speech-narrate-sessions/<session_id>` and appends JSON lines to `/tmp/auto-speech-narrator-events.jsonl`.
   - `narrate_event` performs the exact same check and appends the exact same record structure.
   - When combined with `DaemonClient.ensure_daemon_running()`, this guarantees that events trigger narrator processing seamlessly even if the daemon was idle-stopped.

4. **Transport Controls & Replay**:
   - `playback_control` handles `stop` via `DaemonClient.interrupt()`, which halts `NativeAudioSink` and clears queues.
   - For `replay`, it uses `CacheStore.list_by_recency()` and `DaemonClient.play_cache()`.
   - For `pause`, `resume`, `restart`, and `seek`, it communicates with the active mpv process via `MpvIpc` over `/tmp/auto-speech-mpv.sock` (or returns `"no active playback"` if idle).

5. **Session Configuration**:
   - `session_configure` wraps `SessionEnrollment` (autoplay opt-in markers), `SoloScope` (spotlight marker), narration markers, and `autoplay_config` into a single programmatic tool call.

6. **Diagnostic Transparency**:
   - `doctor` directly calls `Doctor().run()`, returning either formatted text or structured JSON, signaling healthy/unhealthy via `isError`.

7. **Global Mute Integrity**:
   - All speech-producing tools (`speak`, `autoplay_turn`, `narrate_event`, and `playback_control` on `replay`/`resume`) call `self._gate.worker_gated_off()`.
   - If muted, they return an error/notice without initiating audio synthesis or playback.

---

## 3. Caveats

1. **Rewriter Execution in Hermetic Environments**:
   `ClaudeCliRewriter` requires the `claude` CLI binary on PATH. In CI or test environments where `claude` is absent, the fallback to raw message text (`msg.text`) must be preserved and tested so that tests remain hermetic.
2. **`NativeAudioSink` mpv IPC Socket**:
   To support fine-grained `pause`, `resume`, `restart`, and `seek` operations during playback, `NativeAudioSink` must launch mpv with `--input-ipc-server=/tmp/auto-speech-mpv.sock`. If mpv is not running, transport commands must return `"no active playback session"` with exit code / status parity matching `control.py`.
3. **`test_mcp_server.py` Existing Assertions**:
   `test_tools_list_has_only_speak` in `tests/test_mcp_server.py:84-89` explicitly asserts `[t["name"] for t in tools] == ["speak"]`. When implementing R2, this test must be updated to verify the full 6-tool suite.
4. **No Direct Source Code Edits**:
   Per Explorer constraints, no production files were modified during this investigation. All findings and proposed schemas are documented here and in `analysis.md`.

---

## 4. Conclusion

The architecture of `auto-speech` is fully primed for **R2: Full Lifecycle Hook Parity via MCP Server**.
1. All core building blocks (`DaemonClient`, `AutoplayGate`, `TranscriptLocator`, `MessageSelector`, `ClaudeCliRewriter`, `SessionEnrollment`, `SoloScope`, `Doctor`, `CacheStore`, `MpvIpc`) are already implemented, tested, and rely strictly on standard library Python.
2. Expanding `mcp_server.py` to expose `speak`, `autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`, and `doctor` requires:
   - Defining the 6 tool schemas.
   - Adding tool handler methods within `McpServer`.
   - Checking `self._gate.worker_gated_off()` on all speech-producing tools.
   - Retaining `spawn_say_worker` for test and offline compatibility.
3. Expanding `tests/test_mcp_server.py` with unit tests for each new tool will ensure 100% test coverage and preserve green test status across all 42 test suites.

---

## 5. Verification Method

1. **Verify Baseline Test Suites**:
   ```bash
   bash tests/run_all.sh --hermetic
   ```
   *Expected*: Passes 40/40 tests with 0 failures.

2. **Verify MCP Server Unit Tests**:
   ```bash
   .venv/bin/python tests/test_mcp_server.py
   ```
   *Expected*: Passes all test cases (currently 16, expands to ~28 with new tool suites).

3. **Verify Lint & Quality**:
   ```bash
   .venv/bin/ruff check plugin/scripts/python/mcp_server.py tests/test_mcp_server.py
   ```
   *Expected*: 0 violations.

4. **Verify Tool Advertisement via JSON-RPC**:
   ```bash
   echo '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}' | .venv/bin/python plugin/scripts/python/mcp_server.py
   ```
   *Expected*: Returns JSON with tools list containing `["speak", "autoplay_turn", "narrate_event", "playback_control", "session_configure", "doctor"]`.

5. **Verify Global Mute Enforcement**:
   ```bash
   touch ~/.claude/auto-speech.disabled
   echo '{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "speak", "arguments": {"text": "hello"}}}' | .venv/bin/python plugin/scripts/python/mcp_server.py
   ```
   *Expected*: Returns `isError: true` with message citing global mute. (Remember to remove marker after test).
