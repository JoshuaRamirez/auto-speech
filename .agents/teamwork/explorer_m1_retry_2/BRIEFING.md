# BRIEFING — 2026-10-06T21:46:00Z

## Mission
Investigate handle_speak in auto-speech CLI and formulate exact fix strategy for stdin blocking with --source-hash.

## 🔒 My Identity
- Archetype: explorer
- Roles: investigation, synthesis
- Working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_retry_2
- Original parent: 4db9af0e-498d-49a5-831a-0009fd31326e
- Milestone: M1_retry_2

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- Do NOT write or modify code directly. Write fix recommendation to /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_retry_2/handoff.md
- Send a completion message via send_message when finished

## Current Parent
- Conversation ID: 4db9af0e-498d-49a5-831a-0009fd31326e
- Updated: 2026-10-06T21:42:25Z

## Investigation State
- **Explored paths**:
  - `ORIGINAL_REQUEST.md` (header `## 2026-10-06T20:47:50Z`)
  - `PROJECT.md` (CLI dispatcher specifications, exit codes)
  - `challenger_m1_1/handoff.md` (empirical challenge report, observations on Test Scope 3 seek & speak TTY hang)
  - `plugin/scripts/python/cli.py` (lines 89–184 `handle_speak`, lines 296–339 `handle_seek`)
  - `tests/test_cli_dispatcher.py` (`TestCliSpeak`, `TestCliSeek`)
- **Key findings**:
  - In `handle_speak` (`cli.py` lines 112–116), `sys.stdin.read()` is called unconditionally when `args.words` is empty. On interactive terminals (`sys.stdin.isatty() == True`), this hangs waiting for EOF, blocking `--source-hash` cache lookup and failing to exit fast on empty input.
  - Adding safe `is_tty` detection allows branching: on TTY with `--source-hash`, `text = ""` immediately triggers cache lookup without touching stdin; on TTY without input/hash, immediate exit 2 (`EXIT_INVALID_INPUT`); when piped (`not is_tty`), stdin is read and non-empty text speaks while empty text proceeds to cache lookup.
  - In `handle_seek`, target validation must precede `_has_active_session()` check so invalid target syntax returns exit code 4 (`EXIT_BAD_ARG`) even when no session is active.
- **Unexplored areas**: None; all required scopes verified and tested via simulation.

## Key Decisions Made
- Formulated exact drop-in code recommendations for `cli.py` (`handle_speak` and `handle_seek`) and unit tests for `tests/test_cli_dispatcher.py`.

## Artifact Index
- DISPATCH.md — incoming dispatch instructions
- BRIEFING.md — persistent working memory
- progress.md — liveness heartbeat
- handoff.md — structured handoff report
