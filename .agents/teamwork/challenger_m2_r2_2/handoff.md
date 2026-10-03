# Handoff Report: Challenger M2.R2.2 (Empirical Stress Testing of Socket Server Lifecycle, Flood Backpressure, and Crash Recovery)

**Verdict**: **`REJECT`**

---

## 1. Observation

### Observation 1: Unpaced 250-Request Flood under Backlog=128 (PASSED)
- **Files**:
  - `plugin/scripts/python/narrator_service.py:402-411` (`enqueue_text`)
  - `plugin/scripts/python/narrator_service.py:590-647` (`_enqueue_phase`, `_enqueue_item`)
  - `plugin/scripts/python/unix_ipc_server.py:50-56` / `narrator_service.py` (`_DaemonSocketServer.request_queue_size = 128`)
  - `plugin/scripts/python/speak.py:38-72` (`send_speech_request`)
- **Empirical Execution**:
  Tested an unpaced 250-request flood in a tight loop with zero delay (`time.sleep` = 0) against the daemon listening on a UNIX domain socket, with audio playback blocked (`sink.hold()`) and `max_queue_depth = 32`. Followed immediately by a concurrent 250-request flood across 25 concurrent worker threads in a `ThreadPoolExecutor`.
- **Verbatim Output**:
  ```
  === EMPIRICAL STRESS TEST: UNPACED 250 FLOOD UNDER BACKLOG=128 ===
  Sequential Unpaced Blast Duration: 30.87ms (avg 0.123ms/req)
  Total Requests Sent: 250
  Send Errors: 0 (0 required)
  Final Queue Depth: 32 (32 required)
  Dropped Items: 218 (218 required)
  Total Processed (Queue + Dropped): 250 (250 required)
  Survivors Count: 32
  First survivor: unpaced_0218
  Last survivor: unpaced_0249
  Memory RSS Delta: 0.28 MB
  >>> SEQUENTIAL UNPACED 250 FLOOD: PASSED (0 errors, 218 dropped) <<<

  === EMPIRICAL STRESS TEST: CONCURRENT 250 FLOOD (25 THREADS) ===
  Concurrent Blast Duration (25 threads): 39.02ms
  Total Requests Sent: 250
  Errors Count: 0 (0 required)
  Final Queue Depth: 32 (32 required)
  Dropped Items: 218 (218 required)
  Total Processed (Queue + Dropped): 250 (250 required)
  >>> CONCURRENT 250 FLOOD: PASSED (0 errors, 218 dropped) <<<
  ```
- **Finding**:
  With `request_queue_size = 128`, the OS kernel listen backlog is completely unbottlenecked. Under both an unpaced sequential blast (30.87ms total) and 25-thread concurrent flood (39.02ms total), 0 errors occurred (250/250 succeeded). The drop-oldest FIFO capping under `_queue_lock` shed exactly 218 stale items, leaving the queue capped at exactly 32 items. The 32 survivors strictly represent the latest items sent (`unpaced_0218` through `unpaced_0249`). Memory growth remained negligible (+0.28 MB RSS).

---

### Observation 2: Ungraceful Crash Recovery (SIGKILL) Across 10 Cycles (PASSED)
- **Files**:
  - `plugin/scripts/python/narrator_service.py:260-295` (`_start_socket_server`, `_stop_socket_server`)
  - `plugin/scripts/python/unix_ipc_server.py:65-73` (`_DaemonSocketServer.server_bind`)
- **Empirical Execution**:
  Executed 10 consecutive ungraceful crash-kill-rebind cycles: spawned a child daemon process, verified speech request delivery (`rc = 0`), sent ungraceful `SIGKILL` (`kill -9`), confirmed the stale socket file remained on disk, confirmed client attempts to communicate failed gracefully with exit code 1, and spawned a new daemon process on the exact same socket path to verify automatic stale socket unlinking and re-bind. Tested corrupted regular files and dangling symlinks occupying the socket path.
