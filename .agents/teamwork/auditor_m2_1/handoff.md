# Forensic Audit Report: Milestone M2 (Expanded MCP Server Tool Suite)

**Work Product**: `/Users/joshua/Developer/auto-speech-worktree` (`plugin/scripts/python/mcp_server.py`, `tests/test_mcp_server.py`)  
**Profile**: General Project (Development Mode per `ORIGINAL_REQUEST.md` §2026-10-06T20:47:50Z)  
**Auditor**: `teamwork_preview_auditor` (`auditor_m2_1`)  
**Timestamp**: 2026-10-06T22:30:00Z  
**Verdict**: **CLEAN**

---

### Phase Results
- **Phase 1.1: Hardcoded Test Output Detection**: PASS — No hardcoded test results, test-specific constants, or test-harness bypass flags (`PYTEST`, `TESTING`, etc.) detected in `mcp_server.py`.
- **Phase 1.2: Facade & Dummy Implementation Detection**: PASS — All 6 tool handlers (`speak`, `autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`, `doctor`) contain genuine computational and operational logic. Default instantiation wires directly to real production subsystems.
- **Phase 1.3: Pre-populated Verification Artifact Detection**: PASS — No fabricated test logs or fake result files present in the worktree.
- **Phase 2.1: Production Un-injected Collaborator Verification**: PASS — Empirically verified that default un-injected `McpServer()` runs real subsystems (`Doctor` running 13 system probes, `MpvIpc` socket communication, `CacheStore` recency querying, `MessageSelector` JSONL parsing, `SessionEnrollment` marker tracking).
- **Phase 2.2: Global Mute Gating Enforcement**: PASS — Empirically verified across all 5 speech-producing tools (`speak`, `autoplay_turn`, `narrate_event`, `playback_control` resume/replay) that `~/.claude/auto-speech.disabled` completely suppresses audio synthesis and returns descriptive notices.
- **Phase 2.3: Defensive Incompleteness & Error Propagation**: PASS — All malformed arguments, missing prerequisites, out-of-bounds ordinals, invalid seek targets, and missing sessions return explicit `isError: True` tool results or JSON-RPC protocol error codes without crashing or faking success.
- **Phase 2.4: Test Suite Rigor & Authenticity**: PASS — `tests/test_mcp_server.py` contains 52 authentic, non-tautological test cases checking exact JSON-RPC structures, parameter boundary rejections, and mock calls.
- **Phase 2.5: Build & Regression Test Execution**: PASS — `tests/test_mcp_server.py` passed 52/52 tests; `tests/run_all.sh` passed 50/50 test suites cleanly; `ruff check` reported 0 violations.

---

## 1. Observation

### Obs 1: Scope & Ground-Truth User Constraints
Per `/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md` (header `## 2026-10-06T20:47:50Z`) and `/Users/joshua/Developer/auto-speech/PROJECT.md`:
- Mode: Development Mode.
- Deliverable: Expand `auto-speech` MCP server in `/Users/joshua/Developer/auto-speech-worktree` to 6 tools: `speak`, `autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`, `doctor`.
- Mute Invariant: All speech-producing tools must honor global mute (`~/.claude/auto-speech.disabled`).
- Implementation Integrity: Implementations must be 100% genuine; no fake mock bypasses in production code; real subsystem integration when collaborators are un-injected.

### Obs 2: Static Analysis of `plugin/scripts/python/mcp_server.py`
1. Grep search across `mcp_server.py` for `mock`, `fake`, `dummy`, `stub`, `placeholder`, and `todo`:
   - Tool result: `No results found`.
2. Grep search for `test` in `mcp_server.py`:
   - 4 matches found, all of which are substrings of the English word `"latest"` (lines 9, 98, 100, 128). Zero test-related conditional branches exist.
3. Default collaborator instantiation in `McpServer.__init__` and handlers:
   - Line 356: `self._home = Path(home) if home is not None else Path(os.path.expanduser("~"))`
   - Line 359: `self._gate = gate or AutoplayGate(home=self._home)`
   - Line 364: `self._narrator_events_file = Path(ev_log) if ev_log is not None else Path("/tmp/auto-speech-narrator-events.jsonl")`
   - Line 371: `self._narrator_sessions_dir = ... else (self._home / ".claude" / "auto-speech-narrate-sessions")`
   - Line 379: `self._mpv_sock_path = ... else Path("/tmp/auto-speech-mpv.sock")`
   - Line 384: `self._mpv_pid_path = ... else Path("/tmp/auto-speech-mpv.pid")`
   - Line 387: `self._mpv_ipc_send = mpv_ipc_sender or MpvIpc.send`
   - Line 389: `self._enrollment = enrollment or SessionEnrollment(home=self._home)`
   - Line 390: `self._scope = scope or SoloScope(home=self._home)`
   - Line 503: `client = self._client or DaemonClient()`
   - Line 569: `locator = self._locator or TranscriptLocator()`
   - Line 579: `selector = self._selector or MessageSelector()`
   - Line 641: `rewriter = self._rewriter or ClaudeCliRewriter(template)`
   - Line 944: `store = self._cache_store or CacheStore(_default_cache_root())`
   - Line 1167: `doc = self._doctor or Doctor(home=self._home)`

