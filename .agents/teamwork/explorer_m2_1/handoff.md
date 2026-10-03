# Handoff Report: Daemon Socket Server Integration (Milestone M2.1)

**Agent**: `explorer_m2_1`  
**Target Module**: `plugin/scripts/python/narrator_service.py`  
**Handoff Type**: Hard (Task Complete)  
**Date**: 2026-10-03  

---

## 1. Observation

1. **File Locations & Current Baseline**:
   - `plugin/scripts/python/narrator_service.py` defines `NarratorService` and its worker threads.
   - Lines 47-51 of `narrator_service.py`:
     ```python
     EVENTS_LOG = Path("/tmp/auto-speech-narrator-events.jsonl")
     PID_FILE = Path("/tmp/auto-speech-narrator-daemon.pid")
     LOG_FILE = Path("/tmp/auto-speech-narrator-daemon.log")
     DEPTH_FILE = Path("/tmp/auto-speech-narration-depth")
     WATERMARK_FILE = Path("/tmp/auto-speech-narrator-daemon.watermark")
     ```
   - Lines 537-538 of `narrator_service.py`:
     ```python
     if isinstance(phase, str):
         self._speak(phase)
     ```
     `_tts_worker` already contains branch logic to process and speak plain `str` objects from `_tts_queue` using in-process `ResilientSynthesizer` and `NativeAudioSink`.
   - Lines 285-289 of `narrator_service.py`:
     ```python
     if time.time() - self._last_event_ts > self._idle_shutdown:
         _log(f"idle for >{self._idle_shutdown}s → shutting down")
         if self._fsm.can(IDLE_SHUTDOWN):
             self._fsm.transition(IDLE_SHUTDOWN)
         return
     ```
   - Lines 28-36 of `tests/test_narrator_service.py`:
     ```python
     def _bare_service(max_queue: int):
         svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
         svc._max_queue = max_queue
         svc._tts_queue = queue.Queue(maxsize=max_queue)
         svc._dropped_phases = 0
         return svc
     ```
     Existing unit tests instantiate `NarratorService` via `__new__` without calling `__init__`.

2. **Test Baseline Execution**:
   - `PYTHONPATH=. pytest tests/test_narrator_service.py`:
     Result: 21 passed in 0.10s.
   - `PYTHONPATH=. pytest tests/e2e/test_unified_daemon_e2e.py`:
     Result: 20 passed, 21 failed. All 20 passing tests are R1 (In-Process TTS & NativeAudioSink). The 21 failures correspond to un-implemented R2 (Thin Client IPC via UNIX Sockets) and R3 (Dead Architectural Sprawl).

3. **Explicit Contract Assertions in Tests**:
   - `tests/e2e/test_tier1_features.py:268-276`:
     ```python
     self.assertTrue(
         "socketserver" in content or "socket.AF_UNIX" in content,
         "R2 Violation: narrator_service.py must include a UNIX domain socket server",
     )
     self.assertIn(
         "auto-speech-daemon.sock",
         content,
         "R2 Violation: narrator_service.py must listen on /tmp/auto-speech-daemon.sock",
     )
     ```
   - `tests/e2e/test_tier1_features.py:339-342`:
     ```python
     self.assertTrue(
         "unlink" in content or "remove" in content,
         "narrator_service.py must include cleanup logic for socket file on shutdown",
     )
     ```
   - `tests/e2e/harness.py:140`:
     ```python
     self.env["AUTO_SPEECH_DAEMON_SOCK"] = str(self.socket_path)
     ```
   - `tests/e2e/test_tier3_combinations.py:140-174` (`test_tier3_multi_client_concurrent_burst`):
     Runs 10 concurrent clients simultaneously via `ThreadPoolExecutor`, requiring multi-threaded socket request handling.
   - `tests/e2e/test_tier2_boundaries.py:202-230` (`test_tier2_r2_large_socket_payload_chunking`):
     Transmits 128 KB text, requiring chunked `recv()` reading in a loop until EOF.
   - `tests/e2e/test_tier2_boundaries.py:232-270` (`test_tier2_r2_abrupt_client_disconnect`):
     Closes connection with `SO_LINGER 0`, requiring exception shielding against `BrokenPipeError` / `ConnectionResetError`.
   - `tests/e2e/test_tier2_boundaries.py:272-288` (`test_tier2_r2_stale_socket_file_cleanup_on_startup`):
     Pre-existing socket file must be unlinked before binding.

