# Handoff Report: Challenger M2.2 (Empirical Stress Testing of Socket Lifecycle, Backpressure, and Concurrency)

**Verdict**: **`REJECT`**

---

## 1. Observation

### Observation 1: Daemon Lifecycle and Ungraceful Crash Recovery (PASSED)
- **Files**:
  - `plugin/scripts/python/narrator_service.py:215-224` (`_DaemonSocketServer.server_bind()`)
  - `plugin/scripts/python/narrator_service.py:333-365` (`NarratorService._start_socket_server()`)
  - `plugin/scripts/python/narrator_service.py:366-400` (`NarratorService._stop_socket_server()`)
- **Empirical Test**:
  Simulated ungraceful crash by spawning a daemon child process, verifying initial speech request processing, issuing `SIGKILL` (`kill -9`), confirming stale socket `/tmp/.../daemon_test.sock` remained on disk, confirming client received graceful `ConnectionRefusedError` (exit code 1), and then starting a new `NarratorService` instance on the exact same socket path. Repeated across 5 consecutive ungraceful crash-kill-restart cycles, as well as against corrupted regular files and dangling symlinks.
- **Test Executed**:
  `.venv/bin/python -m unittest tests.test_socket_server_stress.TestSocketServerLifecycleAndRecovery`
- **Verbatim Output**:
  ```
  test_repeated_ungraceful_kill_rebind_cycles (__main__.TestSocketServerLifecycleAndRecovery.test_repeated_ungraceful_kill_rebind_cycles)
  Run 5 consecutive iterations of crash-kill-restart cycles. ... ok
  test_stale_corrupted_or_non_socket_file_reclaimed (__main__.TestSocketServerLifecycleAndRecovery.test_stale_corrupted_or_non_socket_file_reclaimed)
  Verifies startup reclaims non-socket files (regular files, symlinks) occupying the path. ... ok
  test_ungraceful_sigkill_crash_recovers_stale_socket (__main__.TestSocketServerLifecycleAndRecovery.test_ungraceful_sigkill_crash_recovers_stale_socket)
  Simulate an ungraceful crash (kill -9) leaving stale socket on disk. ... ok
  ----------------------------------------------------------------------
  Ran 3 tests in 2.381s
  OK
  ```
- **Finding**:
  Ungraceful crash recovery is robust. The server reclaims stale sockets, unlinks them cleanly, avoids `OSError: [Errno 48] Address already in use`, and accepts subsequent requests.

---

### Observation 2: Queue Backpressure Under 200+ Socket Flood (PASSED for Drop-Oldest; DEFECT FOUND for Burst Concurrency)

#### 2A. Drop-Oldest FIFO Cap Under 250-Request Sequential Burst (PASSED)
- **File**: `plugin/scripts/python/narrator_service.py:600-644` (`_enqueue_phase`, `_enqueue_item`)
- **Empirical Test**:
  With `_max_queue = 32` and audio playback held busy, blasted 250 sequential socket requests (`burst_msg_0000` through `burst_msg_0249`).
- **Verbatim Output**:
  ```
  Total sent: 250
  Queue size: 32 (never exceeded 32)
  Dropped phases: 218 (250 - 32)
  Queue survivors: 32 items
  First survivor in queue: burst_msg_0218
  Last survivor in queue: burst_msg_0249
  Survivor ordering: Strictly ascending FIFO
  RSS memory delta: < 2.0 MB (no leak)
  ```
- **Finding**:
  The bounded queue cap (32 items) and drop-oldest shedding logic under `_queue_lock` function as designed under paced sequential traffic. Memory remains stable.

#### 2B. Listen Backlog Starvation Under High-Speed Flood / Concurrent Burst (CRITICAL DEFECT)
- **Files**:
  - `plugin/scripts/python/narrator_service.py:199-224` (`_DaemonSocketServer`)
  - `plugin/scripts/python/speak.py:39-55` (`send_speech_request`)
- **Defect Description**:
  `_DaemonSocketServer` inherits from `socketserver.ThreadingUnixStreamServer` without overriding `request_queue_size`. The standard library defaults `request_queue_size = 5`.
  When a burst of requests arrives without artificial pacing or when multiple client connections connect concurrently (e.g. 25 client threads in a `ThreadPoolExecutor`), macOS kernel immediately drops incoming connections exceeding the backlog of 5.
