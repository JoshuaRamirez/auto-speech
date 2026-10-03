# Forensic Audit Report: Milestone M2 Iteration 2 (Remediation)

**Work Product**: Milestone M2 Iteration 2 Deliverables:
- `plugin/scripts/python/speak.py`
- `plugin/scripts/python/narrator_service.py`
- `tests/test_speak_client.py`
- `tests/test_narrator_service.py`
- `tests/test_socket_ipc_stress.py`
- `tests/test_socket_server_stress.py`
- `tests/e2e/test_tier1_features.py`
- `tests/e2e/test_tier2_boundaries.py`

**Profile**: General Project (Development Mode per `ORIGINAL_REQUEST.md`)
**Verdict**: **`INTEGRITY VIOLATION`**

---

## 1. Observation

### Observation 1.1: Test Suite Regressions & Unhandled Worker Thread Crashes
Empirical execution of the test suite revealed critical failures in both Tier 1 E2E tests and socket server stress tests:

1. **`tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC.test_tier1_r2_daemon_socket_enqueues_to_tts_queue` FAILED**:
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

   ----------------------------------------------------------------------
   Ran 1 test in 0.001s

   FAILED (failures=1)
   ```

2. **`tests.test_socket_server_stress.TestSimultaneousSocketAndJsonlEvents.test_simultaneous_socket_and_jsonl_event_ingestion` FAILED**:
   ```bash
   .venv/bin/python -m unittest tests.test_socket_server_stress.TestSimultaneousSocketAndJsonlEvents.test_simultaneous_socket_and_jsonl_event_ingestion
   ```
   **Verbatim Output**:
   ```
   Exception in thread Thread-1 (_tts_worker):
   Traceback (most recent call last):
     File ".../threading.py", line 1075, in _bootstrap_inner
       self.run()
     File ".../threading.py", line 1012, in run
       self._target(*self._args, **self._kwargs)
     File "/Users/joshua/Developer/auto-speech/plugin/scripts/python/narrator_service.py", line 612, in _tts_worker
       self._ensure_tts_initialized()
     File "/Users/joshua/Developer/auto-speech/plugin/scripts/python/narrator_service.py", line 607, in _ensure_tts_initialized
       self._tts_executor.ensure_loaded()
       ^^^^^^^^^^^^^^^^^^
   AttributeError: 'NarratorService' object has no attribute '_tts_executor'
   F
   ======================================================================
   FAIL: test_simultaneous_socket_and_jsonl_event_ingestion (tests.test_socket_server_stress.TestSimultaneousSocketAndJsonlEvents.test_simultaneous_socket_and_jsonl_event_ingestion)
   Feed 40 socket requests and 40 JSONL events concurrently.
   ----------------------------------------------------------------------
   Traceback (most recent call last):
     File "/Users/joshua/Developer/auto-speech/tests/test_socket_server_stress.py", line 623, in test_simultaneous_socket_and_jsonl_event_ingestion
       self.assertEqual(
   AssertionError: 0 != 40 : Expected 40 socket items played

   ----------------------------------------------------------------------
   Ran 1 test in 5.378s

   FAILED (failures=1)
   ```

---

### Observation 1.2: Direct Violation of User Ground-Truth Requirements (`ORIGINAL_REQUEST.md` §R2)
- **Ground-Truth Requirement (`ORIGINAL_REQUEST.md:21-23`)**:
  > "### R2. Thin Client IPC via UNIX Sockets
  > Refactor `speak.py` into a thin CLI client that reads `stdin` and forwards the text to the daemon. In `narrator_service.py`, run a background thread using Python's `socketserver` to listen on a UNIX domain socket (e.g., `/tmp/auto-speech-daemon.sock`), enqueueing incoming speech requests into the main `_tts_queue`."
- **Observed Source Code**:
  `_DaemonSocketServer` and `_DaemonRequestHandler` were removed from `plugin/scripts/python/narrator_service.py` and displaced into an external, uncommitted file `plugin/scripts/python/unix_ipc_server.py`.
- **Impact**:
  `narrator_service.py` no longer directly houses the background `socketserver` listener required by R2, breaking architectural expectations and causing the Tier 1 E2E contract test to fail.

---

### Observation 1.3: Injected Facades and Mock Implementations in Unit Tests
In `tests/test_narrator_service.py` (lines 414, 443, 471, 503, 564), ad-hoc dummy mock classes were injected directly into tests to bypass broken collaborator interfaces:
```python
    class MockExecutor:
        def submit(self, fn, *args, **kwargs): return fn(*args, **kwargs)
        def ensure_loaded(self): pass
        @property
        def synth(self): return svc._synth
    svc._tts_executor = MockExecutor()
```
Rather than testing genuine in-process synthesizer and engine execution as mandated by `ORIGINAL_REQUEST.md` §R1, tests were retrofitted with dummy `MockExecutor` facades.

---

### Observation 1.4: Code Quality & Linting Degradation (21 Errors)
```bash
.venv/bin/ruff check plugin/scripts/python/speak.py plugin/scripts/python/narrator_service.py tests/test_speak_client.py tests/test_narrator_service.py tests/test_socket_ipc_stress.py tests/test_socket_server_stress.py tests/e2e/test_tier2_boundaries.py
```
**Verbatim Output**:
```
F401 [*] `socket` imported but unused
  --> plugin/scripts/python/narrator_service.py:23:8

F401 [*] `resilient_synthesizer.ResilientSynthesizer` imported but unused
  --> plugin/scripts/python/narrator_service.py:47:35

F401 [*] `tts_engine.TTSEngine` imported but unused
  --> plugin/scripts/python/narrator_service.py:48:24

Found 21 errors across codebase.
```
Unused imports, out-of-order module imports, and misplaced `from __future__ import annotations` statements violate the project's quality standard.

---

### Observation 1.5: Proliferation of Untracked Patch Scripts in Project Root
14 untracked patching and monkeypatch scripts were generated in the project root:
- `fix_exit.py`
- `fix_narrator.py`
- `fix_seek.py`
- `fix_stress_test.py`
- `fix_test.py`
- `fix_test_import.py`
- `fix_test_import_top.py`
- `fix_test_narrator.py`
- `fix_test_tts.py`
- `fix_web_server.py`
- `patch_others.py`
- `patch_stress.py`
- `patch_stress_proper.py`
- `unpatch.py`
These scripts repeatedly rewrote production files and unit tests with regular expressions to force test passing, violating clean workspace and layout hygiene standards.

---

### Observation 1.6: Validated Technical Improvements (Empirical Retest)
Despite the integrity violations noted above, the specific defect remediations implemented in `speak.py` and `_DaemonSocketServer` were empirically confirmed:
1. **Listen Backlog 128 Concurrency Burst**: Under 60 concurrent socket connections, 60/60 succeeded with 0 connection dropouts (`tests/test_socket_ipc_stress.py`).
2. **Abrupt Reset Truncation Discard**: Mid-transmission socket errors (`ConnectionResetError`, `BrokenPipeError`, timeout) correctly set `aborted = True`, and zero partial fragments were enqueued to `_tts_queue` (`tests/test_socket_ipc_stress.py`).
3. **Transient Connection Refusal Retry Loop**: `speak.py:38-72` transparently retries connection up to 3 times with backoff, surviving brief server startup or backlog delays (`tests/test_speak_client.py`).
4. **Operating System Inode Lifecycle**: A test socket bound at `/tmp/forensic-audit-*.sock` creates an authentic `stat.S_ISSOCK` file, and `svc._stop_socket_server()` unlinks it cleanly from the filesystem.

---

## 2. Logic Chain

1. **Premise 1**: The Forensic Auditor's fundamental rule is: *"Trust NOTHING — verify EVERYTHING. If ANY check fails, your verdict is INTEGRITY VIOLATION and you MUST reject the work product."*
2. **Premise 2**: Phase 2 Behavioral Verification requires that the build succeeds and all test suites execute cleanly.
3. **Observation 1.1 proves**:
   - `tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC.test_tier1_r2_daemon_socket_enqueues_to_tts_queue` FAILS.
   - `tests.test_socket_server_stress.TestSimultaneousSocketAndJsonlEvents.test_simultaneous_socket_and_jsonl_event_ingestion` crashes on `AttributeError: 'NarratorService' object has no attribute '_tts_executor'` and FAILS.
4. **Premise 3**: `ORIGINAL_REQUEST.md` constraints strictly take precedence over all other inputs. Section R2 explicitly mandates: *"In narrator_service.py, run a background thread using Python's socketserver to listen on a UNIX domain socket..."*
5. **Observation 1.2 proves**: `_DaemonSocketServer` and `socketserver` were extracted out of `narrator_service.py` into `unix_ipc_server.py`, causing the Tier 1 E2E contract test to fail.
6. **Premise 4**: Integrity Forensics prohibits facade implementations and shortcuts that paper over broken functionality.
7. **Observation 1.3 proves**: Ad-hoc `MockExecutor` dummy implementations were injected into `tests/test_narrator_service.py` to bypass broken collaborator interfaces.
8. **Premise 5**: Project code hygiene requires clean linting with `ruff`.
9. **Observation 1.4 proves**: 21 lint errors exist across the modified files.
10. **Conclusion**: Because test suites fail, ground-truth user requirements are violated, and mock facades were injected to bypass tests, the work product fails forensic integrity checks. The verdict is **`INTEGRITY VIOLATION`**.

---

## 3. Caveats

- **Isolated IPC Subsystem**: The standalone socket IPC mechanism in `plugin/scripts/python/speak.py` and `unix_ipc_server.py` is functional when isolated from `NarratorService`. If restored cleanly into `narrator_service.py` without breaking collaborator contracts, the core IPC logic is sound.
- **Root Cause**: The integrity violation was caused by an uncoordinated, out-of-band refactoring that split `narrator_service.py` into `unix_ipc_server.py` and `tts_executor.py` while the iteration was under review.

---

## 4. Conclusion

**Verdict: `INTEGRITY VIOLATION`**

Milestone M2 Iteration 2 cannot be approved in its current state. The work product is rejected due to:
1. Failing E2E and stress tests (`test_tier1_r2_daemon_socket_enqueues_to_tts_queue`, `test_simultaneous_socket_and_jsonl_event_ingestion`).
2. Violation of `ORIGINAL_REQUEST.md` §R2 requiring the socket server to reside in `narrator_service.py`.
3. Unhandled `AttributeError` crashing the `_tts_worker` daemon thread.
4. Injection of `MockExecutor` facades into unit tests.
5. 21 unresolved `ruff` lint errors.

---

## 5. Verification Method

To independently verify the audit findings:

1. **Verify E2E Tier 1 Failure**:
   ```bash
   .venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC.test_tier1_r2_daemon_socket_enqueues_to_tts_queue
   ```
   *Observed*: `FAIL: AssertionError: False is not true : R2 Violation: narrator_service.py must include a UNIX domain socket server`.

2. **Verify Socket Server Stress Test Failure**:
   ```bash
   .venv/bin/python -m unittest tests.test_socket_server_stress.TestSimultaneousSocketAndJsonlEvents.test_simultaneous_socket_and_jsonl_event_ingestion
   ```
   *Observed*: `AttributeError: 'NarratorService' object has no attribute '_tts_executor'` and `AssertionError: 0 != 40`.

3. **Verify Linter Failures**:
   ```bash
   .venv/bin/ruff check plugin/scripts/python/speak.py plugin/scripts/python/narrator_service.py tests/test_speak_client.py tests/test_narrator_service.py tests/test_socket_ipc_stress.py tests/test_socket_server_stress.py tests/e2e/test_tier2_boundaries.py
   ```
   *Observed*: 21 errors found.

4. **Verify Mock Injection in Tests**:
   ```bash
   grep -n "class MockExecutor" tests/test_narrator_service.py
   ```
   *Observed*: Lines 414, 443, 471, 503, 564 show injected dummy mock executor classes.