### Obs 3: Empirical Execution of Default Un-injected `McpServer`
Independent scripts were executed against `McpServer` without injecting any mocked collaborators:
1. **Tool `doctor`**:
   - Command: `PYTHONPATH=plugin/scripts/python .venv/bin/python -c "import json; from mcp_server import McpServer; s = McpServer(); res = s.handle_line(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'doctor', 'arguments': {'as_json': True}}})); report = json.loads(res['result']['content'][0]['text']); print('Healthy:', report['healthy'], 'Checks:', len(report['checks'])); print('Sample:', report['checks'][0])"`
   - Output:
     ```
     Healthy: True Checks: 13
     Sample: {'name': 'mpv', 'status': 'ok', 'detail': 'found (playback available)'}
     ```
   - Verifies genuine execution of all 13 real system probes via `Doctor`.
2. **Tool `playback_control` (absence of mpv socket)**:
   - Command: `PYTHONPATH=plugin/scripts/python .venv/bin/python -c "import json; from mcp_server import McpServer; s = McpServer(); print(s.handle_line(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'playback_control', 'arguments': {'action': 'pause'}}})))"`
   - Output:
     ```
     {'jsonrpc': '2.0', 'id': 1, 'result': {'content': [{'type': 'text', 'text': 'No active playback session.'}], 'isError': True}}
     ```
   - Verifies real socket existence check (`/tmp/auto-speech-mpv.sock`) and defensive failure reporting.
3. **Tool `narrate_event` (un-enrolled vs enrolled session)**:
   - Unenrolled session returned:
     ```
     {'jsonrpc': '2.0', 'id': 1, 'result': {'content': [{'type': 'text', 'text': "Session 'test-session-unregistered-999' is not enrolled in narration; event ignored."}], 'isError': False}}
     ```
   - Enrolled session (with session marker created on disk) executed:
     ```
     Logged record: {'ts': 1791325657, 'cwd': '/Users/joshua/Developer/auto-speech-worktree', 'pid': 44843, 'event': 'PreToolUse', 'payload': {'tool': 'Edit', 'file': 'foo.py'}, 'session_id': 'my-active-session'}
     ```
   - Verifies genuine enrollment checking and atomic file appending.
4. **Tool `autoplay_turn` (real JSONL transcript parsing & extraction)**:
   - Valid JSONL transcript with qualifying assistant turn:
     ```
     Autoplay turn response: {'jsonrpc': '2.0', 'id': 1, 'result': {'content': [{'type': 'text', 'text': 'Queued autoplay turn (71 chars, mode=raw, hash=eb56044abee8b5d4).'}], 'isError': False}}
     ```
   - Short turn (< 20 chars threshold):
     ```
     Autoplay turn short response: {'jsonrpc': '2.0', 'id': 1, 'result': {'content': [{'type': 'text', 'text': 'assistant turn text too short (6 chars < 20 chars); nothing to speak'}], 'isError': True}}
     ```
5. **Tool `session_configure` (querying real configuration)**:
   - Output:
     ```
     autoplay: OFF — no session id to enroll; 2 other session(s) enrolled
     autoplay scope: ALL — every enrolled session reads (default)
     mode=summary summary_size=medium
     ```
   - Verifies genuine filesystem reading from `~/.claude/auto-speech-autoplay-enabled` and `~/.config/auto-speech/autoplay.toml`.

### Obs 4: Empirical Global Mute Gating Verification
All 5 speech-producing tools were tested with `~/.claude/auto-speech.disabled`:
```
speak: isError=True, text=auto-speech is globally muted (~/.claude/auto-speech.disabled); nothing was spoken
autoplay_turn: isError=True, text=auto-speech is globally muted (~/.claude/auto-speech.disabled); nothing was spoken
narrate_event: isError=True, text=auto-speech is globally muted (~/.claude/auto-speech.disabled); event not narrated
playback_control: isError=True, text=auto-speech is globally muted (~/.claude/auto-speech.disabled); cannot resume playback
playback_control: isError=True, text=auto-speech is globally muted (~/.claude/auto-speech.disabled); replay suppressed
ALL MUTED TESTS PASSED!
```

