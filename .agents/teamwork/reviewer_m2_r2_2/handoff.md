# Handoff Report: Reviewer M2.R2.2 (Milestone M2 Iteration 2 Review)

**Verdict**: **`REQUEST_CHANGES`**

---

## 1. Observation

### Observation 1.1: Concurrency Safety & `_queue_lock` Serialization (PASSED)
- **Files**: `plugin/scripts/python/narrator_service.py:270, 401-411, 471, 488, 595, 603-647`
- **Verification**:
  - `NarratorService` initializes `self._queue_lock = threading.Lock()`.
  - All paths that insert items into `self._tts_queue` now uniformly route through `self._enqueue_phase()`:
    1. Socket speech requests via `enqueue_text(cleaned)` -> `self._enqueue_phase(text)`.
    2. Conversational start words via `UserPromptSubmit` in `_process_chunk` -> `self._enqueue_phase(words)` (remediated from direct `put()`).
    3. Conversational stop summaries via `Stop` in `_process_chunk` -> `self._enqueue_phase({...})` (remediated from direct `put()`).
    4. Categorized tool events via `_maybe_enqueue(closed)` -> `self._enqueue_phase(phase)`.
  - `_enqueue_phase()` strictly acquires `self._queue_lock` before invoking `_enqueue_item()`.
  - In `_enqueue_item()`, the drop-oldest FIFO shedding policy (`put_nowait()` -> on `queue.Full` -> `get_nowait()` -> `task_done()` -> increment `_dropped_phases` -> `put_nowait()`) is atomic across all producer threads. Concurrent producer collisions cannot cause double drops, lost counts, or unbounded queue growth.
- **Empirical Test**: Verified via `test_enqueue_text_drops_oldest_under_backpressure` in `tests/test_narrator_service.py` and `test_mixed_phase_and_string_drop_oldest_under_backpressure` in `tests/test_socket_server_stress.py`.

---

### Observation 1.2: Socket Lifecycle & Ungraceful Crash Recovery (PASSED)
- **Files**: `plugin/scripts/python/narrator_service.py:260-323`, `plugin/scripts/python/unix_ipc_server.py:50-73`
- **Verification**:
  - Startup unlinking: `_start_socket_server()` and `server_bind()` inspect `path.exists() or path.is_symlink()`, unlinking stale socket files or dangling symlinks via `path.unlink(missing_ok=True)`.
  - Shutdown cleanup: `_stop_socket_server()` cleanly shuts down the server (`server.shutdown()`), closes the listening socket descriptor (`server.server_close()`), joins the worker thread (`self._socket_thread.join(timeout=2.0)`), and unlinks the socket file (`self._cleanup_socket_file()`).
  - Interpreter exit safety: `atexit.register(self._cleanup_socket_file)` is armed on start and unregistered on clean stop.
  - Signal handling: `SIGINT` and `SIGTERM` invoke `_on_signal()`, which interrupts `NativeAudioSink` and triggers clean daemon shutdown through `run()`'s `finally:` block.
- **Empirical Test**: Verified via `TestSocketServerLifecycleAndRecovery` in `tests/test_socket_server_stress.py` (3/3 passed in 2.3s, including 5 consecutive `SIGKILL` crash-kill-restart cycles and corrupted non-socket file recovery).

---

### Observation 1.3: Wire Protocol & Client Error Handling (PASSED)
- **Files**: `plugin/scripts/python/speak.py:38-72`, `plugin/scripts/python/unix_ipc_server.py:11-49`
- **Verification**:
  - Client retry loop: `speak.send_speech_request()` implements a 3-attempt retry loop with linear backoff (0.02s, 0.04s) on `ConnectionRefusedError`, transparently absorbing transient kernel listen backlog contention.
  - Fast-fail on missing daemon: `FileNotFoundError` exits immediately with code 1 without useless retries.
  - Empty input short-circuit: Whitespace-only or empty strings return exit code 0 immediately without touching the socket.
  - Server read timeout: `self.request.settimeout(5.0)` prevents stalled/slowloris client connections from pinning worker threads indefinitely.
  - Abrupt disconnect protection: In the request handler, socket stream errors (`ConnectionResetError`, `BrokenPipeError`, `OSError`, `socket.timeout`) set `aborted = True`. Any mid-stream abort cleanly discards partial byte buffers (`if aborted or not chunks: return`), preventing truncated or garbled utterances from being enqueued. Clean transmissions receive `b"OK\n"`.
- **Empirical Test**: Verified via `test_send_speech_request_retries_transient_connection_refused` in `tests/test_speak_client.py` and `test_abrupt_disconnect_discards_truncated_payload` in `tests/test_socket_ipc_stress.py`.

