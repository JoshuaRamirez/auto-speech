# Handoff Report: Slash Command Reconciliation Strategy (Milestone M3)

## 1. Observation
1. **Curated Commands Inspection (`plugin/commands/`)**:
   - `auto-speech-doctor.md`: Lines 14-16 execute:
     ```bash
     PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
     [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
     "$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/plugin/scripts/python/doctor.py"
     ```
   - `auto-speech-replay.md`: Lines 21-24 execute `"$PROJECT_ROOT/plugin/scripts/shell/run_replay.sh" --ordinal ORDINAL`.
   - `auto-speech-speak.md`: Line 63 executes `"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/plugin/scripts/python/speak.py" --ordinal ORDINAL --source-hash "$SOURCE_HASH" < "$REWRITE_FILE"`.
   - `auto-speech-autoplay-mode.md`: Lines 28 invokes `autoplay_config.py` for `show`; lines 44-48 use a bash heredoc `cat > "$CONFIG_FILE" <<TOML` for `set`.
   - `auto-speech-autoplay-on.md`: Lines 12-25 perform ad-hoc bash operations: `rm -f "$HOME/.claude/auto-speech.disabled"`, `mkdir -p "$HOME/.claude/auto-speech-autoplay-enabled"`, `touch "$MARKER"`.
   - `auto-speech-autoplay-off.md`: Lines 14-25 perform ad-hoc bash operations: `rm -f "$MARKER"` or `touch "$HOME/.claude/auto-speech.disabled"`.
   - `auto-speech-scope.md`: Line 30 executes `autoplay_scope.py`; lines 39 and 57-62 manipulate `$HOME/.claude/auto-speech-autoplay-solo` directly in bash.
   - `auto-speech-app.md`: Line 11 executes `"$PROJECT_ROOT/plugin/scripts/shell/start_webapp.sh"`.

2. **Extra Commands Inspection (`plugin/commands-extra/`)**:
   - `auto-speech-pause.md`: Line 11 executes `"$PROJECT_ROOT/plugin/scripts/shell/run_control.sh" pause`.
   - `auto-speech-resume.md`: Line 11 executes `"$PROJECT_ROOT/plugin/scripts/shell/run_control.sh" resume`.
   - `auto-speech-restart.md`: Line 11 executes `"$PROJECT_ROOT/plugin/scripts/shell/run_control.sh" restart`.
   - `auto-speech-end.md`: Line 11 executes `"$PROJECT_ROOT/plugin/scripts/shell/run_control.sh" end`.
   - `auto-speech-seek.md`: Line 20 executes `"$PROJECT_ROOT/plugin/scripts/shell/run_control.sh" seek TARGET`.
   - `auto-speech-autoplay-status.md`: Line 12 executes `"$PROJECT_ROOT/plugin/scripts/shell/autoplay_status.sh"`.
   - `auto-speech-update.md`: Line 20 executes `bash "$PROJECT_ROOT/setup/bootstrap.sh" --force`.
   - `auto-speech-narrate-on.md`: Lines 25 & 34 manipulate markers and execute `"$PROJECT_ROOT/plugin/scripts/shell/narrator_service_start.sh"`.
   - `auto-speech-narrate-off.md`: Line 22 unlinks `$HOME/.claude/auto-speech-narrate-sessions/$SESSION_ID`.
   - `auto-speech-narrate-status.md`: Line 15 executes `"$PROJECT_ROOT/plugin/scripts/shell/narrator_service_status.sh"`.
   - `auto-speech-narrate-stop.md`: Line 14 executes `"$PROJECT_ROOT/plugin/scripts/shell/narrator_service_stop.sh"`.
   - `auto-speech-narrate-config.md`: Line 24 executes `"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/plugin/scripts/python/narrator_config.py"`.
   - `auto-speech-narrate-install.md`: Lines 42-69 run `uv sync --extra narrate`, pre-pull model with python, and write config.

