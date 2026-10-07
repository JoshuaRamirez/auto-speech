## 2026-10-06T21:49:33Z
You are teamwork_preview_worker (worker_m1_fix).
Your dedicated working directory is: /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m1_fix

MANDATORY FIRST STEP: Read the user request verbatim in:
/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md (under header `## 2026-10-06T20:47:50Z`).

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

CRITICAL WORKTREE LOCATION:
All changes and tests MUST be executed inside:
/Users/joshua/Developer/auto-speech-worktree

Reference Material to Read First:
1. /Users/joshua/Developer/auto-speech/PROJECT.md
2. /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_retry_1/handoff.md (Seek validation fix)
3. /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_retry_2/handoff.md (Speak stdin resolution fix)
4. /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_retry_3/handoff.md (Hook non-dict JSON payload defense fix)

Task: Apply the 3 bugfixes to `plugin/scripts/python/cli.py` and expand `tests/test_cli_dispatcher.py`:
1. In `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/cli.py`:
   - `handle_seek`: Reorder validation. Validate `args.target` syntax FIRST (empty, 'end', '+N', '-N', 'N' with float conversion). If invalid, print error to stderr and return EXIT_BAD_ARG (4) immediately. Only if valid, check `_has_active_session()` and return EXIT_NO_SESSION (2) if no session.
   - `handle_speak`: When `args.words` is empty and `--source-hash` is provided, check if `sys.stdin.isatty()`. If so, default text to `""` without calling `sys.stdin.read()`, preventing interactive TTY hangs.
   - `handle_hook`: In both `hook stop` and `hook narrator` subcommands, after `json.loads(payload_raw)`, defensively check `if not isinstance(payload, dict): payload = {}`. Ensure clean 0 exit on non-dict JSON payloads (`[1, 2, 3]`, `null`, `42`).
2. In `/Users/joshua/Developer/auto-speech-worktree/tests/test_cli_dispatcher.py`:
   - Add/update tests for seek invalid targets un-mocked and with `_has_active_session=False`.
   - Add tests for speak `--source-hash` with mocked TTY stdin verifying `.read()` is never called.
   - Add tests for `hook stop` and `hook narrator` with non-dict JSON payloads.
3. Verification in `/Users/joshua/Developer/auto-speech-worktree`:
   - `.venv/bin/python -m unittest tests/test_cli_dispatcher.py` (all tests pass)
   - `bash tests/run_all.sh` (all 50 suites pass)
   - `.venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py` (0 errors)
   - Directly verify: `./bin/auto-speech seek foo` returns exit code 4.

Write your report to:
/Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m1_fix/handoff.md
Send a completion message when finished.
