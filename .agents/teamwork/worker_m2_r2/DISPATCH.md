# Task Assignment: worker_m2_r2 (Milestone M2 Iteration 2 Remediation)

## Objective
Remediate the concurrency listen backlog bottleneck, client-side retry resilience, abrupt disconnect truncation bug, and slowloris timeout protection identified by Challengers M2.1 and M2.2.

## Mandatory Reading
1. `/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md`
2. `/Users/joshua/Developer/auto-speech/PROJECT.md`
3. `/Users/joshua/Developer/auto-speech/.agents/teamwork/challenger_m2_1/handoff.md`
4. `/Users/joshua/Developer/auto-speech/.agents/teamwork/challenger_m2_2/handoff.md`

## MANDATORY INTEGRITY WARNING
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Detailed Requirements

### 1. `plugin/scripts/python/narrator_service.py`
- **Listen Backlog**: In `_DaemonSocketServer(socketserver.ThreadingUnixStreamServer)`, set `request_queue_size = 128` (or `SOMAXCONN`). Python's stdlib defaults to 5, which drops 70-86% of requests under bursts on Darwin/macOS.
- **Read Timeout**: In `_DaemonRequestHandler.handle()`, set `self.request.settimeout(5.0)` to protect against slowloris / stalled connections.
- **Abrupt Disconnect Handling**: In `_DaemonRequestHandler.handle()`, track whether an exception occurred during `recv()`. If `(ConnectionResetError, BrokenPipeError, OSError, socket.timeout)` occurs before clean EOF (`data == b""`), mark the stream as aborted and discard partial `chunks` without enqueuing.
- **Queue Lock Protection**: In `_process_chunk()` lines 549 and 566, route `self._tts_queue.put(...)` through `self._enqueue_phase(...)` to ensure thread safety under `_queue_lock` and drop-oldest backpressure consistency.

### 2. `plugin/scripts/python/speak.py`
- **Transient Connection Retry**: In `send_speech_request()`, add a brief retry loop (e.g. up to 3 attempts with 0.02s–0.05s backoff) when catching `ConnectionRefusedError` while attempting `sock.connect()`. Only print error and return 1 if all retry attempts fail.

### 3. Verification & Testing
Run all of the following commands in the virtual environment (`.venv/bin/python`):
1. `.venv/bin/python tests/test_speak_client.py`
2. `.venv/bin/python tests/test_narrator_service.py`
3. `PYTHONPATH=plugin/scripts/python .venv/bin/python tests/test_socket_ipc_stress.py` (Must pass with 0 failures!)
4. `.venv/bin/python -m unittest tests.test_socket_server_stress` (Must pass with 0 failures!)
5. `.venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC`
6. `.venv/bin/python -m unittest tests.e2e.test_tier2_boundaries.TestTier2R2Boundaries`
7. `.venv/bin/python -m unittest tests.e2e.test_tier3_combinations`
8. `.venv/bin/python -m unittest tests.e2e.test_tier4_scenarios`
9. `.venv/bin/ruff check plugin/scripts/python/speak.py plugin/scripts/python/narrator_service.py tests/test_speak_client.py tests/test_narrator_service.py`

## Deliverables
- Modify `plugin/scripts/python/narrator_service.py` and `plugin/scripts/python/speak.py`.
- Update tests if necessary to verify retry and abrupt disconnect behaviors.
- Write your complete handoff report to `/Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m2_r2/handoff.md`.
- Send a message back to the orchestrator when finished.


## 2026-10-03T19:09:32Z
You are worker_m2_r2.
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m2_r2
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md

Read ORIGINAL_REQUEST.md and PROJECT.md first. Read your task assignment in /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m2_r2/DISPATCH.md.
Also read the challenger findings in:
- /Users/joshua/Developer/auto-speech/.agents/teamwork/challenger_m2_1/handoff.md
- /Users/joshua/Developer/auto-speech/.agents/teamwork/challenger_m2_2/handoff.md

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Implement the remediations:
1. narrator_service.py: set request_queue_size = 128 on _DaemonSocketServer, set read timeout self.request.settimeout(5.0) in _DaemonRequestHandler.handle(), track aborted status on socket error and discard partial chunks without enqueuing, and route lines 549/566 put() calls through _enqueue_phase().
2. speak.py: add transient retry loop on ConnectionRefusedError in send_speech_request().
3. Run all unit tests, both challenger stress test suites (tests/test_socket_ipc_stress.py and tests/test_socket_server_stress.py), and E2E suites.
4. Report all verified test outputs in /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m2_r2/handoff.md and send a message when done.