---

## 2. Logic Chain

1. **Server Choice**:
   - Observation 3 shows that 10 concurrent clients execute against the socket in `test_tier3_multi_client_concurrent_burst`.
   - `socketserver.UnixStreamServer` is single-threaded; any slow reader or paused write-shutdown blocks subsequent clients, causing timeouts.
   - Therefore, `socketserver.ThreadingUnixStreamServer` must be used. Setting `daemon_threads = True` ensures client worker threads do not block daemon process termination.

2. **Path Resolution**:
   - Observation 3 shows `tests/e2e/test_tier1_features.py` expects `"auto-speech-daemon.sock"` in source code, while `tests/e2e/harness.py` sets `AUTO_SPEECH_DAEMON_SOCK` in the environment.
   - Therefore, the path hierarchy must be: constructor argument `socket_path` (if provided) > `os.environ.get("AUTO_SPEECH_DAEMON_SOCK")` > default `Path("/tmp/auto-speech-daemon.sock")`.

3. **Lifecycle & Stale Cleanup**:
   - In UNIX domain sockets, `bind()` fails with `EADDRINUSE` if the file inode exists on disk, even if no process is listening.
   - Therefore, on daemon startup, `_start_socket_server()` must unlink the socket file via `path.unlink(missing_ok=True)` immediately before `_DaemonSocketServer` initialization.
   - On daemon shutdown, the server must call `shutdown()`, `server_close()`, join the listener thread, and unlink the socket file.
   - To guarantee cleanup in all exit modes (signals, idle shutdown, uncaught exceptions, normal termination), `_stop_socket_server()` must be called in `run()`'s `finally:` block and supplemented with an `atexit` registration.

4. **Shutdown Ordering**:
   - If `_tts_worker` were terminated before the socket server, incoming client connections could enqueue items after the worker is dead or after the queue sentinel `None` has been placed.
   - Therefore, shutdown order must be: (1) `_stop_socket_server()`, (2) put `None` into `_tts_queue`, (3) join `tts_thread`, (4) unlink `PID_FILE`, (5) transition `_fsm` to `NOT_RUNNING`.

5. **Wire Protocol & Request Handling**:
   - `speak.py` sends UTF-8 text and closes or shuts down write half (`SHUT_WR`).
   - Observations show payloads can reach 128 KB and abrupt disconnects (`SO_LINGER 0`) can occur.
   - Therefore, `_DaemonRequestHandler` must read in a `while True:` loop accumulating chunks of 4096 bytes until EOF (`b""`), wrapped in `(ConnectionResetError, BrokenPipeError, OSError)` exception handling.
   - Text is decoded with `utf-8` (`errors="replace"`), whitespace-stripped, and if non-empty, passed to `enqueue_text()`.

6. **Queue Concurrency & Drop-Oldest Backpressure**:
   - Socket threads and the main `_tail_events()` thread will write to `_tts_queue` concurrently.
   - Observation 1 shows `_bare_service` in `test_narrator_service.py` skips `__init__`.
   - Therefore, enqueueing must use a shared lock `self._queue_lock`, guarded defensively with `lock = getattr(self, "_queue_lock", None)` to maintain 100% test compatibility with uninitialized bare instances.
   - When the queue is full (`maxsize=32`), the oldest item is dropped via `get_nowait()`, `self._dropped_phases` is incremented, and `self._update_depth()` is called.

