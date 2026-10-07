# Handoff Report: Milestone M2 (Expanded MCP Server Tool Suite)

**Author**: `teamwork_preview_worker` (`worker_m2`)  
**Timestamp**: 2026-10-06T22:24:00Z  
**Target Repository**: `/Users/joshua/Developer/auto-speech-worktree`  
**Parent Agent**: `4db9af0e-498d-49a5-831a-0009fd31326e`  

---

## 1. Observation

1. **Baseline State**:
   - The initial `plugin/scripts/python/mcp_server.py` advertised only a single tool (`speak`) and handled requests via stdio JSON-RPC 2.0.
   - `tests/test_mcp_server.py` contained 17 unit tests asserting `test_tools_list_has_only_speak`.
   - Running `.venv/bin/python tests/test_mcp_server.py` passed 17/17 tests.
   - Running `bash tests/run_all.sh` passed 50/50 test suites.
   - Running `.venv/bin/ruff check plugin/scripts/python/mcp_server.py tests/test_mcp_server.py` reported 0 violations.

2. **Milestone M2 Implementation**:
   - In `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/mcp_server.py`:
     * Defined complete JSON-RPC 2.0 schemas for all 6 tools: `speak`, `autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`, `doctor`.
     * Preserved 100% backward compatibility for `speak`.
     * Added `autoplay_turn`: resolves session transcript via `TranscriptLocator`, extracts qualifying turn with `MessageSelector`, enforces length threshold (`min_len` >= 20 bytes), computes content-addressable SHA-256 source hash, supports `raw` mode / `bypass_llm`, invokes `ClaudeCliRewriter` with template resolution and graceful raw fallback, queues to `DaemonClient.speak(priority=Priority.AUTOPLAY)` with offline `spawn_say_worker` fallback.
     * Added `narrate_event`: checks global mute, validates session enrollment in `~/.claude/auto-speech-narrate-sessions/<sid>`, returns clean non-error notice on un-enrolled sessions (`isError: False`), ensures daemon liveness via `DaemonClient.ensure_daemon_running()`, and atomically appends JSON records to `/tmp/auto-speech-narrator-events.jsonl`.
     * Added `playback_control`: manages transport actions (`pause`, `resume`, `stop`, `restart`, `seek`, `replay`). `stop` issues `DaemonClient.interrupt()`, unlinks PID file, and sends mpv stop; `pause`/`resume`/`restart`/`seek` interact over `/tmp/auto-speech-mpv.sock` via `MpvIpc`; `resume` and `replay` check global mute; `replay` retrieves cached entries via `CacheStore.list_by_recency()` and plays via `DaemonClient.play_cache()`.
     * Added `session_configure`: manages `SessionEnrollment` markers, narration markers, `SoloScope` spotlight, and writes `autoplay.toml` configuration.
     * Added `doctor`: runs `Doctor().run()` returning text or structured JSON, marking `isError: True` if unhealthy.
     * Refactored `McpServer.__init__` with injectable collaborators (`spawner`, `gate`, `client`, `home`, `locator`, `selector`, `rewriter`, `events_log`, `narrator_sessions_dir`, `voice_calibration_path`, `mpv_sock`, `mpv_pid`, `mpv_ipc_sender`, `cache_store`, `enrollment`, `scope`, `doctor`, `is_mpv_alive`).
   - In `/Users/joshua/Developer/auto-speech-worktree/tests/test_mcp_server.py`:
     * Replaced `test_tools_list_has_only_speak` with `test_tools_list_advertises_all_six_tools`.
     * Maintained all 16 existing `speak` and protocol unit tests.
     * Implemented 35 new hermetic test cases covering all 5 new tools across happy paths, edge cases, error conditions, missing files, global mute gating, and rewriter fallbacks.

3. **Empirical Verification Results**:
   - Command: `.venv/bin/python tests/test_mcp_server.py`
     * Result: Exited 0.
     * Output: `mcp_server: 52 tests passed`.
   - Command: `.venv/bin/ruff check plugin/scripts/python/mcp_server.py tests/test_mcp_server.py`
     * Result: Exited 0.
     * Output: `All checks passed!`.
   - Command: `bash tests/run_all.sh`
     * Result: Exited 0.
     * Output: `ran: 50, failed: 0, all tests passed`.

---

## 2. Logic Chain

1. **Schema & Dispatch Integration (Obs. 2)**:
   The MCP server's `tools/list` must advertise the expanded tool suite to allow AI agents in Claude Code and Antigravity to discover all speech lifecycle operations. Defining `ALL_TOOLS` containing all 6 schemas with strict `additionalProperties: False` and required parameters guarantees full protocol compliance.
2. **Collaborator Injection & Test Hermeticity (Obs. 2, Obs. 3)**:
   By allowing optional dependency injection in `McpServer.__init__`, unit tests can supply temporary home directories, mock daemon clients, and stubbed IPC senders. This guarantees tests never modify host user files (`~/.claude`), kill live processes, or interact with real audio hardware.
3. **Safety & Global Mute Invariant (Obs. 2)**:
   All speech-producing tools (`speak`, `autoplay_turn`, `narrate_event`, and `playback_control` resume/replay) check `self._gate.worker_gated_off()`. When muted, they abort cleanly and return an informative error message without touching audio devices or background queues.
4. **Resilience & Graceful Fallback (Obs. 2, Obs. 3)**:
   `autoplay_turn` gracefully falls back to raw text if the rewriter encounters an error or timeout, and falls back to `spawn_say_worker` if the daemon socket is offline. Unenrolled sessions in `narrate_event` return a clean status (`isError: False`) rather than crashing, preventing AI agent false alarms.
5. **Regression Verification (Obs. 3)**:
   The successful execution of `tests/test_mcp_server.py` (52 passed) and `bash tests/run_all.sh` (50 test suites passed) confirms that the implementation satisfies all requirements of Milestone M2 with zero regressions across the codebase.

---

## 3. Caveats

- Shell hook script reconciliations (`autoplay_hook.sh`, `narrator_hook.sh`) and slash command delegates (`plugin/commands/`) belong to Milestone M3 per `PROJECT.md`.
- No caveats regarding code correctness, test coverage, or linting within the scope of Milestone M2.

---

## 4. Conclusion

Milestone M2 (Expanded MCP Server Tool Suite) is completely implemented and empirically verified inside `/Users/joshua/Developer/auto-speech-worktree`. The MCP server now provides full programmatic lifecycle hook and transport parity through 6 tools (`speak`, `autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`, `doctor`). All 52 unit tests pass and the full test suite remains 100% green.

---

## 5. Verification Method

To independently verify this implementation inside `/Users/joshua/Developer/auto-speech-worktree`:

1. Run the MCP server unit test suite:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/python tests/test_mcp_server.py
   ```
   *Expected Output*: `mcp_server: 52 tests passed` and exit code 0.

2. Run code style and lint check:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/ruff check plugin/scripts/python/mcp_server.py tests/test_mcp_server.py
   ```
   *Expected Output*: `All checks passed!` and exit code 0.

3. Run the complete test suite:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   bash tests/run_all.sh
   ```
   *Expected Output*: `ran: 50, failed: 0, all tests passed` and exit code 0.
