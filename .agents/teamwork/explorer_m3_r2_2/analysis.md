# Analysis: Slash Command Reconciliation Strategy (Milestone M3)

**Author**: `teamwork_preview_explorer` (explorer_m3_r2_2)  
**Date**: 2026-10-06  
**Target Environment**: Git worktree `/Users/joshua/Developer/auto-speech-worktree`  
**Reference Document**: `PROJECT.md`, `setup/install-plugin.sh`, `tests/test_install_plugin.sh`

---

## 1. Executive Summary & Problem Boundary

In Milestones M1 and M2, the auto-speech architecture gained two primary operational surfaces:
1. A unified CLI dispatcher (`bin/auto-speech` bash trampoline backed by `plugin/scripts/python/cli.py`), supporting 12 subcommands (`speak`, `replay`, `pause`, `resume`, `stop`, `restart`, `seek`, `autoplay`, `narrate`, `hook`, `doctor`, `update`).
2. An expanded 6-tool Model Context Protocol (MCP) server (`mcp_server.py`).

Milestone M3 requires **Hook Integration & Architecture Coherence**, specifically reconciling all 21 slash commands (`plugin/commands/` [8 curated] and `plugin/commands-extra/` [13 extras]) to delegate into the unified `auto-speech` CLI dispatcher rather than executing disparate legacy shell scripts (`run_control.sh`, `run_replay.sh`, `autoplay_status.sh`, etc.) or manipulating filesystem marker files via ad-hoc bash.

This analysis provides:
- A complete audit of all 21 command markdown files.
- The exact delegation mapping to `"$PROJECT_ROOT/bin/auto-speech" <subcommand>`.
- Verification of installer invariants ensuring that all 12 test assertions in `tests/test_install_plugin.sh` remain 100% green.
- Verbatim before/after blocks and response contracts for every single command.

---

## 2. Invariants & Architectural Principles

### 2.1 The Two Fundamental Execution Environments
1. **Interactive Claude Code / Terminal Sessions**: Commands are invoked by the model reading the prompt in `.claude/commands/auto-speech-*.md` and executing the fenced bash block.
2. **Contract Test Harness (`tests/test_install_plugin.sh`)**: The test harness creates an isolated temporary directory sandbox (`HOME="$SANDBOX"`), runs `install-plugin.sh`, extracts the bash block from `auto-speech-doctor.md`, and runs it with `HOME="$SANDBOX"`.

### 2.2 Invariant 1: Dynamic Project Root Resolution & Guard
Command markdown files are version-controlled templates symlinked into `$HOME/.claude/commands/`. They cannot contain hardcoded paths (e.g. `/Users/joshua/...`). Furthermore, inside subshells or testing sandboxes, `~/.local/bin` may not be in `$PATH`, or may point to an older installation.

Therefore, **every command executing project code must use the canonical 2-line root resolution guard**:
```bash
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
```
Then, it invokes the unified trampoline via:
```bash
"$PROJECT_ROOT/bin/auto-speech" <subcommand> [<args>]
```

### 2.3 Invariant 2: Test Harness Contract (`test_install_plugin.sh`)
The 12 assertions in `tests/test_install_plugin.sh` enforce:
1. `curated install links exactly 8 commands`: `KEEP` array in `install-plugin.sh` has 8 items.
2. `root file recorded with the clone path`: `$HOME/.config/auto-speech/root` matches `$PROJECT_ROOT`.
3. `curated install links ~/.local/bin/auto-speech`: Symlink points to `$PROJECT_ROOT/bin/auto-speech`.
4. `doctor block resolves root from ~/.config/auto-speech/root`: Extracts the first fenced block from `auto-speech-doctor.md` via `awk '/^```/{f=!f;next} f'`, runs it in sandbox, and verifies output does NOT contain `"not configured"`.
5. `guard fires when root file is absent`: Deletes `$SANDBOX/.config/auto-speech/root`, runs doctor block, verifies exit non-zero AND output contains `"not configured"`.
6. `--with-extras links 21 commands`: Exactly 8 curated + 13 extras = 21 links.
7. `plain re-run preserves opted-in extras`: Extras survive idempotent re-run.
8. `prune removes non-keep-set links into plugin/commands/`: Stale curated links pruned.
9. `prune removes dangling links into plugin/commands-extra/`: Stale extras links pruned.
10. `uninstall removes the recorded root file`: `uninstall.sh` removes `$HOME/.config/auto-speech/root`.
11. `uninstall removes ~/.local/bin/auto-speech symlink`: `uninstall.sh` unlinks `~/.local/bin/auto-speech`.
12. `no hardcoded absolute project paths in command files`: `grep -rq "/Users/joshua"` across `plugin/commands`, `plugin/commands-extra`, `bin` returns 0 matches.

