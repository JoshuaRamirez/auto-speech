## 2026-10-06T21:42:25Z

You are teamwork_preview_explorer (explorer_m1_retry_2).
Your working directory is: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_retry_2

MANDATORY FIRST STEP: Read the user request verbatim in:
/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md (under header `## 2026-10-06T20:47:50Z`).
Also read:
/Users/joshua/Developer/auto-speech/PROJECT.md
and the challenger failure report:
/Users/joshua/Developer/auto-speech/.agents/teamwork/challenger_m1_1/handoff.md.

Context: In Milestone M1, challenger_m1_1 discovered that in `handle_speak` (`plugin/scripts/python/cli.py` lines 112–118), when no positional words are provided and `--source-hash` is set, the CLI unconditionally calls `sys.stdin.read()`. On interactive terminals (`sys.stdin.isatty() == True`), this blocks indefinitely waiting for stdin input instead of immediately playing the cached audio.
Objective:
Investigate `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/cli.py` (`handle_speak`).
Formulate the exact fix strategy to ensure:
1. When `args.words` is empty and `--source-hash` is provided: if `sys.stdin.isatty()`, text defaults to `""` without reading stdin, immediately triggering cache lookup.
2. If `sys.stdin` is piped (not a TTY), reads stdin text. If stdin text is non-empty, uses it; if empty, proceeds with `--source-hash`.
3. If neither words nor `--source-hash` are provided and stdin is a TTY, prints error to stderr and returns `EXIT_INVALID_INPUT` (2).
4. Specify unit tests in `tests/test_cli_dispatcher.py`.

Do NOT write or modify code directly. Write your fix recommendation to:
/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_retry_2/handoff.md
Send a completion message when finished.
