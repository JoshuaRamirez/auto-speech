# Handoff Report: Challenger M2.1 (Empirical Socket IPC Stress Testing)

**Verdict**: **`REJECT`**

---

## Challenge Summary

**Overall risk assessment**: **`HIGH`**

### Challenges Overview
1. **[Critical] Listen Backlog Exhaustion Under High Concurrency**:
   - *Target*: `plugin/scripts/python/narrator_service.py:199-224` (`_DaemonSocketServer`) & `plugin/scripts/python/speak.py:48-53` (`send_speech_request`).
   - *Attack scenario*: 50 concurrent client connections connect simultaneously via threading barrier or simultaneous CLI processes.
   - *Blast radius*: 70% to 86% of speech requests fail immediately with `[Errno 61] Connection refused` and are permanently lost.
   - *Mitigation*: Set `request_queue_size = 128` (or `SOMAXCONN`) on `_DaemonSocketServer`, and add retry/backoff logic in `speak.send_speech_request()`.

2. **[High] Abrupt Socket Disconnect Truncation Enqueue Bug**:
   - *Target*: `plugin/scripts/python/narrator_service.py:166-185` (`_DaemonRequestHandler.handle()`).
   - *Attack scenario*: Client disconnects abruptly mid-transmission (`SO_LINGER 0`, RST, or network drop).
   - *Blast radius*: Instead of discarding the aborted request, `_DaemonRequestHandler` continues execution and enqueues truncated/corrupted text into `_tts_queue`, causing incomplete phrases to be synthesized and spoken.
   - *Mitigation*: Flag connection as aborted upon `ConnectionResetError` / `BrokenPipeError` / `OSError` in `handle()` and return immediately without enqueuing.

3. **[Medium] Slowloris / Indefinite Thread Hang Vulnerability**:
   - *Target*: `plugin/scripts/python/narrator_service.py:168-175` (`_DaemonRequestHandler.handle()`).
   - *Attack scenario*: Client connects and holds connection open without sending data or closing.
   - *Blast radius*: `self.request.recv(4096)` blocks indefinitely because no socket timeout is configured. Server worker thread is pinned permanently, leaking threads under slow/stalled clients.
   - *Mitigation*: Set `self.request.settimeout(5.0)` or similar read timeout on accepted client sockets.

---

## 1. Observation

### Observation 1.1: Concurrency Failure Under Default Listen Backlog
- **File**: `plugin/scripts/python/narrator_service.py:199`
  ```python
  class _DaemonSocketServer(socketserver.ThreadingUnixStreamServer):
      """Multi-threaded UNIX domain stream socket server for daemon IPC."""

      address_family = socket.AF_UNIX
      daemon_threads = True
      allow_reuse_address = True
  ```
  `_DaemonSocketServer` does not define `request_queue_size`. In Python's standard library `socketserver.TCPServer`, `request_queue_size = 5`.
- **Command Executed**:
  `PYTHONPATH=plugin/scripts/python .venv/bin/python tests/test_socket_ipc_stress.py TestSocketIPCStress.test_high_concurrency_default_backlog_bottleneck`
- **Verbatim Output**:
  ```
  [Defect 1: Concurrency Backlog 5] Successes: 7/50, Failures: 43/50
  Sample errors: [(10, 'ConnectionRefusedError', '[Errno 61] Connection refused'), ...]
  ```
  43 out of 50 client threads failed. Only 7 succeeded and were enqueued.
- **Process-Level Concurrency Command**:
  Launching 50 concurrent `plugin/scripts/python/speak.py` subprocesses:
  ```
  50 process clients: Exit 0 count=34, Non-zero=16
  Sample failure stderr: Error: cannot connect to auto-speech daemon at /tmp/test_process_concurrency.sock
  ```
  16 out of 50 CLI processes failed with exit code 1.
- **Root Cause Validation**:
  When `_DaemonSocketServer.request_queue_size = 128` was applied, 60 out of 60 concurrent clients succeeded with 0 errors (`Successes: 60/60, Failures: 0/60`, queue size: 60).

### Observation 1.2: Abrupt Disconnect Enqueues Truncated Utterance
- **File**: `plugin/scripts/python/narrator_service.py:166-192`
  ```python
  def handle(self) -> None:
      chunks: list[bytes] = []
      while True:
          try:
              data = self.request.recv(4096)
              if not data:
                  break
              chunks.append(data)
          except (ConnectionResetError, BrokenPipeError, OSError):
              break

      if not chunks:
          return

      try:
          text = b"".join(chunks).decode("utf-8", errors="replace")
      except Exception:
          return

      cleaned = text.strip()
      if not cleaned:
          return

      service: NarratorService | None = getattr(self.server, "service", None)
      if service is not None:
          service.enqueue_text(cleaned)
  ```
- **Command Executed**:
  `PYTHONPATH=plugin/scripts/python .venv/bin/python tests/test_socket_ipc_stress.py TestSocketIPCStress.test_abrupt_disconnect_erroneously_enqueues_truncated_payload`
- **Verbatim Output**:
  ```
  [Defect 2: Abrupt Disconnect Enqueue] Enqueued items count: 1
  [Defect 2: Abrupt Disconnect Enqueue] Enqueued item: 'This is a partial sentence that was aborted mid-stream by'
  ```
  When the client sent partial bytes and aborted via `SO_LINGER 0` (RST), `_DaemonRequestHandler` caught `ConnectionResetError`, broke from the loop, decoded the partial chunks, and enqueued the incomplete sentence into `_tts_queue`.

### Observation 1.3: Robust Performance in Boundary Payloads and Latency
- **Command Executed**:
  `PYTHONPATH=plugin/scripts/python .venv/bin/python tests/test_socket_ipc_stress.py`