Reconciliation strictly preserves all 12 assertions.

---

## 3. Forensic Audit of All 21 Commands

| # | Command Name | Subdir | Current Target | Reconciled Target (`auto-speech`) | Rationale |
|---|--------------|--------|----------------|-----------------------------------|-----------|
| 1 | `auto-speech-doctor` | curated | `python doctor.py` | `"$PROJECT_ROOT/bin/auto-speech" doctor` | Full parity, preserves test contract for assertions 4 & 5. |
| 2 | `auto-speech-replay` | curated | `run_replay.sh --ordinal N` | `"$PROJECT_ROOT/bin/auto-speech" replay --ordinal N` | Replaces `run_replay.sh` subprocess hop. |
| 3 | `auto-speech-speak` | curated | `speak.py --ordinal N --source-hash ...` | `"$PROJECT_ROOT/bin/auto-speech" speak --ordinal N --source-hash ...` | Replaces direct `speak.py` invocation in step 3 of extract-rewrite-speak pipeline. |
| 4 | `auto-speech-autoplay-mode` | curated | `autoplay_config.py` (show) / `cat > autoplay.toml` (set) | `"$PROJECT_ROOT/bin/auto-speech" autoplay mode [ARG]` | Replaces raw bash TOML heredoc and ad-hoc script with unified CLI. |
| 5 | `auto-speech-autoplay-on` | curated | Ad-hoc bash (`touch marker`, `rm disabled`) | `"$PROJECT_ROOT/bin/auto-speech" autoplay on` | Eliminates ad-hoc bash marker manipulation; uses `SessionEnrollment` & `AutoplayGate`. |
| 6 | `auto-speech-autoplay-off` | curated | Ad-hoc bash (`rm marker`, `touch disabled`) | `"$PROJECT_ROOT/bin/auto-speech" autoplay off` | Eliminates ad-hoc bash marker manipulation; uses `SessionEnrollment` & `AutoplayGate`. |
| 7 | `auto-speech-scope` | curated | `autoplay_scope.py` (show) / bash (set) | `"$PROJECT_ROOT/bin/auto-speech" autoplay scope [all\|solo]` | Collapses show & set into unified CLI with `SoloScope`. |
| 8 | `auto-speech-app` | curated | `start_webapp.sh` | Retain `start_webapp.sh` invocation with standard root guard | Web app daemon lifecycle is distinct from CLI dispatcher. |
| 9 | `auto-speech-pause` | extra | `run_control.sh pause` | `"$PROJECT_ROOT/bin/auto-speech" pause` | Replaces `run_control.sh` trampoline; direct mpv IPC. |
| 10 | `auto-speech-resume` | extra | `run_control.sh resume` | `"$PROJECT_ROOT/bin/auto-speech" resume` | Replaces `run_control.sh` trampoline; direct mpv IPC. |
| 11 | `auto-speech-restart` | extra | `run_control.sh restart` | `"$PROJECT_ROOT/bin/auto-speech" restart` | Replaces `run_control.sh` trampoline; direct mpv IPC. |
| 12 | `auto-speech-end` | extra | `run_control.sh end` | `"$PROJECT_ROOT/bin/auto-speech" stop` | Replaces `run_control.sh` trampoline; stops mpv & interrupts daemon. |
| 13 | `auto-speech-seek` | extra | `run_control.sh seek TARGET` | `"$PROJECT_ROOT/bin/auto-speech" seek TARGET` | Replaces `run_control.sh` trampoline; direct mpv IPC. |
| 14 | `auto-speech-autoplay-status` | extra | `autoplay_status.sh` | `"$PROJECT_ROOT/bin/auto-speech" autoplay status` | Standardized enrollment and scope reporting. |
| 15 | `auto-speech-update` | extra | `bash bootstrap.sh --force` | `"$PROJECT_ROOT/bin/auto-speech" update --force` | Replaces raw bash invocation with unified update subcommand. |
| 16 | `auto-speech-narrate-on` | extra | Ad-hoc bash + `narrator_service_start.sh` | `"$PROJECT_ROOT/bin/auto-speech" narrate on` | Centralized session enrollment and daemon verification. |
| 17 | `auto-speech-narrate-off` | extra | Ad-hoc bash marker unlink | `"$PROJECT_ROOT/bin/auto-speech" narrate off` | Centralized session withdrawal. |
| 18 | `auto-speech-narrate-status` | extra | `narrator_service_status.sh` | `"$PROJECT_ROOT/bin/auto-speech" narrate status` | Centralized session enrollment & daemon alive check. |
| 19 | `auto-speech-narrate-stop` | extra | `narrator_service_stop.sh` | `"$PROJECT_ROOT/bin/auto-speech" narrate stop` | Centralized daemon shutdown. |
| 20 | `auto-speech-narrate-config` | extra | `python narrator_config.py` + bash edit | `"$PROJECT_ROOT/bin/auto-speech" narrate config [show\|path\|edit]` | Collapses show, path, and edit into unified CLI. |
| 21 | `auto-speech-narrate-install` | extra | `uv sync --extra narrate` + mlx download | Retain setup flow with standard root guard | Interactive MLX installation & HuggingFace download flow. |

