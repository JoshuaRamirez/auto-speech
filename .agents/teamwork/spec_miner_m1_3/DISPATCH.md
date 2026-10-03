# Task Assignment: Spec Miner M1.3 (NarratorService Cleanup & Unit Testing)

You are spec_miner_m1_3 (teamwork_preview_spec_miner).
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m1_3
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md

## Objective
Read ORIGINAL_REQUEST.md and PROJECT.md first.
You are investigating Milestone M1: Cleanup of legacy hacks and unit test fix in `narrator_service.py`:
1. Catalog every legacy hack line in `narrator_service.py` to be removed during M1:
   - `_speak_script()` resolution to `run_speak.sh` (line 110)
   - `SessionDir.wav_path_path()` reads and `SessionDir.read_pid()` (lines 542, 552)
   - `wave.open` duration calculation + `time.sleep(duration + 0.5)` (lines 545–550)
   - `os.kill(pid, _signal.SIGKILL)` and `os.kill(pid, signal.SIGKILL)` (lines 553–557, 591–596)
   - `_wait_mpv_idle()` method (lines 563–604)
   - Duplicate dead code block in `_process_chunk` (lines 317–349)
2. Specify fix for the unit test failure in `tests/test_narrator_service.py:306`:
   - Line 237 `AttributeError: 'NarratorService' object has no attribute '_classifier'` -> replace with safe attribute check: `classifier = getattr(self, "_classifier", None)`
3. Detail unit test verification strategy for `tests/test_narrator_service.py` under the new in-process architecture.

Write your findings to `/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m1_3/analysis.md` and `handoff.md`. Send a message when done.


## 2026-10-03T18:00:30Z
You are spec_miner_m1_3.
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m1_3
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md

Read ORIGINAL_REQUEST.md and PROJECT.md first. Read your task assignment in /Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m1_3/DISPATCH.md.
Specify all legacy hacks in narrator_service.py to remove during M1 (time.sleep, SIGKILL, SessionDir, _wait_mpv_idle, dead code blocks), specify the line 237 bug fix, and document unit test verification.
Write findings to analysis.md and handoff.md. Send a message when done.