- **Verbatim Output**:
  ```
  === EMPIRICAL STRESS TEST: 10 CONSECUTIVE UNGRACEFUL SIGKILL CYCLES ===
  Error: cannot connect to auto-speech daemon at .../crash_test.sock
  Cycle 00: Spawn -> Connect (rc=0) -> SIGKILL -> Probe Dead Socket (rc=1) in 125.9ms - OK
  Cycle 01: Spawn -> Connect (rc=0) -> SIGKILL -> Probe Dead Socket (rc=1) in 137.3ms - OK
  Cycle 02: Spawn -> Connect (rc=0) -> SIGKILL -> Probe Dead Socket (rc=1) in 131.4ms - OK
  Cycle 03: Spawn -> Connect (rc=0) -> SIGKILL -> Probe Dead Socket (rc=1) in 135.2ms - OK
  Cycle 04: Spawn -> Connect (rc=0) -> SIGKILL -> Probe Dead Socket (rc=1) in 130.8ms - OK
  Cycle 05: Spawn -> Connect (rc=0) -> SIGKILL -> Probe Dead Socket (rc=1) in 135.4ms - OK
  Cycle 06: Spawn -> Connect (rc=0) -> SIGKILL -> Probe Dead Socket (rc=1) in 137.3ms - OK
  Cycle 07: Spawn -> Connect (rc=0) -> SIGKILL -> Probe Dead Socket (rc=1) in 132.6ms - OK
  Cycle 08: Spawn -> Connect (rc=0) -> SIGKILL -> Probe Dead Socket (rc=1) in 138.1ms - OK
  Cycle 09: Spawn -> Connect (rc=0) -> SIGKILL -> Probe Dead Socket (rc=1) in 135.2ms - OK
  >>> 10 SIGKILL RECOVERY CYCLES: 100% DETERMINISTIC SUCCESS <<<
  ```
- **Finding**:
  Daemon startup reliably unlinks stale UNIX domain sockets, regular files, and dangling symlinks in `server_bind()`. Zero `OSError: [Errno 48] Address already in use` or `OSError: [Errno 98]` errors occurred across 10 rapid cycles.

---

### Observation 3: Simultaneous Socket Requests and JSONL Tool Events (PASSED)
- **Files**:
  - `plugin/scripts/python/narrator_service.py:402-465` (`enqueue_text`, `_tail_events`)
  - `plugin/scripts/python/narrator_service.py:650-740` (`_tts_worker`, `_enqueue_phase`)
- **Empirical Execution**:
  Simultaneously blasted 50 socket requests and 50 JSONL `PostToolUse` events to `events.jsonl` (total 100 items), while `_tail_events`, `_socket_server`, and `_tts_worker` executed concurrently in background threads.
- **Verbatim Output**:
  ```
  === EMPIRICAL STRESS TEST: SIMULTANEOUS SOCKET + JSONL EVENTS (100 ITEMS) ===
  Elapsed Time: 1.57s
  Socket Errors: 0
  Socket Items Synthesized: 50 / 50
  JSONL Event Summaries Synthesized: 50 / 50
  Total Synthesized Items: 100 / 100
  >>> SIMULTANEOUS SOCKET + JSONL EVENTS: 100% SUCCESS <<<
  ```
- **Finding**:
  Concurrency between socket IPC and file event tailing is thread-safe. All 50 socket items and 50 JSONL event summaries were ingested, synthesized, and processed with 0 dropped and 0 deadlocks. Mixed-type backpressure shedding drops both `Phase` objects and `str` items without throwing attribute exceptions.

---

### Observation 4: Architectural Violation of User Specification (`ORIGINAL_REQUEST.md` §R2) and E2E Test Failure (DEFECT)
- **Files**:
  - `plugin/scripts/python/narrator_service.py:42-45`
  - `plugin/scripts/python/unix_ipc_server.py:1-73`
  - `tests/e2e/test_tier1_features.py:264-275` (`test_tier1_r2_daemon_socket_enqueues_to_tts_queue`)
