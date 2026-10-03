# Task Assignment: Spec Miner Survey 3

You are spec_miner_survey_3 (teamwork_preview_spec_miner).
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_survey_3
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md

## Objective
Read ORIGINAL_REQUEST.md first.
Mine the codebase specifications and requirements regarding:
1. Dead architectural sprawl to delete: identify exact locations, files, references, imports, and tests of `run_speak.sh`, `PipelineOrchestrator`, `ShortPathStrategy`, `MpvController`, and `SessionDir` PID management.
2. Thin client IPC via UNIX sockets: examine `speak.py` current behavior (flags, stdin handling, environment variables, exit codes), and specify the exact protocol/contract needed for `speak.py` -> UNIX socket (e.g. `/tmp/auto-speech-daemon.sock`) -> `narrator_service.py` background socketserver.
3. Existing test suites: check existing unit/integration tests, test runners, pytest configuration, and how tests currently exercise `speak.py` and `narrator_service.py`.

## Output
Write your findings to `/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_survey_3/analysis.md` and `handoff.md`.
Send a completion message back when done.


## 2026-10-03T17:47:36Z
[Message] timestamp=2026-10-03T17:47:36Z sender=c05df6b8-cecd-49ba-9fb8-8fa47f977488 priority=MESSAGE_PRIORITY_HIGH content=You are spec_miner_survey_3.
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_survey_3
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md

Read ORIGINAL_REQUEST.md first. Read your task assignment in /Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_survey_3/DISPATCH.md.
Mine the codebase specifications and requirements regarding:
1. Dead architectural sprawl to delete: identify exact locations, files, references, imports, and tests of `run_speak.sh`, `PipelineOrchestrator`, `ShortPathStrategy`, `MpvController`, and `SessionDir` PID management.
2. Thin client IPC via UNIX sockets: examine `speak.py` current behavior (flags, stdin handling, environment variables, exit codes), and specify the exact protocol/contract needed for `speak.py` -> UNIX socket (e.g. `/tmp/auto-speech-daemon.sock`) -> `narrator_service.py` background socketserver.
3. Existing test suites: check existing unit/integration tests, test runners, pytest configuration, and how tests currently exercise `speak.py` and `narrator_service.py`.
Write your findings to /Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_survey_3/analysis.md and handoff.md.
Send a message back when complete.
