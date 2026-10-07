# BRIEFING — 2026-10-06T21:56:00Z

## Mission
Apply 3 bugfixes to cli.py and test_cli_dispatcher.py in auto-speech-worktree (seek validation order, speak tty stdin bypass with source-hash, hook non-dict JSON defense), verify with 50 suites and ruff.

## 🔒 My Identity
- Archetype: implementer
- Roles: implementer, qa, specialist
- Working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m1_fix
- Original parent: 4db9af0e-498d-49a5-831a-0009fd31326e
- Milestone: milestone_1

## 🔒 Key Constraints
- All changes and tests executed in /Users/joshua/Developer/auto-speech-worktree
- Do not cheat; genuine implementations only
- .agents/teamwork/ holds only metadata (no code, tests, or data)
- Must pass tests/run_all.sh (all 50 suites) and ruff check
- Direct verification: `./bin/auto-speech seek foo` returns exit code 4

## Current Parent
- Conversation ID: 4db9af0e-498d-49a5-831a-0009fd31326e
- Updated: 2026-10-06T21:56:00Z

## Task Summary
- **What to build**: Fix handle_seek syntax validation before session check; fix handle_speak stdin reading on TTY when --source-hash present; fix handle_hook payload dictionary checking on non-dict JSON. Update tests in test_cli_dispatcher.py.
- **Success criteria**: All 50 test suites pass, ruff check clean (0 errors), seek foo returns exit 4, handoff report generated.
- **Interface contracts**: /Users/joshua/Developer/auto-speech/PROJECT.md
- **Code layout**: /Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/cli.py, /Users/joshua/Developer/auto-speech-worktree/tests/test_cli_dispatcher.py

## Key Decisions Made
- Reordered handle_seek to validate target syntax and finiteness using `math.isfinite()` before querying `_has_active_session()`.
- Guarded `sys.stdin.read()` in `handle_speak` with `is_tty`: when on TTY with `--source-hash`, defaulted text to `""` to trigger cache playback without hanging; when on TTY without text or hash, exited 2 immediately.
- Defensively sanitized `payload` in `handle_hook` (`hook stop` and `hook narrator`) with `if not isinstance(payload, dict): payload = {}` and converted session IDs and event names to strings safely.
- Added 10 new unit/integration tests to `tests/test_cli_dispatcher.py` covering TTY handling, seek edge cases without session mocking, non-dict hook payloads, and trampoline subprocess invocation.

## Artifact Index
- /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m1_fix/DISPATCH.md
- /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m1_fix/progress.md
- /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m1_fix/handoff.md

## Change Tracker
- **Files modified**:
  - `plugin/scripts/python/cli.py`: Applied seek syntax validation order, TTY stdin bypass, non-dict hook defense.
  - `tests/test_cli_dispatcher.py`: Added 10 tests across TestCliSpeak, TestCliTransportControls, TestCliHook, and TestCliTrampolineIntegration.
- **Build status**: PASS (all 50 suites passed in `bash tests/run_all.sh`)
- **Pending issues**: None

## Quality Status
- **Build/test result**: PASS — 84/84 unit tests in test_cli_dispatcher.py, 50/50 suites in run_all.sh.
- **Lint status**: PASS — 0 errors in `ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py`.
- **Tests added/modified**: 10 new test methods added to `tests/test_cli_dispatcher.py`.

## Loaded Skills
- None