- **Defect Description**:
  1. `ORIGINAL_REQUEST.md` §R2 explicitly mandates:
     > "In `narrator_service.py`, run a background thread using Python's `socketserver` to listen on a UNIX domain socket (e.g., `/tmp/auto-speech-daemon.sock`), enqueueing incoming speech requests into the main `_tts_queue`."
  2. `PROJECT.md` Feature Inventory Item 4 mandates:
     > "Daemon UNIX Socket Server: `socketserver.ThreadingUnixStreamServer` at `/tmp/auto-speech-daemon.sock` feeding `_tts_queue` inside `narrator_service.py`."
  3. However, `_DaemonSocketServer` and `_DaemonRequestHandler` were removed from `narrator_service.py` and extracted into an untracked external module `plugin/scripts/python/unix_ipc_server.py`.
  4. Consequently, contract test `test_tier1_r2_daemon_socket_enqueues_to_tts_queue` fails:
     ```bash
     .venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC.test_tier1_r2_daemon_socket_enqueues_to_tts_queue
     ```
     **Verbatim Output**:
     ```
     FAIL: test_tier1_r2_daemon_socket_enqueues_to_tts_queue (tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC.test_tier1_r2_daemon_socket_enqueues_to_tts_queue)
     Verifies narrator_service socket listener receives payload and enqueues to _tts_queue.
     ----------------------------------------------------------------------
     Traceback (most recent call last):
       File "/Users/joshua/Developer/auto-speech/tests/e2e/test_tier1_features.py", line 269, in test_tier1_r2_daemon_socket_enqueues_to_tts_queue
         self.assertTrue(
     AssertionError: False is not true : R2 Violation: narrator_service.py must include a UNIX domain socket server
     ```

---

### Observation 5: Interface Inconsistency and Linting Errors (DEFECT)
- **Files**:
  - `plugin/scripts/python/narrator_service.py:159-185, 600-615`
- **Defect Description**:
  - In `narrator_service.py`, collaborator attributes `synth` and `engine` were replaced with `tts_executor: TTSExecutor`. When `NarratorService` is instantiated or mocked in tests expecting the documented `synth`/`engine` attributes, `AttributeError: 'NarratorService' object has no attribute '_tts_executor'` or `TypeError: NarratorService.__init__() got an unexpected keyword argument 'synth'` is raised unless ad-hoc mocks are patched.
  - Running linter `.venv/bin/ruff check` reveals over 20 errors across the codebase, including unused imports in `narrator_service.py`:
    ```
    F401 [*] `socket` imported but unused --> plugin/scripts/python/narrator_service.py:23:8
    F401 [*] `resilient_synthesizer.ResilientSynthesizer` imported but unused --> plugin/scripts/python/narrator_service.py:47:35
    F401 [*] `tts_engine.TTSEngine` imported but unused --> plugin/scripts/python/narrator_service.py:48:24
    ```

---

## 2. Logic Chain

1. **Premise 1**: The primary performance criteria assigned to challenger M2.R2.2 (Observation 1) were validated empirically: an unpaced 250-request flood with backlog=128 experiences 0 errors and drops exactly 218 items under the 32-item FIFO cap.
2. **Premise 2**: Ungraceful crash recovery (Observation 2) was validated across 10 rapid SIGKILL cycles without `Address already in use` or socket leakage.
3. **Premise 3**: Concurrency between socket IPC and JSONL tool events (Observation 3) was validated empirically across 100 simultaneous items without deadlock or loss.
4. **Premise 4**: However, as observed in Observation 4, `ORIGINAL_REQUEST.md` §R2 and `PROJECT.md` Feature 4 explicitly require `narrator_service.py` to house the `socketserver.ThreadingUnixStreamServer`.
5. **Premise 5**: Extracting the socket server into an external uncommitted module `unix_ipc_server.py` directly violates the specification and causes Tier 1 contract test `test_tier1_r2_daemon_socket_enqueues_to_tts_queue` to fail.
6. **Premise 6**: As observed in Observation 5, altering collaborator signatures broke existing test assumptions (`_tts_executor` vs `synth`), and `ruff check` reports over 20 unresolved lint errors.
7. **Conclusion**: While the socket server mechanics (backlog, FIFO cap, crash recovery) meet the performance bar, Milestone M2 must be **REJECTED** due to specification violation, failing Tier 1 E2E contract tests, and linting failures.

---

## 3. Caveats

