# BRIEFING — 2026-10-06T21:33:00Z

## Mission
Implement Milestone M1 (Unified auto-speech CLI Dispatcher): bin/auto-speech bash trampoline, plugin/scripts/python/cli.py top-level router and 12 subcommands, installer symlink integration, and exhaustive unit tests.

## 🔒 My Identity
- Archetype: teamwork_preview_worker
- Roles: implementer, qa, specialist
- Working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m1
- Original parent: c05df6b8-cecd-49ba-9fb8-8fa47f977488
- Milestone: M1 (In-Process TTSEngine & NativeAudioSink)
- Archetype: teamwork_preview_worker (worker_m1)
- Roles: implementer, qa, specialist
- Working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m1
- Original parent: 4db9af0e-498d-49a5-831a-0009fd31326e
- Milestone: M1 (Unified auto-speech CLI Dispatcher)

## 🔒 Key Constraints
- File ownership: Exclusively own and edit:
  - `plugin/scripts/python/native_audio_sink.py` (NEW)
  - `plugin/scripts/python/narrator_service.py` (MODIFIED)
  - `tests/test_native_audio_sink.py` (NEW)
  - `tests/test_narrator_service.py` (MODIFIED)
- Do NOT modify files in `tests/e2e/` or any other modules.
- MANDATORY INTEGRITY WARNING: DO NOT CHEAT. All implementations must be genuine. No hardcoded test results, no dummy/facade implementations.
- Apple MLX stream affinity: Single-thread compute streams; instantiate/load TTSEngine on the `_tts_worker` thread.
- Zero legacy hacks: Remove run_speak.sh, MpvController, SessionDir, wave duration calculation, time.sleep(), SIGKILL/pkill.
- All code changes and tests MUST be executed inside the isolated git worktree: `/Users/joshua/Developer/auto-speech-worktree`.
- Implement `bin/auto-speech` with recursion guard, `.venv` discovery, stderr diagnostic on missing venv (exit 1), execs `cli.py`. Permissions `chmod +x`.
- Implement `plugin/scripts/python/cli.py` with top-level `argparse` and all 12 subcommands: `speak`, `replay`, `pause`, `resume`, `stop`, `restart`, `seek`, `autoplay`, `narrate`, `hook`, `doctor`, `update`.
- Standardized exit codes: 0 (OK/clean mute), 1 (general fail/doctor FAIL), 2 (no session/invalid input), 3 (IPC fail), 4 (bad arg), 6 (playback fail), 130 (interrupted).
- Update `setup/install-plugin.sh` and `setup/uninstall.sh` for `~/.local/bin/auto-speech` symlink.
- Implement comprehensive unit tests in `tests/test_cli_dispatcher.py` and update `tests/test_install_plugin.sh`.

## Current Parent
- Conversation ID: 4db9af0e-498d-49a5-831a-0009fd31326e
- Updated: 2026-10-06T21:33:00Z

## Task Summary
- **What was built**:
  1. `bin/auto-speech` bash trampoline in `/Users/joshua/Developer/auto-speech-worktree/bin/auto-speech` resolving project root across symlinks with recursion guard, verifying `.venv`, and execing `cli.py "$@"`.
  2. `plugin/scripts/python/cli.py` implementing top-level parser and all 12 subcommands (`speak`, `replay`, `pause`, `resume`, `stop`, `restart`, `seek`, `autoplay`, `narrate`, `hook`, `doctor`, `update`).
  3. `setup/install-plugin.sh` and `setup/uninstall.sh` managing `~/.local/bin/auto-speech` symlink portably.
  4. User symlink `~/.local/bin/auto-speech` created and tested.
  5. `tests/test_cli_dispatcher.py` with 74 tests covering all 12 subcommands and edge cases.
  6. `tests/test_install_plugin.sh` expanded with symlink and portability assertions (12/12 passed).
  7. Registered `test_cli_dispatcher.py` in `tests/run_all.sh` (50/50 test suites passed).
- **Success criteria**:
  - `bin/auto-speech --help` and `~/.local/bin/auto-speech --help` exit 0 (VERIFIED).
  - `.venv/bin/python -m unittest tests/test_cli_dispatcher.py` passes 74/74 (VERIFIED).
  - `bash tests/test_install_plugin.sh` passes 12/12 (VERIFIED).
  - `bash tests/run_all.sh` passes 50/50 suites (VERIFIED).
  - `.venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py` reports 0 errors (VERIFIED).

## Key Decisions Made
- Strict local root resolution in `bin/auto-speech`: no fallback to `~/.config/auto-speech/root` for python discovery to prevent worktree cross-talk.
- Pure standard library implementation in `cli.py` with lightweight delegates.
- Standardized exit code contract: 0 (OK/clean mute), 1 (general fail/doctor FAIL), 2 (no session/invalid input), 3 (IPC fail), 4 (bad arg), 6 (playback fail), 130 (interrupted).

## Artifact Index
- `.agents/teamwork/worker_m1/DISPATCH.md` — Assignment and requirements
- `.agents/teamwork/worker_m1/progress.md` — Liveness heartbeat and step tracking
- `.agents/teamwork/worker_m1/handoff.md` — Final 5-component handoff report

## Change Tracker
- **Files modified / created**:
  - `bin/auto-speech` — New executable bash trampoline
  - `plugin/scripts/python/cli.py` — New unified CLI dispatcher
  - `setup/install-plugin.sh` — Added install_cli_symlink
  - `setup/uninstall.sh` — Added CLI symlink removal
  - `tests/test_cli_dispatcher.py` — New comprehensive unit test suite (74 tests)
  - `tests/test_install_plugin.sh` — Added symlink assertions
  - `tests/run_all.sh` — Registered test_cli_dispatcher.py
  - `~/.local/bin/auto-speech` — Created symlink on user system
- **Build status**: Pass (100% test pass on all targets, 50/50 suites).
- **Pending issues**: None.

## Quality Status
- **Build/test result**: 50/50 test suites pass cleanly.
- **Lint status**: 0 violations on all new/modified files (`ruff check`).
- **Tests added/modified**: 74 new tests in `test_cli_dispatcher.py`, 2 new assertions in `test_install_plugin.sh`.

## Loaded Skills
- None