- **Empirical Execution**:
  Ran 250 socket requests via 25 concurrent threads:
  ```
  Total requests sent: 250
  Errors count: 168 (ConnectionRefusedError: [Errno 61] Connection refused)
  Queue size: 32
  Dropped phases: 50
  Sum of queue size + dropped phases: 82 (168 requests permanently lost at client connect)
  ```
  Ran 250 sequential requests in a tight loop without delays:
  ```
  Sequential burst of 250 in 0.048s:
  Errors count: 15 (ConnectionRefusedError: [Errno 61] Connection refused)
  Queue size + dropped: 235 (15 requests permanently lost)
  ```
- **Root Cause Proof**:
  Setting `_DaemonSocketServer.request_queue_size = 128` (or `SOMAXCONN`) immediately resolved the defect, achieving 250/250 requests processed with 0 errors:
  ```
  Sequential burst of 250 in 0.029s with request_queue_size=128:
  Errors count: 0
  Queue size: 32
  Dropped phases: 218
  Sum: 250 (100% processed)
  ```
  Additionally, `speak.py` does not perform retries with backoff on `ConnectionRefusedError`, failing immediately on transient queue saturation.

---

### Observation 3: Concurrency Between Socket Requests and JSONL Tool Events (PASSED)
- **Files**:
  - `plugin/scripts/python/narrator_service.py:402-411` (`enqueue_text`)
  - `plugin/scripts/python/narrator_service.py:413-465` (`_tail_events`)
  - `plugin/scripts/python/narrator_service.py:698-741` (`_tts_worker`)
- **Empirical Test**:
  Simultaneously blasted 40 socket speech requests via UNIX domain socket and 40 JSONL `PostToolUse` events written to `EVENTS_LOG`, while `_tail_events` and `_tts_worker` operated in background threads. Also tested mixed `Phase` and `str` queue shedding under saturated backpressure.
- **Test Executed**:
  `.venv/bin/python -m unittest tests.test_socket_server_stress.TestSimultaneousSocketAndJsonlEvents`
- **Verbatim Output**:
  ```
  test_mixed_phase_and_string_drop_oldest_under_backpressure (__main__.TestSimultaneousSocketAndJsonlEvents.test_mixed_phase_and_string_drop_oldest_under_backpressure)
  Verify queue sheds mixed Phase and str items under backpressure without crashing. ... ok
  test_simultaneous_socket_and_jsonl_event_ingestion (__main__.TestSimultaneousSocketAndJsonlEvents.test_simultaneous_socket_and_jsonl_event_ingestion)
  Feed 40 socket requests and 40 JSONL events concurrently. ... ok
  ----------------------------------------------------------------------
  Ran 2 tests in 0.485s
  OK
  ```
- **Detailed Ingestion Metric**:
  - Socket requests received: 40 / 40
  - JSONL event summaries received: 40 / 40
  - Total utterances synthesized: 80 / 80 (0 lost, 0 race conditions, 0 deadlocks)
  - Mixed-type drop-oldest shedding: Verified that `_enqueue_item` correctly drops both `Phase` objects and `str` items without throwing `AttributeError` when accessing category attributes.

---

## 2. Logic Chain

