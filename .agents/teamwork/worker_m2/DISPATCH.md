# Task Assignment: Worker M2 (Thin Client IPC via UNIX Sockets)

You are worker_m2 (teamwork_preview_worker).
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m2
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md

## Reference Analysis Reports
Read these reports carefully before writing code:
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_1/analysis.md (Daemon socket server in narrator_service.py)
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_2/analysis.md (speak.py thin client refactor)
- /Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m2_3/analysis.md (M2 test suite and test_speak_client.py)

## File Ownership
You exclusively own and may edit:
- `plugin/scripts/python/speak.py` (MODIFIED)
- `plugin/scripts/python/narrator_service.py` (MODIFIED)
- `tests/test_speak_client.py` (NEW)
- `tests/test_narrator_service.py` (MODIFIED)
Do NOT modify files in `tests/e2e/` or any other modules.

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Tasks
1. Refactor `plugin/scripts/python/speak.py` into a thin CLI client:
   - Accept arguments `--ordinal`, `--source-hash`, `--keep-artifacts`.
   - Read `sys.stdin.read()`. If stripped text is empty, exit 0 cleanly.
   - Connect to `/tmp/auto-speech-daemon.sock` (or `AUTO_SPEECH_DAEMON_SOCK` env).
   - If socket missing / connection refused, print error to `sys.stderr` and exit with code 1.
   - Send UTF-8 encoded text payload, shutdown write (`SHUT_WR`), close socket, exit 0.
2. Refactor `plugin/scripts/python/narrator_service.py`:
   - Add UNIX domain socket server using `socketserver.ThreadingUnixStreamServer` listening on `/tmp/auto-speech-daemon.sock` (configurable via `socket_path` parameter or `AUTO_SPEECH_DAEMON_SOCK`).
   - Run server in a background daemon thread (`self._socket_thread`).
   - Unlink stale socket on startup before binding.
   - Unlink socket on shutdown in `NarratorService.run()` `finally:` block (and register defensive `atexit`).
   - Handler reads incoming UTF-8 stream until EOF, strips whitespace, and if non-empty, enqueues to `self._tts_queue` respecting drop-oldest backpressure and resetting `self._last_event_ts`.
   - Handle client disconnects, large payloads (128KB+), and concurrent connections safely.
3. Implement `tests/test_speak_client.py` and update `tests/test_narrator_service.py`:
   - Unit tests for `speak.py` thin client (mocked & real ephemeral socket server).
   - Unit tests for socket server in `NarratorService`.
4. Verification:
   - Run `.venv/bin/python tests/test_speak_client.py`.
   - Run `.venv/bin/python tests/test_narrator_service.py`.
   - Run `.venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R2ThinClientIPC`.
   - Run `.venv/bin/python -m unittest tests.e2e.test_tier2_boundaries.TestTier2R2Boundaries`.
   - Run `.venv/bin/ruff check plugin/scripts/python/speak.py plugin/scripts/python/narrator_service.py tests/test_speak_client.py`.
5. Report all passing test outputs, changes, and verification in `handoff.md`. Send completion message when done.


## 2026-10-03T18:44:55Z
[Message] timestamp=2026-10-03T18:44:55Z sender=c05df6b8-cecd-49ba-9fb8-8fa47f977488 priority=MESSAGE_PRIORITY_HIGH
You are worker_m2.
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m2
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md

Read ORIGINAL_REQUEST.md and PROJECT.md first. Read your task assignment in /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m2/DISPATCH.md.
Review reference reports in explorer_m2_1/analysis.md, explorer_m2_2/analysis.md, and spec_miner_m2_3/analysis.md.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Implement:
1. speak.py thin CLI client forwarding stdin to /tmp/auto-speech-daemon.sock.
2. UNIX domain socket server in narrator_service.py enqueueing to _tts_queue.
3. tests/test_speak_client.py and test updates.
4. Execute tests and report all passing results in handoff.md. Send a message when done.
