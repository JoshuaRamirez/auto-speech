# Task Assignment: Explorer M2.1 (Daemon Socket Server Specialist)

You are explorer_m2_1 (teamwork_preview_explorer).
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_1
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md

## Objective
Read ORIGINAL_REQUEST.md and PROJECT.md first.
You are investigating Milestone M2: UNIX domain socket server integration into `plugin/scripts/python/narrator_service.py`.
Specify:
1. `socketserver.ThreadingUnixStreamServer` or `socketserver.UnixStreamServer` background thread in `NarratorService`.
2. Socket path: `/tmp/auto-speech-daemon.sock` (configurable via environment or constructor).
3. Lifecycle & cleanup:
   - Safe unlinking of stale socket file on startup before binding.
   - Clean unlinking of socket file on daemon shutdown (`_stop`, signals, exit).
4. Request handling:
   - Read UTF-8 stream from client until EOF.
   - Strip whitespace; if non-empty, enqueue to `self._tts_queue`.
   - Handle concurrent client connections safely.

Write your findings to `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_1/analysis.md` and `handoff.md`. Send a message when done.


## 2026-10-03T18:36:44Z
You are explorer_m2_1.
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_1
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md

Read ORIGINAL_REQUEST.md and PROJECT.md first. Read your task assignment in /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_1/DISPATCH.md.
Investigate daemon socket server integration into narrator_service.py on /tmp/auto-speech-daemon.sock, stale cleanup, unlinking on exit, and enqueueing to _tts_queue.
Write findings to analysis.md and handoff.md. Send a message when done.
