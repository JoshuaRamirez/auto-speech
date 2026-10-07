## 2026-10-06T23:31:30Z
You are teamwork_preview_explorer (explorer_m3_r2_2).
Your dedicated working directory is: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m3_r2_2

MANDATORY FIRST STEP: Read the user request verbatim in:
/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md (under header `## 2026-10-06T20:47:50Z`).
Also read:
/Users/joshua/Developer/auto-speech/PROJECT.md.

Note: Code changes will be applied inside the isolated git worktree at `/Users/joshua/Developer/auto-speech-worktree`.

Objective:
Formulate the concrete reconciliation strategy for all 21 slash commands (`plugin/commands/` [8 curated] and `plugin/commands-extra/` [13 extras]) for Milestone M3.

Investigate and specify:
1. Examine all command files in `plugin/commands/` (8 curated) and `plugin/commands-extra/` (13 extras):
   - How are they currently structured?
   - How each command should be updated to delegate to `auto-speech <subcommand>` (e.g. `"$PROJECT_ROOT/bin/auto-speech" speak ...`, `"$PROJECT_ROOT/bin/auto-speech" pause`, `"$PROJECT_ROOT/bin/auto-speech" doctor`).
2. Installer and test invariants:
   - Verify `setup/install-plugin.sh`, `setup/uninstall.sh`, and `tests/test_install_plugin.sh`.
   - Ensure the 12 test assertions in `tests/test_install_plugin.sh` remain 100% green:
     * curated install links exactly 8 commands
     * root file recorded
     * links ~/.local/bin/auto-speech
     * --with-extras links 21 commands
     * no hardcoded absolute project paths in command files
3. Provide an exact, comprehensive mapping of changes for all 21 command markdown files.

Do NOT implement code directly.
Write your analysis to:
/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m3_r2_2/analysis.md
and handoff report to:
/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m3_r2_2/handoff.md
Send a completion message back to caller when done.
