## 2026-10-06T20:50:56Z

You are teamwork_preview_explorer (explorer_survey_1).
Your working directory is: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_1

MANDATORY FIRST STEP: Read the user request verbatim in:
/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md (under header `## 2026-10-06T20:47:50Z`).

Objective:
Survey existing CLI scripts, command handlers, and entry points in `/Users/joshua/Developer/auto-speech` to map the requirements for R1: Unified `auto-speech` CLI Dispatcher.

Investigate:
1. All existing python scripts in `plugin/scripts/python/` (including `speak.py`, `replay.py`, `pause.py`, `resume.py`, `stop.py`, `restart.py`, `seek.py`, `autoplay.py`, `autoplay_worker.py`, `narrator.py`, `doctor.py`, `status.py`, `daemon_client.py`, `narrator_service.py`, etc.).
2. How transport controls, speech synthesis, replay, autoplay toggles/modes/scopes, narrate toggles/status/stop/config, hook triggers, and doctor are currently implemented and invoked.
3. How a unified CLI dispatcher executable `auto-speech` (and linked to `~/.local/bin/auto-speech`) should be designed: argument parsing, subcommand routing per R1 (`speak`, `replay`, `pause`, `resume`, `stop`, `restart`, `seek`, `autoplay on|off|status|mode|scope`, `narrate on|off|status|stop|config`, `hook stop|narrator|bootstrap`, `doctor`, `update`), exit codes, stderr diagnostics, and handling stdin vs CLI args.
4. Existing symlinks or PATH conventions.

Output:
Update progress.md periodically.
Write full findings to:
/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_1/analysis.md
and a structured handoff report to:
/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_1/handoff.md
When finished, send a message to the caller with the summary and path to your handoff.