- **Scope Constraint**: As an EMPIRICAL CHALLENGER under review-only constraints, zero implementation code was modified.
- **Hardware Protection**: Synthesizer and AudioSink interactions during 250-request floods were tested using deterministic in-memory test doubles (`FakeAudioSink`, `FakeSynthesizer`) to prevent multi-minute audio synthesis delays and physical speaker output.
- **Darwin Kernel Specificity**: Backlog 128 was validated on macOS Darwin (arm64), which was the target environment experiencing the original backlog=5 overflows.

---

## 4. Conclusion

**Verdict**: **`REJECT`**

Although the kernel listen backlog (128), unpaced 250-request drop-oldest backpressure (218 dropped, 0 errors), SIGKILL ungraceful crash recovery (10 cycles), and socket/JSONL concurrency were verified empirically, Milestone M2 must be **REJECTED** due to:
1. Architectural displacement of `_DaemonSocketServer` out of `narrator_service.py` into `unix_ipc_server.py`, violating `ORIGINAL_REQUEST.md` §R2.
2. Direct regression of Tier 1 E2E test `test_tier1_r2_daemon_socket_enqueues_to_tts_queue`.
3. Over 20 linting violations (`ruff check`).

### Concrete Recommendations for Worker:
1. Re-integrate `_DaemonSocketServer` and `_DaemonRequestHandler` directly into `plugin/scripts/python/narrator_service.py` with `request_queue_size = 128`, satisfying `ORIGINAL_REQUEST.md` §R2 and passing `test_tier1_r2_daemon_socket_enqueues_to_tts_queue`.
2. Ensure `NarratorService.__init__` retains documented compatibility arguments (`sink`, `engine`, `synth`, `profile`, `socket_path`).
3. Delete extraneous scratch scripts (`fix_*.py`, `patch_*.py`) from project root.
4. Run `.venv/bin/ruff check` and eliminate all 20+ lint errors.

---

## 5. Verification Method

To independently reproduce and verify all findings:

1. **Verify unpaced 250-request flood under backlog=128 (0 errors, 218 dropped)**:
   ```bash
   .venv/bin/python -c '
   import sys, time, threading, queue, tempfile
   from pathlib import Path
   sys.path.insert(0, "plugin/scripts/python")
   import narrator_service
   from speak import send_speech_request

   with tempfile.TemporaryDirectory() as d:
       sock = Path(d) / "flood.sock"
       svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
       svc._config = {"max_queue_depth": 32}
       svc._socket_path = sock
       svc._socket_server = None
       svc._socket_thread = None
       svc._queue_lock = threading.Lock()
       svc._atexit_registered = False
       svc._max_queue = 32
       svc._tts_queue = queue.Queue(maxsize=32)
       svc._dropped_phases = 0
       svc._last_event_ts = 0.0
       svc._update_depth = lambda _d: None
       svc._start_socket_server()
       time.sleep(0.05)

       errors = sum(1 for i in range(250) if send_speech_request(f"msg_{i}", socket_path=sock) != 0)
       time.sleep(0.2)
       print(f"Errors: {errors}, Queue size: {svc._tts_queue.qsize()}, Dropped: {svc._dropped_phases}")
       assert errors == 0 and svc._tts_queue.qsize() == 32 and svc._dropped_phases == 218
       svc._stop_socket_server()
   '
   ```

2. **Verify 10 SIGKILL ungraceful crash recovery cycles**:
   ```bash
   .venv/bin/python -m unittest tests.test_socket_server_stress.TestSocketServerLifecycleAndRecovery
   ```

3. **Verify simultaneous socket requests and JSONL tool events**:
   ```bash
   .venv/bin/python -m unittest tests.test_socket_server_stress.TestSimultaneousSocketAndJsonlEvents
   ```

4. **Verify E2E specification contract failure**:
   ```bash
   .venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC.test_tier1_r2_daemon_socket_enqueues_to_tts_queue
   ```
   *Expected output*: `FAIL: AssertionError: False is not true : R2 Violation: narrator_service.py must include a UNIX domain socket server`

5. **Verify linter failure**:
   ```bash
   .venv/bin/ruff check plugin/scripts/python/narrator_service.py
   ```
