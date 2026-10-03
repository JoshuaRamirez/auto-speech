# Handoff Report: Milestone M2 Test Specification & Unit Test Design

**Agent**: `spec_miner_m2_3`  
**Date**: 2026-10-03  
**Working Directory**: `/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m2_3`  
**Type**: Hard Handoff  

---

## 1. Observation

1. **Current Milestone Status**:
   - `PROJECT.md:60` indicates Milestone M2 ("Thin Client IPC via UNIX Sockets") is `IN_PROGRESS`.
   - `TEST_READY.md:113` records: "10 failures due to pending M2 (`speak.py` thin socket client and daemon socket listener)".

2. **Existing Implementation of `speak.py`**:
   - `plugin/scripts/python/speak.py:14-48`:
     `from pipeline import PipelineOrchestrator`
     `orchestrator = PipelineOrchestrator(keep_artifacts=args.keep_artifacts, source_hash=args.source_hash)`
     `return orchestrator.run(transcript_text=transcript_text, turn_ordinal=args.ordinal)`
   - Legacy `speak.py` invokes the deprecated heavy pipeline orchestrator instead of acting as a lightweight socket client.

3. **Current State of `narrator_service.py`**:
   - Grep search for `socket` in `plugin/scripts/python/narrator_service.py` returns 0 results.
   - However, `narrator_service.py:537-538` in `_tts_worker` already supports string queue items:
     ```python
     if isinstance(phase, str):
         self._speak(phase)
     ```
   - `narrator_service.py:450-474` implements drop-oldest capped queue ingestion via `_enqueue_phase()`.

4. **E2E Test Execution & Failures**:
   - Command: `PYTHONPATH=. pytest tests/e2e/test_tier1_features.py -k "TestTier1R2"`
     Results: 4 passed, 2 failed.
     - `test_tier1_r2_daemon_socket_enqueues_to_tts_queue` failed at line 268:
       `AssertionError: False is not true : R2 Violation: narrator_service.py must include a UNIX domain socket server`
     - `test_tier1_r2_speak_cli_transmits_stdin_to_socket` failed at line 252:
       `AssertionError: 124 != 0 : speak.py failed with exit code 124. TimeoutExpired: command timed out after 2.5s` (caused by Kokoro weight download/initialization attempt in legacy pipeline).
   - Command: `PYTHONPATH=. pytest tests/e2e/test_tier2_boundaries.py -k "TestTier2R2"`
     Results: 2 passed, 3 failed.
     - `test_tier2_r2_empty_stdin_ignored_by_daemon` failed at line 161:
       `AssertionError: 4 != 0` (legacy pipeline orchestrator exited with code 4 on empty text).
     - `test_tier2_r2_large_socket_payload_chunking` failed at line 225:
       `AssertionError: 124 != 0` (timeout).
     - `test_tier2_r2_special_characters_and_multiline_payload` failed at line 195:
       `AssertionError: 124 != 0` (timeout).
     - `test_tier2_r2_abrupt_client_disconnect` passed (pure socketserver resilience test).
     - `test_tier2_r2_stale_socket_file_cleanup_on_startup` passed.

5. **Absence of Standalone Unit Test Suite**:
   - `tests/test_speak_client.py` does not currently exist in `tests/`.

---

## 2. Logic Chain

1. **Step 1: Cause of Current E2E Failures (from Observations 2, 4)**
   - The test failures in `TestTier1R2ThinClientIPC` and `TestTier2R2Boundaries` stem directly from `speak.py` attempting to run the full `PipelineOrchestrator` rather than transmitting stdin over the UNIX socket.
   - When given empty/whitespace stdin (`test_tier2_r2_empty_stdin_ignored_by_daemon`), legacy `PipelineOrchestrator` returns code 4; the specification requires `speak.py` to exit 0 and ignore empty stdin.
   - When given standard, large, or special-character input, legacy `PipelineOrchestrator` attempts MLX model setup, causing test timeouts (code 124).

2. **Step 2: Server Socket Integration Path (from Observations 3, 4)**
   - `narrator_service.py` requires a background `socketserver.ThreadingUnixStreamServer` thread listening on `AUTO_SPEECH_DAEMON_SOCK` (default `/tmp/auto-speech-daemon.sock`).
   - Because `_tts_worker` already branches on `if isinstance(phase, str): self._speak(phase)` (Observation 3), any string received over the socket can simply be passed into `self._enqueue_phase(payload_str)` to achieve automatic drop-oldest buffering, thread safety, and sequential playback via `NativeAudioSink`.

