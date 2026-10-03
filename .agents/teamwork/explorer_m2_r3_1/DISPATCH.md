# Task Assignment: explorer_m2_r3_1 (Forensic Audit Remediation: Architectural Restoration)

## Objective
Design the exact architectural restoration plan to re-embed `_DaemonSocketServer` and `_DaemonRequestHandler` directly into `plugin/scripts/python/narrator_service.py` (satisfying `ORIGINAL_REQUEST.md` §R2 and passing `test_tier1_r2_daemon_socket_enqueues_to_tts_queue`), while retaining the validated defect remediations (listen backlog `request_queue_size = 128`, 5.0s client read timeout, aborted socket disconnect discard, and `_queue_lock` backpressure), and deleting `plugin/scripts/python/unix_ipc_server.py`.

## Mandatory Reading
1. `/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md`
2. `/Users/joshua/Developer/auto-speech/PROJECT.md`
3. Full Forensic Audit Evidence Report: `/Users/joshua/Developer/auto-speech/.agents/teamwork/auditor_m2_r2_1/handoff.md`
4. Reviewer & Challenger Reports:
   - `/Users/joshua/Developer/auto-speech/.agents/teamwork/reviewer_m2_r2_1/handoff.md`
   - `/Users/joshua/Developer/auto-speech/.agents/teamwork/reviewer_m2_r2_2/handoff.md`
   - `/Users/joshua/Developer/auto-speech/.agents/teamwork/challenger_m2_r2_1/handoff.md`
   - `/Users/joshua/Developer/auto-speech/.agents/teamwork/challenger_m2_r2_2/handoff.md`

## Full Forensic Audit Evidence (Do Not Filter or Omit)
- **Failing Tier 1 E2E Test**: `test_tier1_r2_daemon_socket_enqueues_to_tts_queue` in `tests/e2e/test_tier1_features.py` failed with `AssertionError: False is not true : R2 Violation: narrator_service.py must include a UNIX domain socket server`.
- **Cause**: `_DaemonSocketServer` and `_DaemonRequestHandler` were removed from `narrator_service.py` into `unix_ipc_server.py`.
- **Requirement**: `ORIGINAL_REQUEST.md` §R2 explicitly states: *"In narrator_service.py, run a background thread using Python's socketserver to listen on a UNIX domain socket (e.g., /tmp/auto-speech-daemon.sock), enqueueing incoming speech requests into the main _tts_queue."*
- **Required Fix Strategy**:
  1. Define `_DaemonRequestHandler` and `_DaemonSocketServer` directly in `plugin/scripts/python/narrator_service.py`.
  2. Maintain `request_queue_size = 128`, `self.request.settimeout(5.0)`, and `aborted` discard.
  3. Ensure `_start_socket_server()` and `_stop_socket_server()` are methods of `NarratorService` managing this embedded server.
  4. Ensure `unix_ipc_server.py` is removed or completely deprecated without breaking imports.

## Deliverables
- Write detailed analysis and fix strategy to `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_r3_1/analysis.md` and `handoff.md`.
- Send a message when complete.

## 2026-10-03T19:40:47Z
You are explorer_m2_r3_1.
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_r3_1
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md

Read ORIGINAL_REQUEST.md and PROJECT.md first. Read your task assignment in /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_r3_1/DISPATCH.md.
Also read the full forensic audit evidence report in /Users/joshua/Developer/auto-speech/.agents/teamwork/auditor_m2_r2_1/handoff.md.

Design the exact architectural restoration plan to re-embed _DaemonSocketServer and _DaemonRequestHandler directly inside narrator_service.py (fulfilling ORIGINAL_REQUEST.md §R2 and passing test_tier1_r2_daemon_socket_enqueues_to_tts_queue), retaining all validated remediations (backlog=128, timeout=5.0s, aborted discard, queue lock backpressure), and deleting unix_ipc_server.py.
Write your analysis to analysis.md and handoff.md. Send a message when done.