---

## 4. Comprehensive Mapping & Specification for All 21 Commands

### 4.1 Curated Commands (`plugin/commands/`)

#### 1. `plugin/commands/auto-speech-doctor.md`
- **Frontmatter**: Unchanged.
- **Before**:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/plugin/scripts/python/doctor.py"
  ```
- **After**:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/bin/auto-speech" doctor
  ```
- **Argument handling**: If `$ARGUMENTS` (trimmed, case-insensitive) is `json`, append ` --json`.
- **Exit & Response contract**:
  - Exit 0/nonzero: Output verbatim inside a fenced code block. If FAIL, surface the single most actionable remedy.

#### 2. `plugin/commands/auto-speech-replay.md`
- **Frontmatter**: Unchanged.
- **Before**:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/plugin/scripts/shell/run_replay.sh" --ordinal ORDINAL
  ```
- **After**:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/bin/auto-speech" replay --ordinal ORDINAL
  ```
- **Argument handling**: Default `ORDINAL=1`; positive integer if provided.
- **Exit & Response contract**:
  - Exit 0: `replayed cached entry #ORDINAL`
  - Exit 2: stderr message verbatim ("no cache entries" or "only K entries; you asked for Nth")
  - Other exits: `replay failed: exit <code>` and stderr output.

#### 3. `plugin/commands/auto-speech-speak.md`
- **Frontmatter**: Unchanged.
- **Before** (Step 3 in pipeline):
  ```bash
  "$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/plugin/scripts/python/speak.py" \
      --ordinal ORDINAL --source-hash "$SOURCE_HASH" < "$REWRITE_FILE" >>"$LOG" 2>&1 \
      || dump_log_and_fail $? speak
  ```
- **After** (Step 3 in pipeline):
  ```bash
  "$PROJECT_ROOT/bin/auto-speech" speak \
      --ordinal ORDINAL --source-hash "$SOURCE_HASH" < "$REWRITE_FILE" >>"$LOG" 2>&1 \
      || dump_log_and_fail $? speak
  ```
- **Exit & Response contract**:
  - Exit 0: `spoke message #ORDINAL — N source chars, M rewrite chars`
  - Non-zero: `speak failed at <step> (exit <code>)`.

#### 4. `plugin/commands/auto-speech-autoplay-mode.md`
- **Frontmatter**: Unchanged.
- **Before**:
  - `show` ran `"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/plugin/scripts/python/autoplay_config.py"`.
  - `set` created `$HOME/.config/auto-speech/autoplay.toml` with raw `cat > "$CONFIG_FILE" <<TOML`.
- **After**:
  - Unified into single bash command block:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/bin/auto-speech" autoplay mode [ARGUMENT]
  ```
- **Argument handling**:
  - Empty -> `"$PROJECT_ROOT/bin/auto-speech" autoplay mode`
  - Value (`raw`, `verbatim`, `small`, `medium`, `large`) -> `"$PROJECT_ROOT/bin/auto-speech" autoplay mode <value>`
  - Unknown -> `autoplay-mode: unknown value <value> (expected raw | verbatim | small | medium | large)`
- **Exit & Response contract**:
  - On `show`: Output resolved mode + size + config path inside fenced block.
  - On `set`: Output `autoplay mode set: <NEW_MODE>[/<NEW_SIZE>]`.

#### 5. `plugin/commands/auto-speech-autoplay-on.md`
- **Frontmatter**: Unchanged.
- **Before**: Raw bash checking `$CLAUDE_CODE_SESSION_ID`, `rm auto-speech.disabled`, `touch marker`.
- **After**:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/bin/auto-speech" autoplay on
  ```