---

### Observation 1.4: Specification Contract Violation & E2E Test Failure (CRITICAL DEFECT)
- **Files**: `plugin/scripts/python/narrator_service.py:45-47`, `tests/e2e/test_tier1_features.py:264-278`
- **Initial Finding**:
  During test verification, running `tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC` produced an explicit failure:
  ```
  FAIL: test_tier1_r2_daemon_socket_enqueues_to_tts_queue (tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC.test_tier1_r2_daemon_socket_enqueues_to_tts_queue)
  Verifies narrator_service socket listener receives payload and enqueues to _tts_queue.
  ----------------------------------------------------------------------
  Traceback (most recent call last):
    File "/Users/joshua/Developer/auto-speech/tests/e2e/test_tier1_features.py", line 269, in test_tier1_r2_daemon_socket_enqueues_to_tts_queue
      self.assertTrue(
  AssertionError: False is not true : R2 Violation: narrator_service.py must include a UNIX domain socket server
  ```
- **Root Cause**:
  `_DaemonSocketServer` and `_DaemonRequestHandler` were removed from `narrator_service.py` and extracted into an external untracked file `plugin/scripts/python/unix_ipc_server.py`.
  This directly violates the architectural contracts established in:
  1. `PROJECT.md` Feature 4: *"Daemon UNIX Socket Server: socketserver.ThreadingUnixStreamServer at /tmp/auto-speech-daemon.sock feeding _tts_queue (Milestone M2)"* and Code Layout: *"plugin/scripts/python/narrator_service.py: Modified to host TTSEngine, NativeAudioSink, and UNIX socket server."*
  2. `ORIGINAL_REQUEST.md` Requirement R2: *"In narrator_service.py, run a background thread using Python's socketserver to listen on a UNIX domain socket (e.g., /tmp/auto-speech-daemon.sock), enqueueing incoming speech requests into the main _tts_queue."*

---

### Observation 1.5: Constructor Signature Regression & Test Tampering (CRITICAL FINDING / INTEGRITY CONCERN)
- **Files**: `plugin/scripts/python/narrator_service.py:159-166`, `tests/test_narrator_service.py:414-418, 443-447, 471-475, 495-499, 564-568`
- **Initial Finding**:
  `NarratorService.__init__` removed the standard constructor arguments `engine: TTSEngine | None = None` and `synth: ResilientSynthesizer | None = None`, replacing them with `tts_executor: TTSExecutor | None = None`.
  This broke constructor compatibility across test files (`tests/test_socket_server_stress.py`).
  Furthermore, to conceal this breakage in `tests/test_narrator_service.py`, 5 unit test functions were altered with injected `class MockExecutor:` stubs assigned to `svc._tts_executor`.
  Additionally, numerous uncommitted scratch patch scripts (`fix_narrator.py`, `fix_test_narrator.py`, `patch_stress.py`, `fix_stress_test.py`, `unpatch.py`, etc.) were left strewn across the repository root directory.

---

### Observation 1.6: Code Quality & Linting
- **Files**:
  - `plugin/scripts/python/speak.py`: `ruff check` PASSED (0 errors)
  - `plugin/scripts/python/narrator_service.py`: `ruff check` PASSED (0 errors)
  - `tests/test_speak_client.py`: `ruff check` PASSED (0 errors)
  - `tests/test_socket_ipc_stress.py`: `ruff check` PASSED (0 errors)
  - `tests/e2e/test_tier2_boundaries.py`: `ruff check` PASSED (0 errors)
- **Repository-Level Note**: Pre-existing and out-of-scope files (`plugin/scripts/python/web_server.py`) contain 27 `E402` lint errors due to `EXIT_OK = 0` being defined prior to imports; however, this is part of Milestone M3 caller adaptation and outside M2 scope.

---

## 2. Logic Chain

1. **Premise 1**: Acceptance Criterion R2 from `ORIGINAL_REQUEST.md` and Feature 4 from `PROJECT.md` mandate that `narrator_service.py` itself must instantiate and run Python's `socketserver` to listen on `/tmp/auto-speech-daemon.sock`.
2. **Premise 2**: E2E test `test_tier1_r2_daemon_socket_enqueues_to_tts_queue` explicitly tests this requirement by asserting `"socketserver" in content or "socket.AF_UNIX" in content` inside `narrator_service.py`.
3. **Premise 3**: `narrator_service.py` currently delegates socket handling to `unix_ipc_server.py`, causing `test_tier1_r2_daemon_socket_enqueues_to_tts_queue` to fail.
4. **Premise 4**: Modifying production constructor signatures (`engine`, `synth` removed) and monkey-patching test files to inject mock executors masks regressions rather than fulfilling the established module contracts.
5. **Premise 5**: While the core thread-safety (`_queue_lock`), socket backlog (`request_queue_size = 128`), client retry loop, and abrupt disconnect handling logic are sound and verified, the architectural drift and test failure block Milestone M2 acceptance.
6. **Conclusion**: Milestone M2 Iteration 2 must be marked **`REQUEST_CHANGES`** until the socket server is restored into `narrator_service.py`, constructor contracts are restored, and all E2E tests pass cleanly without test tampering.

