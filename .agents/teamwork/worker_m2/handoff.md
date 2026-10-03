# Handoff Report: Milestone M2 (Thin Client IPC via UNIX Sockets)

## 1. Observation

1. **Pre-Implementation Codebase State**:
   - `plugin/scripts/python/speak.py` imported `from pipeline import PipelineOrchestrator` and directly executed full pipeline synthesis and mpv playback per CLI invocation.
   - `plugin/scripts/python/narrator_service.py` had no UNIX domain socket listener or IPC mechanism; speech requests were only triggered via JSONL events from `_tail_events()`.
   - Running `.venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC` produced:
     ```
     FAIL: test_tier1_r2_daemon_socket_enqueues_to_tts_queue
     AssertionError: False is not true : R2 Violation: narrator_service.py must include a UNIX domain socket server
     FAIL: test_tier1_r2_speak_cli_transmits_stdin_to_socket
     AssertionError: 124 != 0 : speak.py failed with exit code 124. TimeoutExpired: command timed out after 2.5s
     ```
   - Running `.venv/bin/python -m unittest tests.e2e.test_tier2_boundaries.TestTier2R2Boundaries` produced:
     ```
     FAIL: test_tier2_r2_empty_stdin_ignored_by_daemon (AssertionError: 4 != 0)
     FAIL: test_tier2_r2_large_socket_payload_chunking (AssertionError: 124 != 0)
     FAIL: test_tier2_r2_special_characters_and_multiline_payload (AssertionError: 124 != 0)
     ```

2. **Files Modified and Created**:
   - `plugin/scripts/python/speak.py`: Refactored to thin client reading `sys.stdin`, short-circuiting on empty/whitespace input, connecting to `/tmp/auto-speech-daemon.sock` (or `$AUTO_SPEECH_DAEMON_SOCK`), sending UTF-8 payload, executing write shutdown (`SHUT_WR`), closing socket, and exiting 0 cleanly. Gracefully outputs error to `stderr` and exits code 1 if socket is absent or connection is refused.
   - `plugin/scripts/python/narrator_service.py`: Added `_DaemonSocketServer(socketserver.ThreadingUnixStreamServer)` and `_DaemonRequestHandler`. In `NarratorService`, added background socket server thread in `_start_socket_server()`, automatic unlinking of stale socket in `server_bind()`, safe stop and unlinking in `_stop_socket_server()`, defensive `atexit` registration, and thread-safe drop-oldest enqueueing via `enqueue_text()` guarded by `_queue_lock`.
   - `tests/test_speak_client.py`: Created 18 standalone unit tests covering CLI argument parsing, legacy backward-compatibility flags (`--ordinal`, `--keep-artifacts`, `--source-hash`), 64-hex hash validation, socket path resolution, empty stdin short-circuit, mocked socket wire protocol, and real ephemeral socket server interactions with emojis and 128KB payloads.
   - `tests/test_narrator_service.py`: Added 5 unit tests verifying stale socket unlinking at startup, cleanup on shutdown, payload enqueueing to `_tts_queue`, empty input filtering, resilience against abrupt client reset (`SO_LINGER 0`), and backpressure queue shedding. Total suite grew from 21 to 26 passing tests.

3. **Post-Implementation Verification Results**:
   - `.venv/bin/python tests/test_speak_client.py`:
     ```
     Ran 18 tests in 1.677s
     OK
     ```
   - `.venv/bin/python tests/test_narrator_service.py`:
     ```
     narrator_service: 26 tests passed
     ```
   - `.venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC`:
     ```
     Ran 6 tests in 1.199s
     OK
     ```
   - `.venv/bin/python -m unittest tests.e2e.test_tier2_boundaries.TestTier2R2Boundaries`:
     ```
     Ran 5 tests in 2.223s
     OK
     ```
   - `.venv/bin/python -m unittest tests.e2e.test_tier3_combinations`:
     ```
     Ran 5 tests in 3.845s
     OK
     ```
   - `.venv/bin/python -m unittest tests.e2e.test_tier4_scenarios`:
     ```
     Ran 3 tests in 2.172s
     OK
     ```
   - `.venv/bin/ruff check plugin/scripts/python/speak.py plugin/scripts/python/narrator_service.py tests/test_speak_client.py`:
     ```
     All checks passed!
     ```

## 2. Logic Chain

1. From Observation 1, `speak.py` was previously invoking `PipelineOrchestrator`, causing MLX engine load latency and detached process sprawl. The E2E tests expect a zero-overhead thin client communicating over a UNIX domain socket.
2. Replacing `PipelineOrchestrator` in `speak.py` with standard library `socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)` directly eliminated the secondary Python process spawns and removed `PipelineOrchestrator` dependencies from the client layer.
3. Adding `_DaemonSocketServer(socketserver.ThreadingUnixStreamServer)` to `narrator_service.py` allows handling multiple concurrent client bursts without blocking the main event-tailing loop.
4. Implementing `server_bind()` unlinking and `_start_socket_server()` stale-file cleanup ensures that previous crashes do not block subsequent daemon instances from binding with `Address already in use`.
5. Guarding `_enqueue_phase` and `enqueue_text` with `self._queue_lock` ensures thread-safe operations on `_tts_queue` when concurrent socket requests arrive while `_tail_events()` enqueues JSONL events.
6. The test executions in Observation 3 confirm that all feature, boundary, concurrency, and scenario tests for Milestone M2 pass cleanly with zero regressions.

## 3. Caveats

- Milestone M3 files (`run_speak.sh`, `pipeline.py`, `short_path.py`, `mpv_controller.py`, `session_dir.py`) were left untouched in this milestone as they are owned by Milestone M3 (Elimination of Dead Sprawl & Caller Realignment).
- Test runners in `tests/e2e/test_tier2_boundaries.py:345` referencing `run_all.sh` deletions relate to Milestone M3 deliverables and do not belong to M2.

## 4. Conclusion

Milestone M2 (Thin Client IPC via UNIX Sockets) is fully implemented and verified:
- `speak.py` functions as a thin CLI client forwarding stdin to `/tmp/auto-speech-daemon.sock` with backward-compatible CLI arguments.
- `narrator_service.py` runs a multi-threaded UNIX domain socket server enqueueing into `_tts_queue` with drop-oldest backpressure and automatic socket lifecycle management.
- Unit and E2E test suites (Tier 1 R2, Tier 2 R2, Tier 3 combinations, Tier 4 scenarios) pass 100% with clean linting.

## 5. Verification Method

To independently verify this milestone:

1. Run the standalone unit test suite for the thin client:
   ```bash
   .venv/bin/python tests/test_speak_client.py
   ```
2. Run the narrator service unit tests:
   ```bash
   .venv/bin/python tests/test_narrator_service.py
   ```
3. Run the Tier 1 R2 feature tests:
   ```bash
   .venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC
   ```
4. Run the Tier 2 R2 boundary tests:
   ```bash
   .venv/bin/python -m unittest tests.e2e.test_tier2_boundaries.TestTier2R2Boundaries
   ```
5. Run the linter:
   ```bash
   .venv/bin/ruff check plugin/scripts/python/speak.py plugin/scripts/python/narrator_service.py tests/test_speak_client.py
   ```
