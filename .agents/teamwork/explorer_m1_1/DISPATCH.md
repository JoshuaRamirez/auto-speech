## 2026-10-06T21:07:32Z
You are teamwork_preview_explorer (explorer_m1_1).
Your working directory is: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_1

MANDATORY FIRST STEP: Read the user request verbatim in:
/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md (under header `## 2026-10-06T20:47:50Z`).
Also read the project architecture in:
/Users/joshua/Developer/auto-speech/PROJECT.md
and reference the architectural survey in:
/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_1/analysis.md.

Note: All code changes for this milestone will be made inside the isolated git worktree at `/Users/joshua/Developer/auto-speech-worktree`.

Objective:
Formulate the concrete, detailed implementation strategy for the core Python CLI dispatcher (`plugin/scripts/python/cli.py`) for Milestone M1.
Investigate and specify:
1. Argument parsing structure using `argparse`: top-level parser (`--help`, `--version`), 12 subcommands:
   - `speak`: positional words, piped stdin, `--source-hash`, `--session-id`, `--priority`, `--voice`, `--speed`, `--socket-path`, `--ordinal`. Global mute check.
   - `replay`: `--ordinal N`.
   - `pause`, `resume`, `restart`: mpv IPC over `/tmp/auto-speech-mpv.sock`.
   - `stop`: DaemonClient.interrupt() and process signaling.
   - `seek`: target validation (`+N`, `-N`, `N`, `end`) and mpv IPC.
   - `autoplay`: `on`, `off`, `status`, `mode`, `scope`.
   - `narrate`: `on`, `off`, `status`, `stop`, `config`.
   - `hook`: `stop`, `narrator`, `bootstrap`.
   - `doctor`: text and `--json`.
   - `update`: `--force`.
2. Exact exit codes: 0 (OK/muted), 1 (failure), 2 (no session/invalid input), 3 (IPC fail), 4 (bad arg), 6 (playback fail), 130 (interrupted).
3. Error handling: clear diagnostic messages on stderr on failures.

Do NOT implement any code directly.
Write your analysis to `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_1/analysis.md`
and handoff report to `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_1/handoff.md`.
Send a completion message back to caller when done.