- **Verbatim Results**:
  ```
  [256KB+ Payload] Transmitted 262416 bytes in 1.58ms (100% matched after strip)
  [Multi-MB Payload] 1MB transmitted in 2.66ms
  [Multi-MB Payload] 5MB transmitted in 10.93ms
  [Backpressure Stress] Capped at 32, dropped 18 items cleanly
  [Latency Benchmark] Min: 0.019ms, Median: 0.100ms, p95: 0.186ms, p99: 1.502ms, Max: 1.526ms
  ```
  - 256KB+, 1MB, and 5MB payloads stream cleanly without truncation or deadlocks.
  - Emojis (ZWJ sequences, skin tones), CJK, RTL (Arabic/Hebrew), mathematical symbols, and shell metacharacters transmit with full UTF-8 fidelity.
  - Empty and whitespace-only payloads correctly short-circuit and enqueue 0 items.
  - Client roundtrip latency easily satisfies the <20ms target (p99 is 1.502ms; median is 0.100ms).
  - Server thread does not crash and does not leak threads upon client disconnects.

---

## 2. Logic Chain

1. **Step 1**: The task assignment specifically mandated: *"Stress test high concurrency: spawn 50+ concurrent client connections sending text simultaneously. Verify all valid text is enqueued, no deadlocks occur, and all client sockets close cleanly."* (DISPATCH.md §1).
2. **Step 2**: From Observation 1.1, `_DaemonSocketServer` relies on the default `socketserver.TCPServer.request_queue_size = 5`.
3. **Step 3**: On macOS/Darwin, when 50 concurrent client connections hit a UNIX domain socket with a listen backlog of 5, the OS kernel listen queue overflows instantly, returning `[Errno 61] Connection refused`.
4. **Step 4**: `speak.py` does not perform retries on `ConnectionRefusedError`. Consequently, 40 to 43 out of 50 client threads (and 16 out of 50 processes) fail immediately with exit code 1, dropping their speech requests.
5. **Step 5**: Therefore, Milestone M2 fails the high-concurrency requirement.
6. **Step 6**: From Observation 1.2, `_DaemonRequestHandler` swallows `ConnectionResetError` / `BrokenPipeError` on `recv()` without setting an abort flag. It proceeds to decode and enqueue partial byte fragments into `_tts_queue`, corrupting the narration stream with truncated utterances whenever a client aborts.
7. **Step 7**: Therefore, the socket server fails robust handling of abrupt disconnections.
8. **Conclusion**: Because valid text is dropped under concurrent load and truncated fragments are enqueued on aborted connections, Milestone M2 must be **REJECTED** until these defects are remediated.

---

## 3. Caveats

- **OS Specificity**: The listen backlog behavior was verified on macOS (Darwin arm64). Linux kernel defaults (`somaxconn` and `tcp_abort_on_overflow`) may exhibit different backlog queue drop vs syn-retry dynamics, but Darwin is the primary deployment environment for this system.
- **Worker Lockup Not Observed**: The server did not deadlock under backpressure or large payloads; the `_queue_lock` in `NarratorService` performed correctly.
- **No Source Code Modified**: As an EMPIRICAL CHALLENGER under `Review-only` constraint, zero implementation files were modified. The test harness was committed to `tests/test_socket_ipc_stress.py`.

---

## 4. Conclusion

**Verdict**: **`REJECT`**

Milestone M2 cannot be approved due to two confirmed defects:
1. **Critical Concurrency Defect**: `_DaemonSocketServer` drops 70-86% of concurrent speech requests under 50 simultaneous clients due to default `request_queue_size = 5`.
2. **Abrupt Disconnect Truncation Defect**: Aborted connections mid-stream are enqueued as partial speech rather than discarded.

### Required Remediations for Worker:
1. In `plugin/scripts/python/narrator_service.py`:
   - Set `request_queue_size = 128` (or `64`) on `_DaemonSocketServer`.
   - In `_DaemonRequestHandler.handle()`, record whether an exception occurred during `recv()`. If aborted via socket error before clean EOF (`data == b""`), do NOT enqueue `chunks`.
   - In `_DaemonRequestHandler.handle()`, set `self.request.settimeout(5.0)` to protect against slowloris thread starvation.
2. In `plugin/scripts/python/speak.py`:
   - Add a lightweight retry loop (e.g. 3 attempts with 0.02s backoff) for `ConnectionRefusedError` in `send_speech_request()`.

---

## 5. Verification Method

To independently verify these empirical results:

1. **Run the full empirical stress test suite**:
   ```bash
   PYTHONPATH=plugin/scripts/python .venv/bin/python tests/test_socket_ipc_stress.py
   ```
   *Expected output*:
   - `test_high_concurrency_default_backlog_bottleneck`: Confirms failure rate of ~70-90% under default backlog 5.
   - `test_high_concurrency_adequate_backlog_resolution`: Confirms 100% pass (60/60) when backlog is 128.
   - `test_abrupt_disconnect_erroneously_enqueues_truncated_payload`: Confirms truncated payload is enqueued upon RST abort.
   - `test_boundary_payload_*`: Confirms 256KB+, 1MB, 5MB, and Unicode pass.
   - `test_latency_benchmark_under_20ms`: Confirms p99 < 2ms (< 20ms).

2. **Lint check on test suite**:
   ```bash
   .venv/bin/ruff check tests/test_socket_ipc_stress.py
   ```

3. **Baseline test suites**:
   ```bash
   .venv/bin/python tests/test_speak_client.py
   .venv/bin/python tests/test_narrator_service.py
   .venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC tests.e2e.test_tier2_boundaries.TestTier2R2Boundaries
   ```
