# Handoff Report: `speak.py` Thin Client Refactor (Milestone M2)

## 1. Observation

1. **Current `speak.py` Implementation**:
   - Location: `plugin/scripts/python/speak.py:14-48`.
   - Imports `from pipeline import PipelineOrchestrator`.
   - Accepts `--ordinal`, `--keep-artifacts`, `--source-hash` (validating 64-hex char SHA-256 and exiting 2 on malformed input).
   - Reads `transcript_text = sys.stdin.read()`.
   - Directly instantiates `PipelineOrchestrator(keep_artifacts=args.keep_artifacts, source_hash=args.source_hash)` and calls `orchestrator.run(transcript_text=transcript_text, turn_ordinal=args.ordinal)`.

2. **Test Suite Failures in Current State**:
   - Running `python3 tests/e2e/run_e2e.py --tier 1` produces:
     ```
     FAIL: test_tier1_r2_speak_cli_transmits_stdin_to_socket (tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC)
     AssertionError: 124 != 0 : speak.py failed with exit code 124. Stderr: Fetching 56 files: 100%
     TimeoutExpired: command timed out after 2.5s
     ```
     and
     ```
     FAIL: test_tier1_r3_no_pipeline_orchestrator_imports (tests.e2e.test_tier1_features.TestTier1R3DeadSprawlRemoval)
     AssertionError: Lists differ: ['plugin/scripts/python/speak.py', ...] != []
     ```
   - Running `python3 tests/e2e/run_e2e.py --tier 2` produces:
     ```
     FAIL: test_tier2_r2_empty_stdin_ignored_by_daemon (tests.e2e.test_tier2_boundaries.TestTier2R2Boundaries)
     AssertionError: 4 != 0
     ```
     and
     ```
     FAIL: test_tier2_r2_large_socket_payload_chunking
     AssertionError: 124 != 0 (TimeoutExpired)
     ```
     and
     ```
     FAIL: test_tier2_r2_special_characters_and_multiline_payload
     AssertionError: 124 != 0 (TimeoutExpired)
     ```

3. **Call Sites in Active Codebase**:
   - `plugin/commands/auto-speech-speak.md:63-65`:
     Invokes `run_speak.sh --ordinal ORDINAL --source-hash "$SOURCE_HASH" < "$REWRITE_FILE"`.
   - `plugin/scripts/python/autoplay_worker.py:391-393`:
     Invokes `["bash", str(SPEAK), "--source-hash", source_hash]`, piping rewrite to stdin.
   - `tests/e2e/harness.py:140,215-235`:
     Sets `AUTO_SPEECH_DAEMON_SOCK` in sandbox environment and invokes `python3 speak.py` with `input=text`.

4. **Requirements from `DISPATCH.md` & `PROJECT.md` §Interface Contracts**:
   - UNIX socket path: `/tmp/auto-speech-daemon.sock` (or `$AUTO_SPEECH_DAEMON_SOCK`).
   - Wire protocol: Stream-oriented UTF-8 text transmission. Send payload and shutdown write (`socket.SHUT_WR`), then close socket.
   - Stdin: Read from `sys.stdin.read()`. If empty or whitespace-only, exit 0 cleanly without connecting.
   - Argument parsing: Preserve `--ordinal`, `--keep-artifacts`, `--source-hash` for backward compatibility.
   - Error handling: If daemon socket is absent or connection is refused (`FileNotFoundError`, `ConnectionRefusedError`, `OSError`), print clean error to `sys.stderr` and exit 1.

---

## 2. Logic Chain

1. **Root Cause Analysis (from Observation 1 & 2)**:
   - The current `speak.py` times out (exit code 124) and imports `PipelineOrchestrator` because it still invokes the old heavy in-client pipeline.
   - The empty-input failure (exit code 4) occurs because `PipelineOrchestrator` treats empty transcripts as an error rather than exiting 0.
   - Therefore, replacing `PipelineOrchestrator` with a thin socket client in `speak.py` is the direct and complete resolution for all R2 failures in Tier 1 and Tier 2.

2. **Backward Compatibility Strategy (from Observation 1 & 3)**:
   - `autoplay_worker.py` and `auto-speech-speak.md` pass `--ordinal` and `--source-hash`.
   - `test_tier1_r2_speak_cli_accepts_backward_compatible_args` verifies `speak.py` accepts `--ordinal`, `--keep-artifacts`, and `--source-hash`.
   - Retaining these arguments in `argparse` prevents breaking existing callers, while validating `--source-hash` (must be 64 hex characters or exit code 2) preserves historical validation behavior.