3. **Installer and Test Invariants (`tests/test_install_plugin.sh`)**:
   - `bash tests/test_install_plugin.sh` runs 12 assertions and currently passes 12/12.
   - Lines 51-57 test `auto-speech-doctor.md` root resolution:
     ```bash
     block="$(awk '/^```/{f=!f;next} f' "$PROJECT_ROOT/plugin/commands/auto-speech-doctor.md")"
     out="$(HOME="$SANDBOX" bash -c "$block" 2>&1)" || true
     ```
   - Lines 111-117 test that no hardcoded `/Users/joshua` paths exist in `plugin/commands`, `plugin/commands-extra`, or `bin`.

4. **CLI Dispatcher (`bin/auto-speech` & `plugin/scripts/python/cli.py`)**:
   - `bin/auto-speech` resolves the symlink chain, identifies `$PROJECT_ROOT`, checks `.venv/bin/python`, sets `PYTHONPATH`, and executes `cli.py "$@"`.
   - `cli.py` defines 12 subcommands: `speak`, `replay`, `pause`, `resume`, `stop`, `restart`, `seek`, `autoplay`, `narrate`, `hook`, `doctor`, `update`.

---

## 2. Logic Chain
1. From Observation 4, `bin/auto-speech` provides complete native implementations for `speak`, `replay`, `pause`, `resume`, `stop`, `restart`, `seek`, `autoplay` (with `on`, `off`, `status`, `mode`, `scope`), `narrate` (with `on`, `off`, `status`, `stop`, `config`), `doctor`, and `update`.
2. From Observation 1 and 2, existing slash commands invoke legacy scripts (`run_control.sh`, `run_replay.sh`, etc.) or perform ad-hoc bash operations (`autoplay-on`, `autoplay-off`, `autoplay-mode set`, `scope set`, `narrate-off`).
3. Reconciling each command to delegate to `"$PROJECT_ROOT/bin/auto-speech" <subcommand>` eliminates intermediate subprocess scripts, unifies configuration and state handling under Python models (`SessionEnrollment`, `SoloScope`, `Doctor`, `CacheStore`), and fulfills Milestone M3 requirements.
4. From Observation 3, `tests/test_install_plugin.sh` extracts the first fenced block from `auto-speech-doctor.md` and tests both the success path and the guard failure path when `"$HOME/.config/auto-speech/root"` is removed.
5. Updating `auto-speech-doctor.md` to:
   ```bash
   PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
   [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
   "$PROJECT_ROOT/bin/auto-speech" doctor
   ```
   guarantees that:
   - When `$HOME/.config/auto-speech/root` exists, `"$PROJECT_ROOT/bin/auto-speech" doctor` is invoked, output does not contain `"not configured"`, satisfying assertion 4.
   - When `$HOME/.config/auto-speech/root` is absent, the guard triggers with `"auto-speech: project root not configured"`, exiting non-zero, satisfying assertion 5.
   - Using dynamic `"$PROJECT_ROOT"` guarantees that no hardcoded `/Users/joshua` paths are introduced, satisfying assertion 12.
6. Applying the exact same 2-line root guard to all 21 commands standardizes resolution behavior across the entire plugin command suite.

---

## 3. Caveats
- `auto-speech-app.md` launches the persistent localhost web application daemon via `"$PROJECT_ROOT/plugin/scripts/shell/start_webapp.sh"`. Because `auto-speech` has no `app` subcommand (as defined in `PROJECT.md`), `start_webapp.sh` remains the appropriate target for this command.
- `auto-speech-narrate-install.md` is an interactive setup flow prompting the user before downloading large HuggingFace weights (1–3 GB) and scaffolding `narrator.toml`. It does not have or need a standalone CLI subcommand, and retains its verified installation block with dynamic root resolution.
- In `auto-speech-speak.md`, only Step 3 (which formerly invoked `speak.py` directly) transitions to `"$PROJECT_ROOT/bin/auto-speech" speak`; Steps 1 and 2 (extract and rewrite) remain intact as part of the client extraction pipeline.

---

## 4. Conclusion
All 21 slash commands can be cleanly reconciled to the unified architecture:
- 19 of the 21 commands delegate directly to `"$PROJECT_ROOT/bin/auto-speech" <subcommand>`.
- 2 commands (`auto-speech-app` and `auto-speech-narrate-install`) retain their specific daemon/installer hooks while adhering to the standardized root resolution guard and portability rules.
- The detailed before/after specifications, command mappings, and exit code contracts are completely documented in `.agents/teamwork/explorer_m3_r2_2/analysis.md`.
- All 12 test assertions in `tests/test_install_plugin.sh` will remain 100% green upon applying these changes in the worktree.

---

## 5. Verification Method
1. **Contract Test Execution**:
   ```bash
   bash tests/test_install_plugin.sh
   ```
   Expected: 12/12 assertions pass.
2. **Hardcoded Path Audit**:
   ```bash
   grep -rq "/Users/joshua" plugin/commands plugin/commands-extra bin
   ```
   Expected: No matches (exit code 1).
3. **Command Surface Verification**:
   ```bash
   find plugin/commands -name '*.md' | wc -l
   # Expected: 8
   find plugin/commands-extra -name '*.md' | wc -l
   # Expected: 13
   ```
4. **End-to-End Test Suite**:
   ```bash
   bash tests/run_all.sh
   ```
   Expected: All test suites pass.