- **Exit & Response contract**:
  - Exit 0 and contains `autoplay enabled for session <id>`: `autoplay enabled for session <id> (other sessions stay off)`.
  - Non-zero (no session ID): `global mute cleared, but CLAUDE_CODE_SESSION_ID is not set — autoplay is opt-in per session and cannot enroll without it (check that jq is installed; /auto-speech-doctor reports it)`.

#### 6. `plugin/commands/auto-speech-autoplay-off.md`
- **Frontmatter**: Unchanged.
- **Before**: Raw bash checking `$CLAUDE_CODE_SESSION_ID`, removing marker or touching `disabled`.
- **After**:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/bin/auto-speech" autoplay off
  ```
- **Exit & Response contract**:
  - If output contains `autoplay disabled for session <id>`: `autoplay disabled for session <id> (other sessions unchanged)`.
  - If output contains `set global mute marker instead`: `no session id — set global mute marker instead (~/.claude/auto-speech.disabled)`.

#### 7. `plugin/commands/auto-speech-scope.md`
- **Frontmatter**: Unchanged.
- **Before**:
  - `show` ran `autoplay_scope.py`.
  - `set all` ran `rm -f auto-speech-autoplay-solo`.
  - `set solo` wrote `$SESSION_ID` to `auto-speech-autoplay-solo` and touched enrollment marker.
- **After**:
  - Unified into:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/bin/auto-speech" autoplay scope [all|solo]
  ```
- **Argument handling**:
  - Empty: runs `"$PROJECT_ROOT/bin/auto-speech" autoplay scope`
  - `all`: runs `"$PROJECT_ROOT/bin/auto-speech" autoplay scope all`
  - `solo` / `this` / `one` / `only`: runs `"$PROJECT_ROOT/bin/auto-speech" autoplay scope solo`
- **Exit & Response contract**:
  - `show`: output verbatim on one line.
  - `all`: `autoplay scope: ALL — every enrolled session reads`.
  - `solo`: `autoplay scope: SOLO — only this session (<id>) reads; all others muted`.
  - If no session ID: `cannot solo: CLAUDE_CODE_SESSION_ID is not set in this shell`.

#### 8. `plugin/commands/auto-speech-app.md`
- **Frontmatter**: Unchanged.
- **Code Block**:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/plugin/scripts/shell/start_webapp.sh"
  ```
- **Status**: Already fully compliant with the 2-line root guard and no hardcoded paths. Retained as-is.

---

### 4.2 Extra Commands (`plugin/commands-extra/`)

#### 9. `plugin/commands-extra/auto-speech-pause.md`
- **Frontmatter**: Unchanged.
- **Before**: `"$PROJECT_ROOT/plugin/scripts/shell/run_control.sh" pause`
- **After**:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/bin/auto-speech" pause
  ```
- **Exit & Response contract**:
  - Exit 0: `paused`
  - Exit 2: `no active playback`
  - Other: `pause failed: exit <code>` and stderr output.

#### 10. `plugin/commands-extra/auto-speech-resume.md`
- **Frontmatter**: Unchanged.
- **Before**: `"$PROJECT_ROOT/plugin/scripts/shell/run_control.sh" resume`
- **After**:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/bin/auto-speech" resume
  ```
- **Exit & Response contract**:
  - Exit 0: `resumed`
  - Exit 2: `no active playback`
  - Other: `resume failed: exit <code>` and stderr output.

#### 11. `plugin/commands-extra/auto-speech-restart.md`
- **Frontmatter**: Unchanged.
- **Before**: `"$PROJECT_ROOT/plugin/scripts/shell/run_control.sh" restart`
- **After**:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/bin/auto-speech" restart
  ```
- **Exit & Response contract**:
  - Exit 0: `restarted from beginning`
  - Exit 2: `no active playback`
  - Other: `restart failed: exit <code>` and stderr output.

#### 12. `plugin/commands-extra/auto-speech-end.md`
- **Frontmatter**: Unchanged.
- **Before**: `"$PROJECT_ROOT/plugin/scripts/shell/run_control.sh" end`
- **After**:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/bin/auto-speech" stop
  ```
- **Exit & Response contract**:
  - Exit 0: `playback ended`
  - Exit 2: `no active playback`
  - Other: `end failed: exit <code>` and stderr output.

#### 13. `plugin/commands-extra/auto-speech-seek.md`
- **Frontmatter**: Unchanged.
- **Before**: `"$PROJECT_ROOT/plugin/scripts/shell/run_control.sh" seek TARGET`
- **After**:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/bin/auto-speech" seek TARGET
  ```
- **Exit & Response contract**:
  - Exit 0: `seeked to TARGET`
  - Exit 2: `no active playback`
  - Exit 4: `seek: bad target '<value>'`
  - Other: `seek failed: exit <code>` and stderr output.

