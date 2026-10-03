# Task Assignment: Reviewer M2.2

You are reviewer_m2_2 (teamwork_preview_reviewer).
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/reviewer_m2_2
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md
Worker handoff: /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m2/handoff.md

## Objective
Read ORIGINAL_REQUEST.md, PROJECT.md, and worker_m2 handoff.
Independently review the Milestone M2 implementation:
- `plugin/scripts/python/speak.py`
- `plugin/scripts/python/narrator_service.py`
- `tests/test_speak_client.py`
- `tests/test_narrator_service.py`

Examine:
1. Thread safety and locking: verify `self._queue_lock` guarding `self._tts_queue`, ensuring zero race conditions between socket requests and event-tailing threads.
2. Socket lifecycle and cleanup: verify automatic unlinking of stale socket on startup before binding, and clean unlinking on shutdown in `NarratorService.run()` `finally:` block.
3. Resilience against abrupt client disconnects, socket EOF chunking, large payloads (128KB+), and malformed streams.
4. Execute tests and linter: run tests and `ruff check`.

Output:
Write your review report to `/Users/joshua/Developer/auto-speech/.agents/teamwork/reviewer_m2_2/handoff.md` with explicit verdict `APPROVE` or `REQUEST_CHANGES`. Send a message when done.

## 2026-10-03T18:56:03Z
[Message] timestamp=2026-10-03T18:56:03Z sender=c05df6b8-cecd-49ba-9fb8-8fa47f977488 priority=MESSAGE_PRIORITY_HIGH content=You are reviewer_m2_2.
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/reviewer_m2_2
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md
Worker handoff: /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m2/handoff.md

Read ORIGINAL_REQUEST.md and PROJECT.md first. Read your task assignment in /Users/joshua/Developer/auto-speech/.agents/teamwork/reviewer_m2_2/DISPATCH.md.
Review Milestone M2 implementation focusing on socket lifecycle, thread safety (_queue_lock), wire protocol chunking, error handling, and linter check.
Run tests and deliver your report to handoff.md with APPROVE or REQUEST_CHANGES. Send a message when done.
