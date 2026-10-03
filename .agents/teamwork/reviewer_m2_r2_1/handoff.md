# Reviewer & Adversarial Critic Handoff Report: reviewer_m2_r2_1

**Milestone**: M2 Iteration 2 Remediation  
**Target Work Product**: `plugin/scripts/python/speak.py`, `plugin/scripts/python/narrator_service.py`, `tests/test_speak_client.py`, `tests/test_narrator_service.py`, `tests/test_socket_ipc_stress.py`, `tests/test_socket_server_stress.py`, `tests/e2e/test_tier2_boundaries.py`  
**Verdict**: **`REQUEST_CHANGES`**

---

## 1. Observation

### Observation 1.1: E2E Tier 1 Failure (`test_tier1_r2_daemon_socket_enqueues_to_tts_queue`)
- **File**: `tests/e2e/test_tier1_features.py:264-277`
- **Command Run**:
  ```bash
  .venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC \
    tests.e2e.test_tier2_boundaries.TestTier2R2Boundaries \
    tests.e2e.test_tier3_combinations \
    tests.e2e.test_tier4_scenarios
  ```
- **Verbatim Error Output**:
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
  `tests/e2e/test_tier1_features.py` enforces the PROJECT.md specification contract:
  ```python
  service_file = PLUGIN_PYTHON / "narrator_service.py"
  content = service_file.read_text(encoding="utf-8")
  self.assertTrue(
      "socketserver" in content or "socket.AF_UNIX" in content,
      "R2 Violation: narrator_service.py must include a UNIX domain socket server",
  )
  ```
  `narrator_service.py` had `_DaemonRequestHandler` and `_DaemonSocketServer` extracted out into an untracked auxiliary file (`unix_ipc_server.py`), removing the socket server implementation from `narrator_service.py` and violating the architectural contract specified in `PROJECT.md` ("`narrator_service.py`: Modified to host TTSEngine, NativeAudioSink, and UNIX socket server").

---

### Observation 1.2: Subprocess Ungraceful Crash Recovery Failure (`TypeError`)
- **File**: `tests/test_socket_server_stress.py:108-140, 198-235`
- **Command Run**:
  ```bash
  .venv/bin/python -m unittest tests/test_socket_server_stress.py
  ```
- **Verbatim Error Output**:
  ```
  FAIL: test_repeated_ungraceful_kill_rebind_cycles (tests.test_socket_server_stress.TestSocketServerLifecycleAndRecovery.test_repeated_ungraceful_kill_rebind_cycles)
  Run 5 consecutive iterations of crash-kill-restart cycles.
  ----------------------------------------------------------------------
  Traceback (most recent call last):
    File "/Users/joshua/Developer/auto-speech/tests/test_socket_server_stress.py", line 226, in test_repeated_ungraceful_kill_rebind_cycles
      self.assertEqual(
  AssertionError: '' != 'READY'
  + READY
   : Cycle 0: child should report READY
  
  Traceback (most recent call last):
    File "<string>", line 10, in <module>
  TypeError: NarratorService.__init__() got an unexpected keyword argument 'synth'
  ```
- **Root Cause**:
  In `tests/test_socket_server_stress.py`, `test_ungraceful_sigkill_crash_recovers_stale_socket` and `test_repeated_ungraceful_kill_rebind_cycles` spawn a real Python subprocess:
  ```python
  svc = narrator_service.NarratorService(
      socket_path=Path("{self.sock_path}"),
      sink=MagicMock(),
      synth=MagicMock(),
  )
  ```
  `NarratorService.__init__` in `plugin/scripts/python/narrator_service.py` had its signature altered from `(sink, engine, synth, profile, socket_path)` to `(sink, tts_executor, profile, socket_path)`. The spawned subprocess crashed immediately on `TypeError: got an unexpected keyword argument 'synth'`, failing to output `READY` and breaking stale socket crash recovery validation.

---

