# Forensic Audit Report: Milestone M2 (Thin Client IPC via UNIX Sockets)

**Work Product**: Milestone M2 deliverables:
- `plugin/scripts/python/speak.py`
- `plugin/scripts/python/narrator_service.py`
- `tests/test_speak_client.py`
- `tests/test_narrator_service.py`
**Profile**: General Project (Development Mode per ORIGINAL_REQUEST.md)
**Verdict**: CLEAN

---

## 1. Observation

### 1.1 Source Code Inspection
1. **`plugin/scripts/python/speak.py`**:
   - Lines 39–55: Creates genuine standard-library socket:
     ```python
     sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
     try:
         sock.settimeout(timeout)
         sock.connect(str(socket_path))
         sock.sendall(text.encode("utf-8"))
         try:
             sock.shutdown(socket.SHUT_WR)
         except OSError:
             pass
     except (FileNotFoundError, ConnectionRefusedError):
         print(f"Error: cannot connect to auto-speech daemon at {socket_path}", file=sys.stderr)
         return 1
     ```
   - Lines 33–34 & 89–90: Clean short-circuiting on empty or whitespace-only inputs without opening a socket connection.
   - Lines 60–86: Backward-compatible CLI parsing retaining `--ordinal`, `--keep-artifacts`, `--source-hash` (with strict 64-character lowercase hexadecimal validation), and `--socket-path` override.
   - No mock fixtures, no hardcoded return values, no test string bypasses.

2. **`plugin/scripts/python/narrator_service.py`**:
   - Lines 161–197: `_DaemonRequestHandler` reading stream chunks up to EOF, decoding UTF-8, stripping whitespace, and calling `service.enqueue_text(cleaned)`.
   - Lines 199–224: `_DaemonSocketServer(socketserver.ThreadingUnixStreamServer)` with `server_bind()` unlinking any stale socket file before binding:
     ```python
     def server_bind(self) -> None:
         try:
             path = Path(self.server_address)
             if path.exists() or path.is_symlink():
                 path.unlink(missing_ok=True)
         except OSError:
             pass
         super().server_bind()
     ```
   - Lines 333–365: `_start_socket_server()` launching the server on background daemon thread `daemon-socket-server`, registering safe cleanup handler with `atexit`.
   - Lines 366–400: `_stop_socket_server()` cleanly shutting down the server, closing the listening socket, joining the thread, unlinking the socket file from the filesystem, and unregistering `atexit`.
   - Lines 401–411 & 600–644: `enqueue_text()` enqueues incoming socket requests into `_tts_queue` guarded by `_queue_lock`, applying drop-oldest backpressure capped at `max_queue_depth`.
   - Lines 707–708: `_tts_worker` consumes items from `_tts_queue`; when an item is a string, it calls `self._speak(phase)` directly.

3. **Absence of Prohibited Patterns**:
   - Grep search for test string literals (`Hello from`, `Line 1:`) in `plugin/scripts/python/` returned zero matches.
   - Grep search for `mock` in `plugin/scripts/python/` revealed only architectural components (`MockSummarizer` fallback for offline LLM mode in `narrator_summarizer.py`). No mocks exist in `speak.py` or `narrator_service.py` socket paths.
   - Pre-populated artifact check (`find .agents/teamwork -name '*.log' -o -name '*result*' -o -name '*output*'`) returned zero files.

### 1.2 Test Suite Execution Results
- Unit test suite `tests/test_speak_client.py`:
  ```
  .venv/bin/python tests/test_speak_client.py
  Ran 18 tests in 1.171s
  OK
  ```
- Unit test suite `tests/test_narrator_service.py`:
  ```
  .venv/bin/python tests/test_narrator_service.py
  narrator_service: 26 tests passed
  ```
- E2E Tier 1 Feature suite `tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC`:
  ```
  .venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC
  Ran 6 tests in 1.185s
  OK
  ```
- E2E Tier 2 Boundary suite `tests.e2e.test_tier2_boundaries.TestTier2R2Boundaries`:
  ```
  .venv/bin/python -m unittest tests.e2e.test_tier2_boundaries.TestTier2R2Boundaries
  Ran 5 tests in 2.215s
  OK
  ```