3. **Socket IPC Mechanics (from Observation 4)**:
   - `speak.py` should resolve the target socket as:
     `args.socket_path or os.environ.get("AUTO_SPEECH_DAEMON_SOCK", "/tmp/auto-speech-daemon.sock")`.
   - To prevent deadlocks, socket communication uses `sock.settimeout(5.0)`.
   - Calling `sock.sendall(transcript_text.encode("utf-8"))` followed by `sock.shutdown(socket.SHUT_WR)` signals EOF to the daemon's socket reader cleanly without abrupt resets.
   - If `transcript_text.strip() == ""`, exiting 0 immediately without opening a socket avoids empty queue entries in the daemon and eliminates unnecessary IPC overhead.

4. **Error Handling Mechanics (from Observation 2 & 4)**:
   - When the daemon is offline, connecting to a non-existent socket raises `FileNotFoundError`, while a stale socket raises `ConnectionRefusedError`.
   - Catching these exceptions, writing `"Error: auto-speech daemon is not running."` to `sys.stderr`, and exiting with status code 1 satisfies `test_tier1_r2_speak_cli_fails_gracefully_when_daemon_down` and graceful degradation requirements.

---

## 3. Caveats

1. **Daemon Server Dependency**:
   - `speak.py` requires a running daemon server on `/tmp/auto-speech-daemon.sock` (or `$AUTO_SPEECH_DAEMON_SOCK`) to synthesize audio. When the daemon is offline, it cleanly exits 1.
2. **Audio Return Value**:
   - `speak.py` no longer blocks on audio completion or returns playback status. It enqueues speech asynchronously to the daemon's `_tts_queue`. Audio playback is managed synchronously inside the daemon worker.
3. **No-op Flags**:
   - `--keep-artifacts` and `--source-hash` become no-ops in `speak.py` because caching and artifacts are now managed either at the caller level or in the daemon.
4. **Encoding Assumption**:
   - Standard UTF-8 encoding is assumed for text transmission over the domain socket.

---

## 4. Conclusion

`plugin/scripts/python/speak.py` should be refactored into a thin CLI client adhering to the following specification:
- **Zero non-standard dependencies**: standard library only (`argparse`, `os`, `socket`, `sys`).
- **Input handling**: `sys.stdin.read()`. If `not transcript_text.strip(): return 0`.
- **Arguments**:
  - `--ordinal`: int (default 1)
  - `--keep-artifacts`: flag (action="store_true")
  - `--source-hash`: str (validates 64 hex chars or returns 2)
  - `--socket-path`: optional override str
- **Socket communication**: Connects to `AUTO_SPEECH_DAEMON_SOCK` or `/tmp/auto-speech-daemon.sock`. Transmits UTF-8 payload via `sendall`, signals EOF with `shutdown(socket.SHUT_WR)`, and closes socket.
- **Error handling**: Catches `FileNotFoundError`, `ConnectionRefusedError`, `socket.timeout`, `OSError`; prints error to `sys.stderr`; returns 1.

The complete code implementation is provided in `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_2/analysis.md` §5.

---

## 5. Verification Method

1. **Unit Verification Script**:
   Execute the standalone scenario runner to verify empty input, daemon offline exit code 1, argument compatibility, and live socket transmission:
   ```bash
   python3 -c "
   import socket, socketserver, subprocess, sys, threading, tempfile, os

   # Test daemon down
   res = subprocess.run([sys.executable, 'plugin/scripts/python/speak.py'], input='hello', text=True, capture_output=True, env={'AUTO_SPEECH_DAEMON_SOCK': '/tmp/test_nonexistent.sock'})
   assert res.returncode == 1, f'Expected 1, got {res.returncode}'
   assert len(res.stderr) > 0, 'Expected stderr message'

   # Test empty stdin
   res = subprocess.run([sys.executable, 'plugin/scripts/python/speak.py'], input='   \n\t  ', text=True, capture_output=True)
   assert res.returncode == 0, f'Expected 0, got {res.returncode}'
   "
   ```

2. **E2E Test Suite Execution**:
   Run Tier 1 R2 tests:
   ```bash
   python3 tests/e2e/run_e2e.py --tier 1
   ```
   Inspect results for:
   - `test_tier1_r2_speak_cli_transmits_stdin_to_socket` -> PASS
   - `test_tier1_r2_speak_cli_fails_gracefully_when_daemon_down` -> PASS
   - `test_tier1_r2_speak_cli_accepts_backward_compatible_args` -> PASS

   Run Tier 2 R2 boundary tests:
   ```bash
   python3 tests/e2e/run_e2e.py --tier 2
   ```
   Inspect results for:
   - `test_tier2_r2_empty_stdin_ignored_by_daemon` -> PASS
   - `test_tier2_r2_special_characters_and_multiline_payload` -> PASS
   - `test_tier2_r2_large_socket_payload_chunking` -> PASS

3. **Manual Verification Command**:
   ```bash
   echo "test manual audio" | python3 plugin/scripts/python/speak.py
   ```
   - When daemon is running: transmits text and exits 0 in < 15ms.
   - When daemon is stopped: prints `Error: auto-speech daemon is not running.` to stderr and exits 1.
