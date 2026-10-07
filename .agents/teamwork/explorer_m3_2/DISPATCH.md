## 2026-10-06T23:06:02Z

You are teamwork_preview_explorer (explorer_m3_2).
Your dedicated working directory is: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m3_2

MANDATORY FIRST STEP: Read the user request verbatim in:
/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md (under header `## 2026-10-06T20:47:50Z`).
Also read:
/Users/joshua/Developer/auto-speech/PROJECT.md.

Note: Target worktree is `/Users/joshua/Developer/auto-speech-worktree`.

Objective:
Formulate the concrete reconciliation strategy for all 21 slash commands (`plugin/commands/` and `plugin/commands-extra/`) for Milestone M3.

Investigate and specify:
1. Examine all command files in `plugin/commands/` (8 curated) and `plugin/commands-extra/` (13 extras):
   - How are they currently structured? (markdown instructions invoking shell commands, or shell scripts?)
   - How should they be updated to delegate to `auto-speech <subcommand>` (e.g. `/auto-speech-speak`, `/auto-speech-pause`, `/auto-speech-doctor`, etc.)?
2. Installer and test invariants:
   - Examine `setup/install-plugin.sh`, `setup/uninstall.sh`, and `tests/test_install_plugin.sh`.
   - Ensure the 12 test assertions in `tests/test_install_plugin.sh` remain 100% green:
     * curated install links exactly 8 commands
     * root file recorded
     * links ~/.local/bin/auto-speech
     * --with-extras links 21 commands
     * no hardcoded absolute project paths in command files
3. Formulate the exact file changes needed across `plugin/commands/` and `plugin/commands-extra/`.

Do NOT implement code directly.
Write your analysis to `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m3_2/analysis.md`
and handoff report to `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m3_2/handoff.md`.
Send a completion message back to caller when done.
