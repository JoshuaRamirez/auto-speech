## 2026-10-06T20:50:56Z
From: 4db9af0e-498d-49a5-831a-0009fd31326e (parent)
Objective: Survey shell hooks, test harness infrastructure, and git worktree procedures in `/Users/joshua/Developer/auto-speech` for R3, R4, and verification.

Investigate:
1. Existing shell hooks: `plugin/scripts/shell/autoplay_hook.sh`, `plugin/scripts/shell/narrator_hook.sh`, and `~/.local/bin/agent-event-router` or related hook routers. How they interact with Claude Code and Antigravity hooks (`Stop`, `PreToolUse`, `PostToolUse`, `UserPromptSubmit`).
2. How hooks should delegate cleanly into the unified dispatcher or shared domain logic per R3 without breaking existing slash commands or configurations.
3. Test suite structure: examine `bash tests/run_all.sh`, `tests/test_mcp_server.py`, `tests/test_cli_rewrite.py`, `tests/test_control.py`, and E2E harnesses. Verify what virtual environment exists (`.venv/`), what test frameworks are used (unittest/pytest), and how tests are executed.
4. Git worktree configuration: verify git status, branch structure, and exact commands for creating `worktree/cli-mcp-parity`, setting up virtual environments in worktrees if needed, and merging back cleanly.