#### 14. `plugin/commands-extra/auto-speech-autoplay-status.md`
- **Frontmatter**: Unchanged.
- **Before**: `"$PROJECT_ROOT/plugin/scripts/shell/autoplay_status.sh"`
- **After**:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/bin/auto-speech" autoplay status
  ```
- **Exit & Response contract**: Output verbatim inside a fenced code block.

#### 15. `plugin/commands-extra/auto-speech-update.md`
- **Frontmatter**: Unchanged.
- **Before**: `bash "$PROJECT_ROOT/setup/bootstrap.sh" --force`
- **After**:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/bin/auto-speech" update --force
  ```
- **Exit & Response contract**:
  - Exit 0: confirm venv is now reconciled with lock.
  - Failure: surface `/tmp/auto-speech-sync.log` tail.

#### 16. `plugin/commands-extra/auto-speech-narrate-on.md`
- **Frontmatter**: Unchanged.
- **Before**: Ad-hoc bash script creating marker and calling `narrator_service_start.sh`.
- **After**:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/bin/auto-speech" narrate on
  ```
- **Exit & Response contract**:
  - Exit 0: `narrate on — session <session-id>, daemon running`
  - Failure: surface stderr verbatim.

#### 17. `plugin/commands-extra/auto-speech-narrate-off.md`
- **Frontmatter**: Unchanged.
- **Before**: Ad-hoc bash script unlinking marker file.
- **After**:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/bin/auto-speech" narrate off
  ```
- **Exit & Response contract**:
  - Exit 0: `narrate off — session <session-id>, <message>`

#### 18. `plugin/commands-extra/auto-speech-narrate-status.md`
- **Frontmatter**: Unchanged.
- **Before**: `"$PROJECT_ROOT/plugin/scripts/shell/narrator_service_status.sh"`
- **After**:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/bin/auto-speech" narrate status
  ```
- **Exit & Response contract**: Full output verbatim inside fenced code block.

#### 19. `plugin/commands-extra/auto-speech-narrate-stop.md`
- **Frontmatter**: Unchanged.
- **Before**: `"$PROJECT_ROOT/plugin/scripts/shell/narrator_service_stop.sh"`
- **After**:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/bin/auto-speech" narrate stop
  ```
- **Exit & Response contract**: One-line status from stdout.

#### 20. `plugin/commands-extra/auto-speech-narrate-config.md`
- **Frontmatter**: Unchanged.
- **Before**:
  - Step 1: `python narrator_config.py`
  - Step 2: bash parser for show / edit / path.
- **After**:
  - Unified into:
  ```bash
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  "$PROJECT_ROOT/bin/auto-speech" narrate config ACTION
  ```
- **Argument handling**:
  - Empty or `show` -> `"$PROJECT_ROOT/bin/auto-speech" narrate config show`
  - `edit` -> `"$PROJECT_ROOT/bin/auto-speech" narrate config edit`
  - `path` -> `"$PROJECT_ROOT/bin/auto-speech" narrate config path`
- **Exit & Response contract**:
  - `show`: TOML output inside fenced block.
  - `path`: single-line path verbatim.
  - `edit`: `opened <config_path> in <editor>`.

#### 21. `plugin/commands-extra/auto-speech-narrate-install.md`
- **Frontmatter**: Unchanged.
- **Code Block**:
  ```bash
  set -euo pipefail
  PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
  [ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
  VENV="$PROJECT_ROOT/.venv"
  MODEL='<MODEL>'

  ( cd "$PROJECT_ROOT" && uv sync --extra narrate )
  "$VENV/bin/python" -c "
  from mlx_lm import load
  print('downloading and loading model:', '$MODEL')
  load('$MODEL')
  print('model ready')
  "
  ...
  ```
- **Status**: Already uses standard root guard and dynamic paths. Retained as-is.

---

## 5. Verification Plan & Test Gate

To ensure zero regressions across the codebase:
1. **Installer Contract Tests**:
   ```bash
   bash tests/test_install_plugin.sh
   ```
   Must pass all 12/12 assertions.
2. **Portability Check**:
   ```bash
   grep -rq "/Users/joshua" plugin/commands plugin/commands-extra bin
   ```
   Must return 0 occurrences (exit 1).
3. **Dispatcher Unit Tests**:
   ```bash
   .venv/bin/pytest tests/test_cli_dispatcher.py -v
   ```
4. **Overall Test Runner**:
   ```bash
   bash tests/run_all.sh
   ```
   Must pass all suites without errors.