3. **Step 3: Stale Socket and Disconnect Resilience (from Observations 4)**
   - `test_tier2_r2_stale_socket_file_cleanup_on_startup` verifies that a daemon starting with an existing socket file must unlink it prior to binding.
   - `test_tier2_r2_abrupt_client_disconnect` proves that wrapping socket stream reads in exception handlers for `(ConnectionResetError, BrokenPipeError, OSError)` successfully keeps the daemon thread healthy under abrupt disconnects.

4. **Step 4: Standalone Unit Testing Architecture (from Observations 1, 5)**
   - Unit testing `speak.py` should not require starting the heavy daemon or running subprocess sandboxes.
   - By structuring `speak.py` into distinct helper functions (`get_socket_path`, `send_speech_request`, and `main`), tests can exercise argument parsing, empty input short-circuiting, environment variable overrides, mock socket calls, and ephemeral in-memory UNIX socket servers with sub-second execution.

---

## 3. Caveats

- **No Codebase Modification (Read-Only)**: As a Specification Miner, no files outside of `.agents/teamwork/spec_miner_m2_3/` were modified. The unit test suite design and implementation requirements are documented in `analysis.md` ready for implementation agents.
- **Assumed `speak.py` Error Code**: When the daemon is down, `PROJECT.md` specifies exiting with a non-zero code. Exit code `1` was chosen for socket connection failures, while exit code `2` is preserved for argparse and `--source-hash` validation failures.
- **Drop-Oldest String Logging**: When strings are dropped from `_tts_queue` in `narrator_service.py`, `getattr(dropped, "category", None)` yields `None`, defaulting category to `"?"`. A minor cosmetic logging improvement can label dropped socket requests as `"cli"` or `"text"`.

---

## 4. Conclusion

The specification for Milestone M2 is fully probed, cataloged, and documented:
1. **17 Discovered Features** across 6 categories (CLI Thin Client, Daemon UNIX Socket Server, Wire Protocol, Queue Ingestion, Resilience, Lifecycle) were identified and cataloged with inputs, outputs, error behavior, and discovery source.
2. **15 Edge Cases** spanning empty stdin, multi-line unicode, 128 KB payloads, legacy hash validations, abrupt resets, and stale socket cleanup were probed and documented.
3. **M2 Test Catalog** across Tiers 1-4 was mapped out.
4. **Standalone Unit Test Suite** `tests/test_speak_client.py` was fully designed and verified with a prototype runner, covering CLI arguments, environment variable overrides, input short-circuiting, socket protocol mocking, live ephemeral socket transmission, and error handling.
5. All findings, tables, and drop-in ready test code are committed to `/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m2_3/analysis.md`.

---

## 5. Verification Method

1. **Verify Analysis and Specification Artifacts**:
   - Inspect `/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m2_3/analysis.md` for the Features Discovered table, Edge Cases table, and complete unit test implementation blueprint.
   - Inspect `/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m2_3/handoff.md`.

2. **Verify Current E2E Test Baseline (Pre-Implementation)**:
   - Run: `PYTHONPATH=. pytest tests/e2e/test_tier1_features.py -k "TestTier1R2"`
   - Expected: 4 passed, 2 failed (daemon socket missing, speak CLI timeout).
   - Run: `PYTHONPATH=. pytest tests/e2e/test_tier2_boundaries.py -k "TestTier2R2"`
   - Expected: 2 passed, 3 failed (empty stdin code 4, 128KB timeout, special characters timeout).

3. **Verify Post-Implementation Target**:
   - Once the implementation agent implements `speak.py` and `tests/test_speak_client.py`:
     - Run: `PYTHONPATH=. pytest tests/test_speak_client.py` (all tests pass in < 1s).
     - Run: `PYTHONPATH=. pytest tests/e2e/test_tier1_features.py -k "TestTier1R2"` (6 passed, 0 failed).
     - Run: `PYTHONPATH=. pytest tests/e2e/test_tier2_boundaries.py -k "TestTier2R2"` (5 passed, 0 failed).