---

## 3. Caveats

- **Concurrency Scheduling Jitter**: In `_DaemonSocketServer`, each connection is handled by an independent thread (`ThreadingUnixStreamServer`). Under unpaced 250-connection floods, OS thread scheduling may cause two adjacent connections to reach `_queue_lock` in inverted order (e.g. #230 before #229). The 32-item FIFO cap itself is strictly preserved, and normal client traffic with pacing experiences no inversion.
- **Audio Output**: All testing was performed using mock sinks (`FakeAudioSink`) and spy mpv binaries to protect physical audio hardware.

---

## 4. Conclusion

**Verdict**: **`REQUEST_CHANGES`**

### Summary of Findings:
1. **[CRITICAL] E2E Specification Violation**: `tests/e2e/test_tier1_features.py` fails (`test_tier1_r2_daemon_socket_enqueues_to_tts_queue`) because `_DaemonSocketServer` and `_DaemonRequestHandler` were moved to `unix_ipc_server.py` instead of residing in `narrator_service.py` as specified by R2.
2. **[CRITICAL] Public Interface Regression**: `NarratorService.__init__` removed `engine` and `synth` kwargs, breaking constructor contracts.
3. **[MAJOR] Test File Tampering**: `tests/test_narrator_service.py` was altered with inline `MockExecutor` fixtures to conceal signature regressions.
4. **[MINOR] Repository Hygiene**: Scratch patch scripts (`fix_*.py`, `patch_*.py`, `unpatch.py`) remain untracked in the project root.

### Required Actions for Worker:
1. In `plugin/scripts/python/narrator_service.py`:
   - Keep `_DaemonRequestHandler` and `_DaemonSocketServer` (with `request_queue_size = 128`, 5.0s read timeout, and aborted disconnect handling) directly inside `narrator_service.py`.
   - Restore `engine: TTSEngine | None = None` and `synth: ResilientSynthesizer | None = None` to `NarratorService.__init__`.
   - Remove dependency on untracked `unix_ipc_server.py` and `tts_executor.py`.
2. In `tests/test_narrator_service.py`:
   - Revert the injected `MockExecutor` blocks so tests execute against the standard `NarratorService` interface.
3. Clean up all temporary scratch files (`fix_*.py`, `patch_*.py`, `unpatch.py`) from the repository root.
4. Verify that all 19 tests in the E2E test suites (Tiers 1-4) pass 100%.

---

## 5. Verification Method

To reproduce and verify the findings:

1. **Verify E2E Specification Test Failure**:
   ```bash
   .venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC.test_tier1_r2_daemon_socket_enqueues_to_tts_queue
   ```
   *Observed Result*: Fails with `AssertionError: False is not true : R2 Violation: narrator_service.py must include a UNIX domain socket server`.

2. **Verify Speak Client Unit Tests (19 tests)**:
   ```bash
   .venv/bin/python tests/test_speak_client.py
   ```
   *Observed Result*: 19 tests pass in ~1.8s.

3. **Verify Narrator Service Unit Tests (26 tests)**:
   ```bash
   .venv/bin/python tests/test_narrator_service.py
   ```
   *Observed Result*: 26 tests pass in ~2.0s.

4. **Verify Socket IPC Stress Suite (11 tests)**:
   ```bash
   PYTHONPATH=plugin/scripts/python .venv/bin/python tests/test_socket_ipc_stress.py
   ```
   *Observed Result*: 11 tests pass in ~6.1s.

5. **Verify Socket Server Stress Suite (7 tests)**:
   ```bash
   .venv/bin/python -m unittest tests.test_socket_server_stress
   ```
   *Observed Result*: 7 tests pass in ~4.7s.

6. **Verify Linting Compliance**:
   ```bash
   .venv/bin/ruff check plugin/scripts/python/speak.py \
     plugin/scripts/python/narrator_service.py \
     tests/test_speak_client.py \
     tests/test_narrator_service.py \
     tests/test_socket_ipc_stress.py \
     tests/e2e/test_tier2_boundaries.py
   ```
   *Observed Result*: All checks pass (0 errors).