- E2E Tier 3 Combination suite `tests.e2e.test_tier3_combinations`:
  ```
  .venv/bin/python -m unittest tests.e2e.test_tier3_combinations
  Ran 5 tests in 3.872s
  OK
  ```
- E2E Tier 4 Scenario suite `tests.e2e.test_tier4_scenarios`:
  ```
  .venv/bin/python -m unittest tests.e2e.test_tier4_scenarios
  Ran 3 tests in 2.185s
  OK
  ```
- Code quality / linting:
  ```
  .venv/bin/ruff check plugin/scripts/python/speak.py plugin/scripts/python/narrator_service.py tests/test_speak_client.py
  All checks passed!
  ```

### 1.3 Independent Empirical Runtime Audit
An independent auditor test script was executed without mocks, testing real OS file inodes and inter-process communication:
1. **Socket creation & type verification**:
   - Daemon socket bound at `/tmp/forensic-audit-d76d9f4a.sock`.
   - File stat verification: `sock_path.exists() == True`, `stat.S_ISSOCK(os.stat(sock_path).st_mode) == True`, mode `0o140755`. Confirmed authentic UNIX domain socket inode created by OS.
2. **Cross-process transmission**:
   - Separate process `python3 plugin/scripts/python/speak.py --socket-path /tmp/forensic-audit-d76d9f4a.sock` invoked with dynamic stdin `ForensicToken_a09b05fb198a4796b7b48a68f360c99f`.
   - Exit code: `0`.
   - `svc._tts_queue.get(timeout=2.0)` received exact token: `'ForensicToken_a09b05fb198a4796b7b48a68f360c99f'`.
3. **Real unlinking on disk**:
   - `svc._stop_socket_server()` executed.
   - `sock_path.exists()` evaluated to `False`. The socket inode was completely unlinked from `/tmp`.
4. **Stale socket unlinking on restart**:
   - A non-socket file was created at the socket path (`sock_path.touch()`).
   - `svc._start_socket_server()` executed.
   - Successfully replaced stale file with a live socket (`stat.S_ISSOCK` is True) without raising `OSError: [Errno 48] Address already in use`.
5. **Absent daemon error handling**:
   - `speak.py` invoked against non-existent socket `/tmp/forensic-audit-d76d9f4a.sock`.
   - Exit code: `1`.
   - Stderr: `"Error: cannot connect to auto-speech daemon at /tmp/forensic-audit-d76d9f4a.sock"`.
6. **Whitespace/empty short-circuit**:
   - `speak.py` invoked with whitespace-only stdin `"   \n\t  \n"` against non-existent socket.
   - Exit code: `0` (clean exit without socket connect attempt).
7. **Adversarial stress testing**:
   - 20 concurrent client processes transmitting simultaneously via `ThreadPoolExecutor(max_workers=10)`: 20/20 requests successfully transmitted and enqueued.
   - 500 KB payload containing emojis (`🚀`) and Unicode characters (`日本語`) transmitted across the stream: received with 100% byte fidelity (`512020` chars received matching source).
   - Backward compatibility CLI arguments (`--source-hash`, `--ordinal`, `--keep-artifacts`) tested with real socket transmission: successfully enqueued payload.

---

## 2. Logic Chain

1. **Integrity Mode Mapping**:
   `ORIGINAL_REQUEST.md` specifies `Integrity mode: development`. Under Development Mode, the forensic audit checks for absence of hardcoded test results, facade implementations, and fabricated verification artifacts.
2. **Analysis of Implementation Authenticity**:
   - Observation 1.1 establishes that `plugin/scripts/python/speak.py` has no mocks or test-specific branches. It uses standard library `socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)` to connect, stream, and close.
   - Observation 1.1 establishes that `plugin/scripts/python/narrator_service.py` runs a bona fide `socketserver.ThreadingUnixStreamServer`, decodes incoming UTF-8 bytes from the socket stream, and enqueues them into `_tts_queue` with queue-lock protection and drop-oldest shedding.
3. **Verification of Absence of Facades & Mocks**:
   - As observed in Observation 1.1.3, there are no hardcoded string matches or fabricated test responses in production code.