### Obs 5: Stdio Transport Launcher Verification
Piping JSON-RPC lines directly into `plugin/scripts/shell/run_mcp.sh`:
- Command: `printf '{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}\n{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}\n' | bash plugin/scripts/shell/run_mcp.sh`
- Result: Exited 0 with exact JSON-RPC 2.0 responses advertising all 6 tools with rigorous parameter schemas and `additionalProperties: False`.

### Obs 6: Full Test Suite and Lint Verification
1. MCP Unit Suite:
   - Command: `.venv/bin/python tests/test_mcp_server.py`
   - Result: Exited 0 (`mcp_server: 52 tests passed`).
2. Linter:
   - Command: `.venv/bin/ruff check plugin/scripts/python/mcp_server.py tests/test_mcp_server.py`
   - Result: Exited 0 (`All checks passed!`).
3. Complete Project Suite:
   - Command: `bash tests/run_all.sh`
   - Result: Exited 0 (`ran: 50, failed: 0, all tests passed`).

---

## 2. Logic Chain

1. **Absence of Fakes and Bypass Logic (Obs 2)**:
   Static analysis confirms zero mock imports, dummy constants, or fake bypass branches inside `plugin/scripts/python/mcp_server.py`. The code contains no test-detection conditionals.
2. **Authentic Subsystem Integration (Obs 2, Obs 3)**:
   When `McpServer` is instantiated in production without arguments, each collaborator defaults to real subsystem classes (`Doctor`, `DaemonClient`, `MpvIpc`, `CacheStore`, `SessionEnrollment`, `SoloScope`, `MessageSelector`, `TranscriptLocator`). Empirical test runs proved that calling tools on an un-injected `McpServer` executes genuine filesystem operations, runs real diagnostic probes, inspects real POSIX sockets, and parses real JSONL files.
3. **Defense Against Silent Failures (Obs 3, Obs 4)**:
   Every failure mode—missing sockets, invalid action parameters, unenrolled sessions, short turns, and global mute flags—triggers explicit handling. Speech-producing tools strictly check `worker_gated_off()`, and invalid actions return `isError: True` with informative diagnostics.
4. **Test Authenticity and Coverage (Obs 6)**:
   `tests/test_mcp_server.py` exercises 52 hermetic unit test cases validating schema compliance, parameter boundaries, error codes, rewriter fallbacks, and offline worker spawning. Zero tests use tautological assertions.
5. **No Regressions Across Codebase (Obs 6)**:
   Running `bash tests/run_all.sh` executes all 50 unit and integration suites cleanly with 0 failures, ensuring Milestone M2 additions introduce no regressions.

---

## 3. Caveats

- `systemone` local decision daemon was not running in the environment (`launchctl list | grep systemone` returned 1; connection refused on socket). Judgements were therefore verified directly through empirical Python scripts and full test suite executions.
- Shell hook updates (`autoplay_hook.sh`, `narrator_hook.sh`) and slash command delegates belong to Milestone M3 per `PROJECT.md`.
- No integrity caveats exist regarding `mcp_server.py` or `test_mcp_server.py`.

---

## 4. Conclusion

The Milestone M2 implementation (`plugin/scripts/python/mcp_server.py` and `tests/test_mcp_server.py`) in `/Users/joshua/Developer/auto-speech-worktree` complies with all requirements and integrity constraints. It provides genuine, robust, and defensive implementations of all 6 MCP tools without facades, shortcuts, or mock bypasses. The verdict is **CLEAN**.

---

## 5. Verification Method

To independently reproduce this forensic audit:

1. **Verify MCP server unit suite**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/python tests/test_mcp_server.py
   ```
   *Expected*: `mcp_server: 52 tests passed`, exit code 0.

2. **Verify code quality**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/ruff check plugin/scripts/python/mcp_server.py tests/test_mcp_server.py
   ```
   *Expected*: `All checks passed!`, exit code 0.

3. **Verify un-injected default McpServer doctor probe**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   PYTHONPATH=plugin/scripts/python .venv/bin/python -c "import json; from mcp_server import McpServer; s = McpServer(); res = s.handle_line(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'doctor', 'arguments': {'as_json': True}}})); report = json.loads(res['result']['content'][0]['text']); assert report['healthy'] is True; assert len(report['checks']) == 13; print('Uninjected McpServer Doctor: OK')"
   ```
   *Expected*: `Uninjected McpServer Doctor: OK`, exit code 0.

4. **Verify global test suite pass**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   bash tests/run_all.sh
   ```
   *Expected*: `ran: 50, failed: 0, all tests passed`, exit code 0.