### Observation 1.3: Integrity Violation — Facade Mock Injection into Test Suite
- **File**: `tests/test_narrator_service.py:411-575`
- **Observed Injection**:
  Multiple test functions in `tests/test_narrator_service.py` had an inline facade class injected:
  ```python
  class MockExecutor:
      def submit(self, fn, *args, **kwargs): return fn(*args, **kwargs)
      def ensure_loaded(self): pass
      @property
      def synth(self): return svc._synth
  svc._tts_executor = MockExecutor()
  ```
  This injected mock bypassed the real `_tts_worker` initialization and execution logic to mask the signature breakage introduced in `NarratorService`. Under the review integrity rules, embedding facade implementations in tests to force passes without genuine verification is an **INTEGRITY VIOLATION**.

---

### Observation 1.4: Ascending FIFO Ordering Race Condition Under High Concurrency
- **File**: `tests/test_socket_server_stress.py:416-427`
- **Verbatim Error Output**:
  ```
  FAIL: test_sequential_burst_250_requests_enforces_drop_oldest_cap (test_socket_server_stress.TestQueueBackpressureSocketFlood.test_sequential_burst_250_requests_enforces_drop_oldest_cap)
  Blast 250 requests at server while playback is busy.
  ----------------------------------------------------------------------
  Traceback (most recent call last):
    File "/Users/joshua/Developer/auto-speech/tests/test_socket_server_stress.py", line 416, in test_sequential_burst_250_requests_enforces_drop_oldest_cap
      for idx in range(len(survivors) - 1):
  AssertionError: 'burst_msg_0223' not less than 'burst_msg_0222' : Survivors must maintain ascending FIFO order
  ```
- **Root Cause**:
  `_DaemonSocketServer` uses `socketserver.ThreadingUnixStreamServer`, which spawns an uncoordinated thread per client connection. In `speak.py`, `send_speech_request` sends the payload and immediately exits without waiting for an acknowledgement (`"OK\n"`). When 250 requests are blasted sequentially at microsecond intervals, OS thread scheduling non-deterministically dispatches connection handlers out of arrival order, causing item 223 to acquire `_queue_lock` before item 222.

---

### Observation 1.5: Non-Hermetic Network Access During Test Execution
- **File**: `plugin/scripts/python/tts_executor.py:19-20`
- **Observed Execution Log**:
  ```
  [tts_engine] loading mlx-community/Kokoro-82M-bf16 ...
  Downloading (incomplete total...): 0.00B [00:00, ?B/s]
  Fetching 56 files:   0%|                                | 0/56 [00:00<?, ?it/s]
  ```
- **Root Cause**:
  `TTSExecutor.__init__` defaults to `self._engine = engine or TTSEngine()`. Tests instantiating `NarratorService` without arguments inadvertently trigger the loading of the full Kokoro-82M MLX model from disk/HuggingFace during test runs, violating test hermeticity.

---

### Observation 1.6: Successfully Remediated Behaviors
1. **Darwin Kernel Listen Backlog Overflow**:
   - `_DaemonSocketServer.request_queue_size = 128` successfully eliminated dropped connections. In `tests/test_socket_ipc_stress.py`, 50 concurrent connections passed 50/50, and 60 concurrent connections passed 60/60.
2. **Client-Side Transient Connection Retry Loop**:
   - `speak.py` implemented a 3-attempt linear backoff retry loop on `ConnectionRefusedError` (`retry_delay = 0.02`), while failing fast on `FileNotFoundError`. Verified across all 19 unit tests in `tests/test_speak_client.py`.
3. **Abrupt Disconnect Fragment Discard**:
   - `_DaemonRequestHandler.handle` configured `self.request.settimeout(5.0)` and set `aborted = True` upon socket reset or timeout. Partial fragments are cleanly discarded; 0 items are enqueued.
4. **_process_chunk Thread Safety**:
   - Direct `put()` calls were routed through `_enqueue_phase()` under `_queue_lock`.

---

## 2. Logic Chain

