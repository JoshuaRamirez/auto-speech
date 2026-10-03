# Task Assignment: challenger_m2_r2_2 (Milestone M2 Iteration 2 Empirical Challenger)

## Objective
Empirically stress-test socket server lifecycle, unpaced flood drops under `request_queue_size = 128`, and simultaneous socket + JSONL events.

## Mandatory Reading
1. `/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md`
2. `/Users/joshua/Developer/auto-speech/PROJECT.md`
3. `/Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m2_r2/handoff.md`
4. `/Users/joshua/Developer/auto-speech/.agents/teamwork/challenger_m2_2/handoff.md`

## Challenge Tasks
1. Run `.venv/bin/python -m unittest tests.test_socket_server_stress`.
2. Verify unpaced 250-request flood with backlog=128 experiences 0 errors and drops 218 items cleanly under 32-item queue cap.
3. Verify ungraceful crash recovery (SIGKILL) across multiple restart cycles.
4. Verify simultaneous socket requests and JSONL tool events under load.
5. Deliver handoff report to `/Users/joshua/Developer/auto-speech/.agents/teamwork/challenger_m2_r2_2/handoff.md` with explicit verdict `APPROVE` or `REJECT`.


## 2026-10-03T19:27:52Z
You are challenger_m2_r2_2.
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/challenger_m2_r2_2
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md
Worker handoff: /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m2_r2/handoff.md

Read ORIGINAL_REQUEST.md and PROJECT.md first. Read your task assignment in /Users/joshua/Developer/auto-speech/.agents/teamwork/challenger_m2_r2_2/DISPATCH.md.
Empirically stress-test socket server lifecycle: run tests/test_socket_server_stress.py, verify 250-request flood under backlog=128 experiences 0 errors and drops 218 items under FIFO cap, verify SIGKILL ungraceful crash recovery, and verify simultaneous socket + JSONL events.
Deliver your report to /Users/joshua/Developer/auto-speech/.agents/teamwork/challenger_m2_r2_2/handoff.md with APPROVE or REJECT. Send a message when done.
