# BRIEFING — 2026-10-06T21:15:30Z

## Mission
Formulate implementation strategy for bin/auto-speech bash trampoline, ~/.local/bin/auto-speech symlink, and installer/uninstaller synchronization for Milestone M1.

## 🔒 My Identity
- Archetype: explorer
- Roles: Teamwork explorer
- Working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_2
- Original parent: 4db9af0e-498d-49a5-831a-0009fd31326e
- Milestone: M1

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- Target worktree is /Users/joshua/Developer/auto-speech-worktree
- Write analysis only to .agents/teamwork/explorer_m1_2/
- Keep tests/test_install_plugin.sh green (100%)

## Current Parent
- Conversation ID: 4db9af0e-498d-49a5-831a-0009fd31326e
- Updated: not yet

## Investigation State
- **Explored paths**:
  - `bin/auto-speech` design and symlink canonicalization
  - `/Users/joshua/Developer/auto-speech-worktree/.venv` symlink structure
  - `setup/install-plugin.sh` and `setup/uninstall.sh`
  - `tests/test_install_plugin.sh` contract assertions (10/10 green baseline)
  - PATH discovery and macOS environment (`~/.local/bin` on PATH)
- **Key findings**:
  - `bin/auto-speech` must resolve its true location across symlinks via POSIX `readlink` loop with a recursion guard (`MAX_HOPS=20`).
  - `.venv/bin/python` must be resolved relative to `$PROJECT_ROOT` without fallback to `~/.config/auto-speech/root` to prevent silent cross-talk between the worktree and main clone.
  - Missing venv emits formatted error to stderr and exits 1.
  - `exec "$VENV_PYTHON" "$CLI_PY" "$@"` replaces the shell process, preserving signals, exit codes, and stdin/stdout streams.
  - `setup/install-plugin.sh` links `~/.local/bin/auto-speech` portably, respecting `$HOME` for sandbox isolation.
  - `setup/uninstall.sh` removes the symlink only if it matches `"$PROJECT_ROOT/"*`.
  - `tests/test_install_plugin.sh` retains all 10 existing assertions green and adds 3 new contract assertions (13/13 passing).
- **Unexplored areas**: None for M1 trampoline/installer scope. Implementation will be executed by `worker_m1`.

## Key Decisions Made
- Strict repository-relative venv resolution (no `~/.config/auto-speech/root` fallback in trampoline).
- Scoped uninstall protection (`case "$target" in "$PROJECT_ROOT/"*)`).
- Sandbox isolation preserved by using `$HOME/.local/bin`.

## Artifact Index
- `DISPATCH.md` — incoming parent dispatch record
- `BRIEFING.md` — persistent working memory
- `progress.md` — heartbeat progress log
- `analysis.md` — comprehensive specification and ready-to-apply diff patches
- `handoff.md` — 5-component hard handoff report