7. **Idle Timer Prevention**:
   - Observation 1 shows the daemon auto-shuts down if `time.time() - self._last_event_ts > self._idle_shutdown`.
   - Incoming socket speech requests represent active user operations; therefore, `enqueue_text()` must reset `self._last_event_ts = time.time()`.

---

## 3. Caveats

1. **Client Implementation (`speak.py`)**:
   - This investigation focuses specifically on the daemon socket server in `narrator_service.py` (M2.1). The corresponding CLI client refactoring in `speak.py` (M2.2) is a separate task, though its wire protocol contract is fully specified here.
2. **macOS Socket Path Length Limit**:
   - On Darwin, `sockaddr_un.sun_path` is limited to 104 bytes. The default path `/tmp/auto-speech-daemon.sock` is 29 bytes. Temporary paths generated in test fixtures (`IsolatedEnvironment`) should be monitored if deeply nested paths are ever used.
3. **No Caveats in TTS Worker Core**:
   - `_tts_worker` already supports string dispatch to `self._speak(phase)` without modification.

---

## 4. Conclusion

The daemon socket server integration for Milestone M2.1 is fully designed and verified against all architectural and testing requirements.

The implementer should add:
1. `_DaemonRequestHandler` and `_DaemonSocketServer(socketserver.ThreadingUnixStreamServer)` to `narrator_service.py`.
2. `socket_path` resolution with default `/tmp/auto-speech-daemon.sock` and `AUTO_SPEECH_DAEMON_SOCK` env override.
3. Stale socket unlinking on startup in `_start_socket_server()` before binding.
4. Clean socket shutdown and unlinking in `_stop_socket_server()` invoked in `run()`'s `finally:` block, with `atexit` registration as safety fallback.
5. Thread-safe `enqueue_text()` with drop-oldest queue management, `_queue_lock`, and idle timer reset.
6. Defensive `getattr(self, "_queue_lock", None)` to guarantee existing unit tests pass without regression.

Refer to `analysis.md` in this directory for the exact before/after code snippets and architectural specifications.

---

## 5. Verification Method

1. **Existing Unit Test Suite**:
   ```bash
   PYTHONPATH=. pytest tests/test_narrator_service.py
   ```
   *Expected*: All 21 tests pass with zero regressions.

2. **Tier 1 Feature Tests (Post-implementation of M2.1 & M2.2)**:
   ```bash
   PYTHONPATH=. pytest tests/e2e/test_tier1_features.py -k "test_tier1_r2"
   ```
   *Expected*: `test_tier1_r2_daemon_socket_enqueues_to_tts_queue`, `test_tier1_r2_socket_wire_protocol_stream_handling`, and `test_tier1_r2_daemon_cleans_up_socket_file_on_shutdown` pass.

3. **Tier 2 Boundary Tests**:
   ```bash
   PYTHONPATH=. pytest tests/e2e/test_tier2_boundaries.py -k "test_tier2_r2"
   ```
   *Expected*: All Tier 2 R2 boundary tests pass (empty stdin, special characters, 128 KB payload, abrupt disconnect, stale socket cleanup).

4. **Tier 3 Combination & Concurrency Tests**:
   ```bash
   PYTHONPATH=. pytest tests/e2e/test_tier3_combinations.py -k "socket"
   ```
   *Expected*: `test_tier3_concurrent_socket_and_jsonl_events`, `test_tier3_backpressure_queue_cap_with_socket_burst`, and `test_tier3_multi_client_concurrent_burst` pass.

5. **Invalidation Condition**:
   - If binding fails with `Address already in use`, startup stale unlinking was bypassed or executed after `bind()`.
   - If tests timeout during 10-client burst, `UnixStreamServer` was used instead of `ThreadingUnixStreamServer`.
   - If `tests/test_narrator_service.py` fails with `AttributeError`, `_queue_lock` was accessed directly without `getattr` fallback.
