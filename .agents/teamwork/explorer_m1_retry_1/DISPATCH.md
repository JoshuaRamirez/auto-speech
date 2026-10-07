## 2026-10-06T21:42:24Z
You are teamwork_preview_explorer (explorer_m1_retry_1).
Your working directory is: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_retry_1

MANDATORY FIRST STEP: Read the user request verbatim in:
/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md (under header `## 2026-10-06T20:47:50Z`).
Also read:
/Users/joshua/Developer/auto-speech/PROJECT.md
and the challenger failure report:
/Users/joshua/Developer/auto-speech/.agents/teamwork/challenger_m1_1/handoff.md.

Context: In Milestone M1, challenger_m1_1 discovered that running `bin/auto-speech seek foo` exits with code 2 instead of code 4 because `_has_active_session()` is checked before validating `args.target`.
Objective:
Investigate `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/cli.py` line 296+ (`handle_seek`).
Formulate the exact fix strategy to ensure:
1. `args.target` is validated FIRST (checking not empty and matches valid seek syntax `+N`, `-N`, `N`, `end`).
2. If invalid, prints diagnostic to stderr and returns `EXIT_BAD_ARG` (4) IMMEDIATELY, before checking session presence.
3. If valid, checks `_has_active_session()` and returns `EXIT_NO_SESSION` (2) if no session.
4. Specify un-mocked unit test assertions for `tests/test_cli_dispatcher.py`.

Do NOT write or modify code directly. Write your fix recommendation to:
/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_retry_1/handoff.md
Send a completion message when finished.
