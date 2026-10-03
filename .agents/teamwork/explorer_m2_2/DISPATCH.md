# Task Assignment: Explorer M2.2 (speak.py Thin Client Specialist)

You are explorer_m2_2 (teamwork_preview_explorer).
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_2
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md

## Objective
Read ORIGINAL_REQUEST.md and PROJECT.md first.
You are investigating Milestone M2: Refactoring `plugin/scripts/python/speak.py` into a thin CLI client.
Specify:
1. Argument parsing: Keep backward-compatible arguments (`--ordinal`, `--source-hash`, `--keep-artifacts`) so existing callers do not break.
2. Input reading: Read text from `sys.stdin.read()`. If empty or whitespace-only, exit 0 cleanly.
3. Socket IPC:
   - Connect to `/tmp/auto-speech-daemon.sock`.
   - Send UTF-8 encoded text payload.
   - Shutdown socket write (`socket.SHUT_WR`) and close.
4. Error handling:
   - If daemon socket is missing or connection refused (`FileNotFoundError`, `ConnectionRefusedError`):
     Output clean error to `sys.stderr` (e.g. "Error: auto-speech daemon is not running.") and exit with code 1.
5. Verification:
   - Manual test command: `echo "test" | python3 plugin/scripts/python/speak.py`.

Write your findings to `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_2/analysis.md` and `handoff.md`. Send a message when done.


## 2026-10-03T18:36:44Z
[Message] timestamp=2026-10-03T18:36:44Z sender=c05df6b8-cecd-49ba-9fb8-8fa47f977488 priority=MESSAGE_PRIORITY_HIGH content=You are explorer_m2_2.
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_2
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md

Read ORIGINAL_REQUEST.md and PROJECT.md first. Read your task assignment in /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_2/DISPATCH.md.
Investigate speak.py thin client refactor: argument parsing compatibility, stdin streaming, socket connection to /tmp/auto-speech-daemon.sock, and graceful error handling when daemon is down.
Write findings to analysis.md and handoff.md. Send a message when done.
