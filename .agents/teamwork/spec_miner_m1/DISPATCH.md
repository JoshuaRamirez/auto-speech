## 2026-10-06T21:07:32Z
You are teamwork_preview_spec_miner (spec_miner_m1).
Your working directory is: /Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m1

MANDATORY FIRST STEP: Read the user request verbatim in:
/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md (under header `## 2026-10-06T20:47:50Z`).
Also read:
/Users/joshua/Developer/auto-speech/PROJECT.md.

Note: Target worktree is `/Users/joshua/Developer/auto-speech-worktree`.

Objective:
Mine exact test specifications, assertions, edge cases, and test harness design for `tests/test_cli_dispatcher.py` to achieve comprehensive coverage for the unified `auto-speech` CLI dispatcher (Milestone M1).
Investigate:
1. Test cases for `auto-speech --help` and all 12 subcommands with `--help`.
2. Test cases for `speak`: text arguments, stdin piping, `--source-hash` cache playback, empty input error, global mute handling.
3. Test cases for `replay`: valid ordinal, missing ordinal, out-of-bounds/empty cache (exit 2).
4. Test cases for transport controls: `pause`, `resume`, `stop`, `restart`, `seek` (verifying exit 2 when no active session; exit 4 on invalid seek target).
5. Test cases for `autoplay`: `on`, `off`, `status`, `mode`, `scope` (valid/invalid modes, solo vs all).
6. Test cases for `narrate`: `on`, `off`, `status`, `stop`, `config`.
7. Test cases for `hook`: `stop`, `narrator`, `bootstrap` with sample JSON payloads.
8. Test cases for `doctor` and `update`.
9. Ensure tests can run hermetically using isolated temp directories and mocks/environment variables.

Do NOT implement test files directly.
Write your specification report to:
/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m1/analysis.md
and handoff report to:
/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m1/handoff.md.
Send a completion message back to caller when done.