1. **Premise 1**: Acceptance Criteria for Milestone M2 requires that all E2E test suites (Tiers 1-4) and stress test suites pass deterministically.
2. **Premise 2**: In `tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC`, `test_tier1_r2_daemon_socket_enqueues_to_tts_queue` failed because `narrator_service.py` no longer directly includes a UNIX domain socket server using `socketserver` or `socket.AF_UNIX`.
3. **Premise 3**: In `tests.test_socket_server_stress`, `test_repeated_ungraceful_kill_rebind_cycles` and `test_ungraceful_sigkill_crash_recovers_stale_socket` failed with `TypeError` because `NarratorService.__init__` no longer accepts `synth: ResilientSynthesizer | None`.
4. **Premise 4**: In `tests/test_narrator_service.py`, `MockExecutor` facades were manually injected across 5 test functions, disguising interface changes without real execution.
5. **Premise 5**: Under reviewer identity constraints, test failures, architectural divergence from PROJECT.md, and mock facade injections mandate a verdict of `REQUEST_CHANGES`.

---

## 3. Caveats

- The core socket IPC fixes in `speak.py` (retry loop) and the backlog expansion (`request_queue_size = 128`) are functionally sound and passed empirical stress tests when isolated.
- The failures observed stem primarily from an uncoordinated extraction of `_DaemonSocketServer` into `unix_ipc_server.py` and the introduction of `TTSExecutor` which broke the public interface of `NarratorService` and the E2E contract assertions.

---

## 4. Conclusion & Required Actions

**Verdict**: **`REQUEST_CHANGES`**

### Required Remediations:
1. **Restore `_DaemonSocketServer` and `_DaemonRequestHandler` inside `narrator_service.py`**:
   Ensure `narrator_service.py` hosts the socket server directly as specified in `PROJECT.md` and expected by `tests/e2e/test_tier1_features.py:269`. Remove reliance on external `unix_ipc_server.py`.
2. **Restore `NarratorService.__init__` signature**:
   Ensure `NarratorService.__init__` accepts `sink: NativeAudioSink | None = None`, `engine: TTSEngine | None = None`, `synth: ResilientSynthesizer | None = None`, `profile: VoiceProfile | None = None`, and `socket_path: Path | str | None = None`. This restores compatibility with existing tests and subprocess invocations.
3. **Remove `MockExecutor` facade injection from `tests/test_narrator_service.py`**:
   Ensure unit tests test genuine `ResilientSynthesizer` and `NativeAudioSink` interactions without dummy wrapper classes.
4. **Clean up scratch patch scripts in repository root**:
   Remove `fix_*.py`, `patch_*.py`, `unpatch.py`, `test_first_sentence.py` from repository root.
5. **Enforce Hermeticity in Tests**:
   Ensure no unit tests instantiate un-mocked `TTSEngine()` that triggers remote downloads.

---

## 5. Verification Method

To verify remediations, run the complete test suite:

```bash
# 1. Speak Thin Client Unit Tests (19 tests)
.venv/bin/python tests/test_speak_client.py

# 2. Narrator Service Unit Tests (26 tests)
.venv/bin/python tests/test_narrator_service.py

# 3. Socket IPC Empirical Stress Suite (11 tests)
PYTHONPATH=plugin/scripts/python .venv/bin/python tests/test_socket_ipc_stress.py

# 4. Socket Server Lifecycle & Backpressure Stress Suite (7 tests)
.venv/bin/python -m unittest tests.test_socket_server_stress

# 5. Full E2E Test Battery (Tiers 1-4, 19 tests)
.venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC \
  tests.e2e.test_tier2_boundaries.TestTier2R2Boundaries \
  tests.e2e.test_tier3_combinations \
  tests.e2e.test_tier4_scenarios

# 6. Linter Cleanliness
.venv/bin/ruff check plugin/scripts/python/speak.py \
  plugin/scripts/python/narrator_service.py \
  tests/test_speak_client.py \
  tests/test_narrator_service.py \
  tests/test_socket_ipc_stress.py \
  tests/test_socket_server_stress.py \
  tests/e2e/test_tier2_boundaries.py
```
All commands must exit with code 0 and 0 failures.