1. **Premise 1**: The assignment mandates stress-testing queue backpressure under socket flood (200+ request burst), server lifecycle crash recovery, and simultaneous socket + JSONL events.
2. **Premise 2**: As established in Observation 1, ungraceful crash recovery (`SIGKILL`) succeeds 100%: the daemon reclaims stale socket files, corrupted regular files, and dangling symlinks without `Address already in use` errors.
3. **Premise 3**: As established in Observation 3, simultaneous socket requests and JSONL tool events are ingested in a thread-safe manner without race conditions, and mixed-type drop-oldest shedding functions correctly.
4. **Premise 4**: As established in Observation 2A, the drop-oldest FIFO cap (32 items) is strictly enforced under backpressure, correctly discarding the oldest items and preserving the newest 32 items.
5. **Premise 5**: As established in Observation 2B, `_DaemonSocketServer` defaults to `request_queue_size = 5`.
6. **Premise 6**: When 200+ requests are sent at high frequency or under concurrency (25 threads), the kernel socket backlog of 5 overflows immediately on macOS, causing `connect()` to fail with `[Errno 61] Connection refused`.
7. **Premise 7**: `speak.py` does not retry on `ConnectionRefusedError`, causing up to 67% (168/250) of speech requests to be permanently dropped at the CLI entry point.
8. **Premise 8**: Corroborating peer challenger `challenger_m2_1` (Observation 1.2 in `challenger_m2_1/handoff.md`), abrupt client resets mid-stream are currently enqueued as truncated speech rather than discarded due to swallowed socket exceptions in `_DaemonRequestHandler.handle()`.
9. **Conclusion**: Because burst requests and concurrent client connections drop valid speech requests due to insufficient socket backlog, Milestone M2 must be **REJECTED** until backlog sizing and client-side retry resilience are remediated.

---

## 3. Caveats

- **Operating System Environment**: Testing was conducted on macOS (Darwin arm64). Linux kernel socket queueing behavior can differ depending on `tcp_abort_on_overflow` and `somaxconn`, but macOS is the target deployment environment for `auto-speech`.
- **In-Process Engine Testing**: Synthesizer and AudioSink interactions during 250-request blasts were verified using deterministic mock test doubles (`FakeAudioSink`, `FakeSynthesizer`) to avoid multi-minute audio synthesis delays and host speaker pollution.
- **Review-Only Constraint Honored**: Zero implementation files (`plugin/scripts/python/*.py`) were modified. All tests were executed via the test suite `tests/test_socket_server_stress.py`.

---

## 4. Conclusion

**Verdict**: **`REJECT`**

While ungraceful crash recovery, drop-oldest FIFO cap enforcement, and simultaneous socket + JSONL event ingestion perform well, Milestone M2 fails under high-frequency socket bursts and concurrency due to kernel listen backlog starvation.

### Concrete Remediations for Worker:
1. In `plugin/scripts/python/narrator_service.py` (`_DaemonSocketServer`):
   ```python
   class _DaemonSocketServer(socketserver.ThreadingUnixStreamServer):
       address_family = socket.AF_UNIX
       daemon_threads = True
       allow_reuse_address = True
       request_queue_size = 128  # Fix listen backlog bottleneck (default is 5)
   ```
2. In `plugin/scripts/python/speak.py` (`send_speech_request`):
   Add a retry loop for `ConnectionRefusedError` (e.g. 3 attempts with 0.02s backoff) to gracefully absorb transient burst spikes.
3. In `plugin/scripts/python/narrator_service.py` (`_DaemonRequestHandler.handle`):
   Set an `aborted` flag if an exception occurs during `recv()`, and do not enqueue truncated text if the stream was aborted mid-transmission.
   Add `self.request.settimeout(5.0)` to protect worker threads from stalled connections.

---

## 5. Verification Method

To independently verify all findings in this report:

1. **Run the full socket server stress test suite**:
   ```bash
   .venv/bin/python -m unittest tests.test_socket_server_stress
   ```
   *Expected output*: 7 tests run, 7 pass cleanly in ~4.6 seconds.

2. **Verify listen backlog defect reproduction**:
   ```bash
   .venv/bin/python -c '
   import socket, tempfile, threading, queue, narrator_service, time
   from concurrent.futures import ThreadPoolExecutor, as_completed
   from pathlib import Path
   from speak import send_speech_request

   with tempfile.TemporaryDirectory() as d:
       sock = Path(d) / "flood.sock"
       svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
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
       try:
           errors = []
           with ThreadPoolExecutor(max_workers=25) as pool:
               futures = [pool.submit(send_speech_request, f"req_{i}", socket_path=sock) for i in range(250)]
               for f in as_completed(futures):
                   if f.result() != 0:
                       errors.append(1)
           print(f"Failed connections under backlog=5: {len(errors)} / 250")
           assert len(errors) > 50, "Expected significant connection failures"
       finally:
           svc._stop_socket_server()
   '
   ```

3. **Verify linting compliance**:
   ```bash
   .venv/bin/ruff check tests/test_socket_server_stress.py
   ```