4. **Empirical Validation of Operating System Primitives**:
   - As proven in Observation 1.3, `/tmp/forensic-audit-*.sock` was created with `stat.S_ISSOCK`, confirming authentic UNIX domain socket creation by the macOS kernel.
   - Inter-process communication between an independent CLI invocation of `speak.py` and the daemon thread successfully transmitted arbitrary dynamic UUID tokens.
   - Shutdown via `_stop_socket_server()` physically removed the socket file from the filesystem (`sock_path.exists() == False`).
   - Startup successfully unlinked stale sockets, avoiding `Address already in use`.
5. **Robustness & Stress Resilience**:
   - As proven in Observation 1.3.7, the implementation handled 20 concurrent client processes and 500 KB payloads without deadlocks, corruption, or truncation.
6. **Test Suite Completeness**:
   - As recorded in Observation 1.2, all 18 unit tests in `test_speak_client.py`, all 26 unit tests in `test_narrator_service.py`, and all 19 E2E tests across Tiers 1-4 pass cleanly with zero lint warnings.

Therefore, the work product adheres strictly to architectural contracts, user requirements, and integrity standards.

---

## 3. Caveats

- Milestone M3 files (`run_speak.sh`, `pipeline.py`, `short_path.py`, `mpv_controller.py`, `session_dir.py`) remain present on disk in accordance with the project plan, as their deletion and caller adaptation is assigned to Milestone M3.
- Hardware audio playback (MLX Kokoro TTS model generation and physical speaker output via `mpv`) was tested in M1 and mocked/stubbed in socket unit tests as expected; the M2 audit specifically validated the IPC socket transmission layer, queue ingestion, and lifecycle unlinking.

---

## 4. Conclusion

**Verdict: CLEAN**

Milestone M2 (Thin Client IPC via UNIX Sockets) satisfies all integrity criteria and implementation specifications:
1. `speak.py` is a genuine thin CLI client transmitting `stdin` over a real UNIX domain stream socket.
2. `narrator_service.py` runs a multi-threaded UNIX domain socket server that authenticates incoming stream payloads, enforces UTF-8 decoding, enqueues to `_tts_queue`, and executes proper socket lifecycle management.
3. Socket creation, transmission, unlinking, and stale file cleanup operate authentically at the OS level.
4. No hardcoded results, mock facades, or shortcuts exist in production code.
5. All unit, boundary, integration, and adversarial stress tests pass 100%.

---

## 5. Verification Method

To independently verify the audit findings:

1. **Run Thin Client Unit Tests**:
   ```bash
   .venv/bin/python tests/test_speak_client.py
   ```
   *Expected*: 18 passed in ~1.2s.

2. **Run Narrator Service Unit Tests**:
   ```bash
   .venv/bin/python tests/test_narrator_service.py
   ```
   *Expected*: 26 passed.

3. **Run E2E IPC Feature Tests**:
   ```bash
   .venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC
   ```
   *Expected*: 6 passed.

4. **Run E2E Boundary Tests**:
   ```bash
   .venv/bin/python -m unittest tests.e2e.test_tier2_boundaries.TestTier2R2Boundaries
   ```
   *Expected*: 5 passed.

5. **Run Empirical Inode & Unlinking Verification**:
   ```bash
   .venv/bin/python -c '
   import os, sys, stat, uuid, subprocess, time, queue, threading
   from pathlib import Path
   sys.path.insert(0, "plugin/scripts/python")
   import narrator_service, speak

   sock = Path(f"/tmp/verify-{uuid.uuid4().hex[:6]}.sock")
   svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
   svc._config, svc._max_queue = {"max_queue_depth": 32}, 32
   svc._tts_queue, svc._dropped_phases = queue.Queue(maxsize=32), 0
   svc._socket_path, svc._socket_server, svc._socket_thread = sock, None, None
   svc._queue_lock, svc._atexit_registered, svc._last_event_ts = threading.Lock(), False, 0.0
   svc._update_depth = lambda d: None

   svc._start_socket_server()
   assert stat.S_ISSOCK(os.stat(sock).st_mode), "Not a socket"
   p = subprocess.run([sys.executable, "plugin/scripts/python/speak.py", "--socket-path", str(sock)], input="Hello Audit", text=True, capture_output=True)
   assert p.returncode == 0 and svc._tts_queue.get(timeout=2) == "Hello Audit"
   svc._stop_socket_server()
   assert not sock.exists(), "Socket not unlinked"
   print("Empirical Verification Successful!")
   '
   ```
   *Expected*: Prints `"Empirical Verification Successful!"` with exit code 0.
