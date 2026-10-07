# Survey Analysis: Shell Hooks, Test Harness Infrastructure, and Git Worktree Procedures

**Surveyor**: `explorer_survey_3`  
**Working Directory**: `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_3`  
**Date**: 2026-10-06  
**Target Milestone**: R1, R2, R3, R4, R5 Parity & Dispatcher Architecture

---

## Executive Summary

This survey investigates the shell hook ecosystem, test harness execution models, and Git worktree operational procedures for `auto-speech`. The goal is to provide the architectural foundation for:
1. Reconciling shell entrypoints (`autoplay_hook.sh`, `narrator_hook.sh`, `agent-event-router`) to delegate cleanly into a unified CLI dispatcher and shared domain logic (R3).
2. Supporting full lifecycle hook parity via the expanded MCP server (R2).
3. Establishing an ironclad, isolated Git worktree procedure with bidirectional merge reconcile (R4).
4. Verifying test suite execution across unit, integration, and E2E tiers.

---

## 1. Shell Hooks & Hook Routers Analysis

### 1.1 Existing Hook Files and Invocations

The repository and operator system contain three key hook mechanisms:

| Hook File / Mechanism | Event Scope | Caller / Registration | Current Execution Path |
|---|---|---|---|
| `plugin/scripts/shell/autoplay_hook.sh` | `Stop` | `~/.claude/settings.json` via `setup/install-hook.sh` OR `~/.local/bin/agent-event-router --event Stop --action autoplay` | Fast bash pre-filter (~50ms) -> detached Python double-fork -> `autoplay_worker.sh` -> `autoplay_worker.py` |
| `plugin/scripts/shell/narrator_hook.sh` | `PreToolUse`, `PostToolUse`, `Stop`, `UserPromptSubmit` | `~/.claude/settings.json` via `setup/install-narrator-hooks.sh` | Pure bash + `jq` -> appends decorated JSONL line to `/tmp/auto-speech-narrator-events.jsonl` |
| `~/.local/bin/agent-event-router` | System-wide multiplexer (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PermissionRequest`, `Stop`) | `~/.claude/settings.json` | Reads stdin payload, executes case statement on `--event` and `--action`, pipes stdin to target script |
| `.agents/plugins/auto-speech/hooks.json` | `Stop` | Antigravity plugin manifest | Calls `~/.local/bin/agent-event-router --event Stop --action autoplay` (timeout: 15s) |

### 1.2 Inspection of `plugin/scripts/shell/autoplay_hook.sh`
- **Location**: `plugin/scripts/shell/autoplay_hook.sh:1-165`
- **Payload Parsing (Lines 34–40)**:
  ```bash
  PAYLOAD="$(cat)"
  TRANSCRIPT_PATH=""
  SESSION_ID=""
  if command -v jq >/dev/null 2>&1; then
      TRANSCRIPT_PATH="$(printf '%s' "$PAYLOAD" | jq -r '(.transcript_path // .transcriptPath) // ""' 2>/dev/null || true)"
      SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r '(.session_id // .conversationId) // ""' 2>/dev/null || true)"
  fi
  ```
  *Key Observation*: Already handles both Claude Code (`transcript_path`, `session_id`) and Antigravity (`transcriptPath`, `conversationId`).
- **Gating Sequence**:
  1. `AUTO_SPEECH_SUPPRESS_HOOKS == 1` (Lines 47–49): Bails immediately to break nested `claude -p` rewriter recursion.
  2. Global mute marker (Lines 53–55): Bails if `~/.claude/auto-speech.disabled` exists.
  3. Solo spotlight marker (Lines 63–69): If `~/.claude/auto-speech-autoplay-solo` exists and differs from `SESSION_ID`, bails.
  4. Per-session opt-in enrollment (Lines 81–84): Requires `~/.claude/auto-speech-autoplay-enabled/$SESSION_ID` to exist; empty `SESSION_ID` exits 0 (silent default).
- **Daemon Liveness Check (Lines 87–104)**:
  Checks `/tmp/auto-speech-narrator-daemon.pid` using `narrator_pid_is_ours "$DAEMON_PID"` from `daemon_pid.sh`. If inactive, spawns `narrator_service_start.sh`.
- **Staleness Beacon (Lines 109–129)**:
  Touches per-session beacon `/tmp/auto-speech-last-stop.$SESSION_ID` and captures sub-second mtime `BEACON_MTIME` (`stat -f %Fm` on BSD/macOS).
- **Detached Worker Launch (Lines 137–162)**:
  Uses Python inline double-fork + `os.setsid()` to detach `autoplay_worker.sh` cleanly without blocking the shell hook (<100ms total hook execution).

### 1.3 Inspection of `plugin/scripts/shell/narrator_hook.sh`
- **Location**: `plugin/scripts/shell/narrator_hook.sh:1-123`
- **Payload Ingestion (Lines 20–45)**:
  ```bash
  PAYLOAD="$(cat)"
  if [[ "${AUTO_SPEECH_SUPPRESS_HOOKS:-}" == "1" ]]; then exit 0; fi
  SESSION_ID=""
  if command -v jq >/dev/null 2>&1; then
      SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r '.session_id // ""' 2>/dev/null || true)"
  fi
  if [[ -z "$SESSION_ID" ]]; then
      SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}"
  fi
  if [[ -z "$SESSION_ID" ]]; then exit 0; fi
  MARKER="$HOME/.claude/auto-speech-narrate-sessions/$SESSION_ID"
  if [[ ! -e "$MARKER" ]]; then exit 0; fi
  ```
  *Critical Observation*: Lines 38–39 only check `.session_id`. Unlike `autoplay_hook.sh`, it does **not** check `.conversationId`. In Antigravity sessions where `conversationId` is provided instead of `session_id`, `narrator_hook.sh` will fail to extract `SESSION_ID` and silently exit 0.
  *Remediation*: Update line 38 to `(.session_id // .conversationId) // ""`.
- **Event Logging (Lines 86–121)**:
  Assembles `{ts, cwd, pid, event: (.hook_event_name // "unknown"), payload: .}` and appends to `/tmp/auto-speech-narrator-events.jsonl`.
- **Performance Invariant**:
  Because `PreToolUse` fires multiple times per turn, `narrator_hook.sh` avoids starting Python interpreters on the fast path; it relies on fast bash checks and exits immediately when un-enrolled.

### 1.4 Inspection of `~/.local/bin/agent-event-router`
- **Location**: `~/.local/bin/agent-event-router:1-114`
- Under `--event Stop`:
  ```bash
  Stop)
      if [ "$ACTION" == "autoplay" ]; then
          run_hook "~/Developer/auto-speech/plugin/scripts/shell/autoplay_hook.sh"
      ...
  ```
- Reads standard input into `INPUT` and feeds it downstream via `echo "$INPUT" | eval "$cmd"`.
- This ensures full payload transmission without socket corruption or dropped arguments.

---

## 2. Hook Delegation into Unified Dispatcher (R3 Architecture)

### 2.1 The Problem: Divergence vs Coherence
Currently, hook handling, slash command execution, and MCP calls have separate entrypoints:
- `autoplay_hook.sh` and `autoplay_worker.sh` -> `autoplay_worker.py`
- Slash commands -> inline multi-step shell snippets in `plugin/commands/*.md`
- MCP server -> only `speak` tool in `mcp_server.py`

### 2.2 Unified Dispatcher Design (`auto-speech` CLI)
To satisfy R1 and R3 without breaking existing installations:
1. Provide a single executable dispatcher script linked to `~/.local/bin/auto-speech` (and committed at `bin/auto-speech` or `plugin/scripts/shell/auto-speech`).
2. The dispatcher invokes Python domain commands via `.venv/bin/python`:
   - `auto-speech speak [text]` -> delegates to `plugin/scripts/python/speak.py` / `DaemonClient.speak`
   - `auto-speech replay [--ordinal N]` -> delegates to `plugin/scripts/python/replay.py`
   - `auto-speech pause|resume|stop|restart|seek <target>` -> delegates to `plugin/scripts/python/control.py`
   - `auto-speech autoplay on|off|status|mode [val]|scope [val]` -> manages markers and `autoplay_config.py` / `autoplay_scope.py`
   - `auto-speech narrate on|off|status|stop|config [val]` -> manages markers, `narrator_config.py`, `narrator_service_start.sh`
   - `auto-speech hook stop|narrator|bootstrap` -> executes hook handling logic directly, accepting JSON on stdin
   - `auto-speech doctor [--json]` -> delegates to `plugin/scripts/python/doctor.py`
   - `auto-speech update` -> delegates to `plugin/scripts/python/self_update.py`

### 2.3 Preserving Slash Commands and Contract Invariants
Contract test `tests/test_install_plugin.sh` verifies strict invariants:
- Curated install links **exactly 8 commands** from `plugin/commands/`.
- `--with-extras` links **exactly 21 commands** (`plugin/commands/` + `plugin/commands-extra/`).
- Markdown command blocks resolve `PROJECT_ROOT` from `~/.config/auto-speech/root`.
- No hardcoded `/Users/joshua` paths allowed.

**Preservation Strategy**:
- Do **not** delete or rename the `.md` files in `plugin/commands/` or `plugin/commands-extra/`.
- Ensure the commands either continue executing their existing bash blocks or invoke `auto-speech <subcommand>`.
- Keep `autoplay_hook.sh` and `narrator_hook.sh` as fast shell entrypoints that can delegate into `auto-speech hook stop` and `auto-speech hook narrator`, maintaining sub-50ms gating.

---

## 3. Test Harness Infrastructure & Execution Model

### 3.1 Python Virtual Environment
- **Path**: `/Users/joshua/Developer/auto-speech/.venv`
- **Python**: `Python 3.12.13`
- **Ruff**: `ruff 0.15.18`
- **Dependencies**: Managed strictly via `uv.lock` and `pyproject.toml`.
  Includes: `mlx-audio>=0.4.2`, `misaki[en]>=0.9.4`, `num2words>=0.5.14`, `flask>=3.0`, `mlx-lm>=0.31.1` (under optional extra `narrate`), and `en_core_web_sm` spaCy model.

### 3.2 Test Suites Breakdown

#### A. Main Test Runner (`bash tests/run_all.sh`)
- Does **not** require pytest; runs tests via direct invocation (`$PYTHON "$path"` and `bash "$path"`).
- Test categories in `tests/run_all.sh`:
  1. `CHEAP` (37 test suites): Fast unit tests covering state machines, logging, doctor, gate, scope, config, selector, rewriter, daemon client, audio sink.
  2. `SHELL_TESTS` (8 test suites): Shell helper tests (`test_autoplay_shim.sh`, `test_narrator_hook.sh`, `test_compute_hash.sh`, `test_install_idempotent.sh`, `test_install_plugin.sh`, etc.).
  3. `WEB` (1 test suite): `test_synthesize_endpoint.py`.
  4. `HEAVY` (5 test suites): `test_chunk_planner.py`, `test_wav_concat.py`, `test_resilient_synthesizer.py`, `test_segment_producer.py`, `test_playback_consumer.py`.
- **Baseline Run Result**:
  Executed `bash tests/run_all.sh`:
  ```
  ====================
  ran:    49
  failed: 0
  all tests passed
  ```

#### B. End-to-End Test Suite (`tests/e2e/run_e2e.py`)
- Test framework: Python `unittest`.
- Total tests: 74 tests across 5 tiers:
  - Tier 1: In-process AudioSink & Thin client IPC
  - Tier 2: Boundary conditions
  - Tier 3: Combinations & concurrency
  - Tier 4: Scenarios
  - Tier 5: Adversarial tests (Audio sink stress, socket lifecycle, backpressure, queue flooding, interruptions).
- **Execution**:
  - Full suite: `.venv/bin/python tests/e2e/run_e2e.py --tier all`
  - Tier 5 run: `.venv/bin/python tests/e2e/run_e2e.py --tier 5` passed 33/33 tests in 18.61s.

#### C. Code Quality Gates
- Linter: `.venv/bin/ruff check .`
- Test gate: `bash tests/run_all.sh` (must be 0 failures).

---

## 4. Git Worktree Operational Procedures (R4 Specification)

Per user directive and R4 requirements:
All development, testing, and verification must take place in an isolated Git worktree:
`worktree/cli-mcp-parity`.

### 4.1 Current Repository State
- Branch: `main`
- Status: 10 commits ahead of origin/main.
- Tracked production/test tree is clean. Only `.agents/teamwork/` metadata files are present.

### 4.2 Step-by-Step Worktree Lifecycle

```bash
# -------------------------------------------------------------------------
# Step 1: Create isolated worktree outside main working tree
# -------------------------------------------------------------------------
git worktree add -b worktree/cli-mcp-parity /Users/joshua/Developer/auto-speech-worktree main

# -------------------------------------------------------------------------
# Step 2: Establish environment in worktree
# Symlink .venv from primary clone to avoid re-syncing 1GB+ MLX models
# -------------------------------------------------------------------------
ln -s /Users/joshua/Developer/auto-speech/.venv /Users/joshua/Developer/auto-speech-worktree/.venv

# Verify environment inside worktree
/Users/joshua/Developer/auto-speech-worktree/.venv/bin/python --version
/Users/joshua/Developer/auto-speech-worktree/.venv/bin/ruff --version

# -------------------------------------------------------------------------
# Step 3: Execute Implementation & Verification in Worktree
# -------------------------------------------------------------------------
cd /Users/joshua/Developer/auto-speech-worktree

# Implement R1 (CLI dispatcher), R2 (MCP server parity), R3 (Hook integration)
# Run tests:
bash tests/run_all.sh
.venv/bin/python tests/e2e/run_e2e.py
.venv/bin/ruff check .

# Commit changes to worktree branch
git add .
git commit -m "feat(cli,mcp): unified auto-speech dispatcher and expanded MCP server parity"

# -------------------------------------------------------------------------
# Step 4: Bidirectional Merge Reconcile
# 4a. Fetch/merge main into worktree branch first and re-verify
# -------------------------------------------------------------------------
git fetch origin main || true
git merge main --no-edit
bash tests/run_all.sh
.venv/bin/ruff check .

# -------------------------------------------------------------------------
# Step 5: Merge worktree branch back into main
# -------------------------------------------------------------------------
cd /Users/joshua/Developer/auto-speech
git merge worktree/cli-mcp-parity --no-ff -m "feat(cli,mcp): merge worktree/cli-mcp-parity into main"

# Final verification on main
bash tests/run_all.sh
.venv/bin/python tests/e2e/run_e2e.py
.venv/bin/ruff check .

# -------------------------------------------------------------------------
# Step 6: Worktree Cleanup
# -------------------------------------------------------------------------
git worktree remove /Users/joshua/Developer/auto-speech-worktree
git branch -d worktree/cli-mcp-parity
git worktree list
```

---

## 5. Risk Assessment & Mitigations

1. **Test Assertion Breakage in `test_mcp_server.py`**:
   - `test_tools_list_has_only_speak` asserts `[t["name"] for t in tools] == ["speak"]`.
   - *Mitigation*: Update the test to verify all 6 tools (`speak`, `autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`, `doctor`) with their exact schemas and properties.
2. **Slash Command Link Count Test (`test_install_plugin.sh`)**:
   - Asserts exact file counts: 8 in curated set, 21 in extras set.
   - *Mitigation*: Do not add arbitrary `.md` files to `plugin/commands/` or `plugin/commands-extra/`. Maintain existing 8 and 13 split.
3. **Hook Latency Inflation**:
   - Calling Python on `PreToolUse` for every tool step adds 100–200ms latency.
   - *Mitigation*: Maintain bash gate in `narrator_hook.sh` so un-enrolled sessions exit in <5ms.
4. **Antigravity Payload Key Divergence**:
   - Antigravity uses `conversationId` and `transcriptPath`, while Claude Code uses `session_id` and `transcript_path`.
   - *Mitigation*: Ensure all parsers (in hooks, CLI, and MCP) consistently evaluate `(.session_id // .conversationId)` and `(.transcript_path // .transcriptPath)`.
