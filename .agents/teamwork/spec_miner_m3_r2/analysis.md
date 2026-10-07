# Milestone M3 Test Specification & Verification Harness Analysis

**Date**: 2026-10-06T23:37:00Z  
**Author**: `teamwork_preview_spec_miner` (`spec_miner_m3_r2`)  
**Scope**: Milestone M3 — Hook Reconcile & Architectural Coherence  
**Working Trees Examined**:
- Canonical Repo: `/Users/joshua/Developer/auto-speech`
- Isolated Feature Worktree: `/Users/joshua/Developer/auto-speech-worktree` (`worktree/cli-mcp-parity`)

---

## 1. Executive Summary & Objective Scope

The objective of Milestone M3 is Hook Reconcile & Architectural Coherence, bridging Claude Code and Antigravity lifecycle hook conventions, unifying slash command execution through the `auto-speech` CLI dispatcher and shared domain logic, and verifying that the entire test harness passes with zero regressions.

This document mines and formalizes the complete test specification across three designated focus areas:
1. **Shell Hook Test Specifications**:
   - `tests/test_narrator_hook.sh`: Test specifications for Antigravity payloads (`{"conversationId": "conv-1"}`), dual payloads (`{"session_id": "s1", "conversationId": "c1"}`), global mute (`~/.claude/auto-speech.disabled`), and non-dict / malformed payloads.
   - `tests/test_autoplay_shim.sh`: Test specifications for Antigravity payloads (`{"conversationId": "conv-1"}`), global mute enforcement, and non-dict / malformed payloads.
2. **Slash Command & Installer Specifications**:
   - Verification of `tests/test_install_plugin.sh` contract and assertions (12/12 passing).
   - Verification that all 21 slash commands (8 curated, 13 extras) delegate to `auto-speech` / shared components cleanly without broken syntax, syntax warnings, or hardcoded paths.
3. **Test Suite Regression Verification**:
   - Verification of full suite execution via `bash tests/run_all.sh` (50 test suites across 4 tiers: cheap Python, shell, web, and heavy audio).

---

## 2. Authoritative Specification Sources

1. **`ORIGINAL_REQUEST.md`** (`## 2026-10-06T20:47:50Z` & `## 2026-10-06T23:24:37Z`):
   - §R1: Unified `auto-speech` CLI Dispatcher
   - §R2: Full Lifecycle Hook Parity via MCP Server
   - §R3: Hook Integration & Architecture Coherence
   - §R4: Isolated Worktree Execution & Bidirectional Merge Reconcile
   - §Acceptance Criteria: Zero regressions across all test suites, complete hook compatibility with Claude Code & Antigravity.
2. **`PROJECT.md`**:
   - Architecture diagram: CLI Trampoline, MCP Server, Core Python Subsystems, Unified Daemon.
   - Feature Inventory: Feature 19 (Hook & Router Reconcile), Feature 20 (Slash Command Reconcile), Feature 21 (Test Suite & Regression Hardening).
   - Milestone M3 definition: Shell hook reconcile (`autoplay_hook.sh`, `narrator_hook.sh`), 21 slash commands reconcile, invariant preservation.
3. **Shell Hook Implementations**:
   - `plugin/scripts/shell/narrator_hook.sh`
   - `plugin/scripts/shell/autoplay_hook.sh`
   - `plugin/scripts/shell/autoplay_worker.sh`
   - `plugin/scripts/shell/daemon_pid.sh`
4. **Harness & Runner Code**:
   - `tests/test_narrator_hook.sh`
   - `tests/test_autoplay_shim.sh`
   - `tests/test_install_plugin.sh`
   - `tests/run_all.sh`

---

## 3. Features Discovered

| # | Category | Feature | Description | Inputs | Outputs | Error Behavior | Discovered Via |
|---|----------|---------|-------------|--------|---------|----------------|----------------|
| 1 | Narrator Hook | Session ID Ingestion (`session_id`) | Ingests Claude Code session identifier from JSON hook payload | JSON object with `"session_id": "sid-1"` on stdin | Matches marker `$HOME/.claude/auto-speech-narrate-sessions/sid-1`, appends event | If marker absent, exits 0 without append | `plugin/scripts/shell/narrator_hook.sh:38` |
| 2 | Narrator Hook | Antigravity Ingestion (`conversationId`) | Ingests Antigravity conversation identifier when `session_id` absent | JSON object with `"conversationId": "conv-1"` on stdin | Matches marker `$HOME/.claude/auto-speech-narrate-sessions/conv-1`, appends event | If marker absent, exits 0 without append | `ORIGINAL_REQUEST §R3`, `cli.py:714` |
| 3 | Narrator Hook | Dual Payload Precedence | Resolves session identifier when both `session_id` and `conversationId` exist | JSON object with `{"session_id": "s1", "conversationId": "c1"}` | `session_id` (`s1`) takes precedence; checks marker for `s1` | Ignores `conversationId` when `session_id` is present | Dispatch objective 1 |
| 4 | Narrator Hook | Global Mute Gate | Suppresses narrator hook execution when globally muted | File `$HOME/.claude/auto-speech.disabled` exists | Immediate exit 0; 0 events appended; daemon not spawned | Silently exits 0 without writing error log | `PROJECT.md §Global Mute`, Dispatch objective 1 |
| 5 | Narrator Hook | Nested Claude Guard | Suppresses recursive hook execution from `cli_rewrite` spawning `claude -p` | `AUTO_SPEECH_SUPPRESS_HOOKS=1` in environment | Immediate exit 0 | Clean exit 0 | `narrator_hook.sh:25-27` |
| 6 | Narrator Hook | Non-Dict / Malformed Ingestion | Tolerates non-object JSON (arrays, strings, numbers) or non-JSON strings on stdin | Array `["PreToolUse"]`, string `"test"`, number `123`, or `invalid` | Exits 0 cleanly; if session ID cannot be extracted, bails with 0 events | Never crashes or exits non-zero; logs to stderr/err log if needed | Dispatch objective 1, `narrator_hook.sh:84-111` |
| 7 | Narrator Hook | Daemon Auto-Spawn Guard | Starts narrator daemon if alive check fails and marker present | `narrator_pid_is_ours` returns false; daemon dead | Detached spawn of `narrator_service_start.sh` | Continues execution; stderr to `/tmp/auto-speech-narrator-hook.err` | `narrator_hook.sh:51-75` |
| 8 | Autoplay Hook | Session ID Ingestion (`session_id`) | Ingests Claude Code session ID from Stop hook payload | JSON object with `"session_id": "s1"` | Looks up `$HOME/.claude/auto-speech-autoplay-enabled/s1` | If absent, exits 0 (opt-in default) | `autoplay_hook.sh:39` |
| 9 | Autoplay Hook | Antigravity Ingestion (`conversationId`) | Ingests Antigravity conversation ID from Stop hook payload | JSON object with `"conversationId": "conv-1"` | Looks up `$HOME/.claude/auto-speech-autoplay-enabled/conv-1` | If absent, exits 0 | `autoplay_hook.sh:39` |
| 10 | Autoplay Hook | Antigravity Transcript (`transcriptPath`) | Ingests camelCase `transcriptPath` from Antigravity payload | JSON object with `"transcriptPath": "/path/to/t.jsonl"` | Passes path to detached worker | Falls back to empty string / locator heuristic | `autoplay_hook.sh:38` |
| 11 | Autoplay Hook | Global Mute Gate | Bails immediately on global mute | File `$HOME/.claude/auto-speech.disabled` exists | Immediate exit 0; beacon not updated; worker not spawned | Exits 0 fast | `autoplay_hook.sh:53-55` |
| 12 | Autoplay Hook | Non-Dict / Malformed Ingestion | Tolerates non-dict JSON or raw text on stdin | Array `["Stop"]`, string `"Stop"`, or non-JSON | Bails cleanly with exit 0 because session cannot be enrolled | Exits 0, no detached worker spawned | Dispatch objective 1 |
| 13 | Autoplay Hook | Solo Scope Enforcement | Restricts playback to spotlighted session if marker exists | `$HOME/.claude/auto-speech-autoplay-solo` exists | If session != solo session, exits 0 silently | Non-spotlighted sessions stay silent | `autoplay_hook.sh:63-69` |
| 14 | Autoplay Hook | Beacon Mtime Precision | Updates per-session staleness beacon with sub-second precision | Beacon file `/tmp/auto-speech-last-stop.<session_id>` | Captured with `%Fm` (BSD) or `%.9Y` (GNU) | Stored in worker env | `autoplay_hook.sh:127-129` |
| 15 | Autoplay Shim | 3-Arg Worker Execution | Bash shim execs `autoplay_worker.py` preserving 3 positional args | `$1`: beacon_mtime, `$2`: transcript_path, `$3`: session_id | `exec python3 ... autoplay_worker.py "$@"` | Propagates exit code | `tests/test_autoplay_shim.sh:23-32` |
| 16 | Plugin Installer | Curated Command Linking | Installs exactly 8 curated slash commands into `~/.claude/commands` | `bash setup/install-plugin.sh` | 8 symlinks created in `~/.claude/commands/auto-speech-*.md` | Refuses to overwrite non-symlink | `tests/test_install_plugin.sh:30-34` |
| 17 | Plugin Installer | Root File Recording | Records absolute clone path in `~/.config/auto-speech/root` | `bash setup/install-plugin.sh` | File written with verbatim clone root | Fails if path invalid | `tests/test_install_plugin.sh:35-38` |
| 18 | Plugin Installer | CLI Symlink Creation | Symlinks `~/.local/bin/auto-speech` to `bin/auto-speech` | `bash setup/install-plugin.sh` | Symlink created at `~/.local/bin/auto-speech` | Refuses to overwrite non-symlink | `tests/test_install_plugin.sh:40-45` |
| 19 | Plugin Installer | Extras Opt-In & Preservation | Installs all 21 commands when `--with-extras` specified; preserves on re-run | `--with-extras` flag | 21 symlinks created; preserved on plain re-run | Fails if link fails | `tests/test_install_plugin.sh:68-76` |
| 20 | Plugin Installer | Two-Tier Link Pruning | Prunes obsolete commands in curated keep-set and dangling extras | Re-run installer | Removes non-keep-set links in curated and dangling extras links | Exits 0 | `tests/test_install_plugin.sh:78-89` |
| 21 | Plugin Installer | Uninstallation Cleanup | Removes root file and `~/.local/bin/auto-speech` symlink | `bash setup/uninstall.sh` | Root file and CLI symlink removed | Exits 0 | `tests/test_install_plugin.sh:91-108` |
| 22 | Slash Commands | Portability Invariant | Ensures no hardcoded user home paths (`/Users/joshua`) exist in commands or bin | Grep over `plugin/commands`, `plugin/commands-extra`, `bin` | 0 occurrences found | Test failure if path leaked | `tests/test_install_plugin.sh:110-117` |
| 23 | Slash Commands | Command Bash Syntax Integrity | Ensures all 21 markdown files contain valid bash scripts | `bash -n` on extracted fenced code blocks | All 21 commands parse with 0 syntax errors | Syntax error flagged | Analysis verification |
| 24 | CLI Dispatcher | Subcommand Trampoline | Resolves real clone root across symlinks and calls `cli.py` via venv python | Invocation via symlink `~/.local/bin/auto-speech <subcommand>` | Resolves symlink target up to 20 hops, executes `cli.py` | Exits 1 if python or cli.py missing | `bin/auto-speech:8-40` |
| 25 | Test Suite | Unified Regression Runner | Executes full test harness without pytest dependency | `bash tests/run_all.sh` | Runs 50 test suites (36 cheap, 8 shell, 1 web, 5 heavy) | Reports failed suite names and exits 1 | `tests/run_all.sh:1-190` |

---

## 4. Edge Cases

| # | Feature | Input | Observed Behavior |
|---|---------|-------|-------------------|
| 1 | Narrator Hook | Stdin payload is JSON array: `["PreToolUse", "Bash"]` | In current `narrator_hook.sh`, jq attempts `.session_id // ""` which errors with `Cannot index array with string "session_id"`. With redirect `2>/dev/null`, returns empty string. Hook bails safely with delta=0. If env var `CLAUDE_CODE_SESSION_ID` is set and marker exists, event builder fails with `Cannot index array with string "hook_event_name"`, logs to err log, and exits 0 with delta=0. |
| 2 | Narrator Hook | Stdin payload is plain string: `"Stop"` | jq fails to index string; returns empty string. Hook bails with delta=0, exit 0. |
| 3 | Narrator Hook | Stdin payload is completely empty string `""` | `cat` produces empty string; jq fails to parse; returns empty string. Hook bails with delta=0, exit 0. |
| 4 | Narrator Hook | Stdin payload is malformed JSON: `{"session_id": "test"` | jq parse error redirected to `/dev/null`; session ID is empty. Falls back to env var if present; otherwise exits 0 with delta=0. |
| 5 | Narrator Hook | Dual payload: `{"session_id": "s1", "conversationId": "c1"}` where only `c1` marker exists | If `.session_id` takes precedence, hook resolves `s1`. Since `s1` marker is absent, hook MUST bail with delta=0 (does NOT fall back to `c1`). |
| 6 | Narrator Hook | Dual payload: `{"session_id": "s1", "conversationId": "c1"}` where only `s1` marker exists | Hook resolves `s1`. Since `s1` marker exists, hook fires with delta=1. |
| 7 | Narrator Hook | Antigravity payload `{"conversationId": "conv-1"}` when marker exists | Hook resolves `conv-1` as session ID, verifies marker, and fires with delta=1. (Currently fails in worktree because `narrator_hook.sh` only checks `.session_id`). |
| 8 | Narrator Hook | Enrolled session when global mute `$HOME/.claude/auto-speech.disabled` exists | Hook bails immediately with delta=0, exit 0. (Currently fails in worktree because `narrator_hook.sh` does not check `$DISABLED_MARKER`). |
| 9 | Autoplay Hook | Antigravity payload `{"conversationId": "ag-1", "transcriptPath": "/path/t.jsonl"}` with enrolled marker | Hook extracts `ag-1`, verifies `$HOME/.claude/auto-speech-autoplay-enabled/ag-1` exists, updates `/tmp/auto-speech-last-stop.ag-1`, and spawns detached worker. |
| 10 | Autoplay Hook | Antigravity payload `{"conversationId": "ag-1"}` when unenrolled | Hook extracts `ag-1`, finds marker absent, and bails silently with exit 0 (no beacon created). |
| 11 | Autoplay Hook | Stdin payload is JSON array: `["Stop"]` | Hook extracts empty session ID, finds marker absent, and bails silently with exit 0. |
| 12 | Autoplay Hook | Stdin payload is malformed text: `non-json-stop` | jq returns empty string; session ID is empty; hook bails silently with exit 0. |
| 13 | Autoplay Hook | Enrolled Antigravity session when `$HOME/.claude/auto-speech.disabled` exists | Hook bails at disabled marker check before evaluating session or writing beacon; exits 0. |
| 14 | Plugin Installer | Running `install-plugin.sh` when `~/.local/bin/auto-speech` exists as a regular file | Installer detects non-symlink collision and aborts with explicit error message to preserve user file integrity. |
| 15 | Plugin Installer | Running command block when `~/.config/auto-speech/root` is missing | Command block detects missing root file, emits clear diagnostic on stderr, and exits 1. |
| 16 | Slash Commands | Executing `/auto-speech-speak` with non-integer argument: `/auto-speech-speak abc` | Command parser halts immediately with error message `speak: argument must be a positive integer, got abc`, exiting without calling backend. |
| 17 | Slash Commands | Executing `/auto-speech-replay` with non-integer argument: `/auto-speech-replay -5` | Command parser halts immediately with error message `replay: argument must be a positive integer, got -5`. |
| 18 | Slash Commands | Executing `/auto-speech-autoplay-on` when `CLAUDE_CODE_SESSION_ID` is unset | Command outputs `no-session-id`, clears global mute, and informs user that autoplay requires session enrollment. |
| 19 | Test Runner | Running `tests/run_all.sh --hermetic` on non-macOS or bare Python environment | Skips 5 dependency-requiring tests (`NEEDS_DEPS`) and runs all 31 hermetic unit suites cleanly. |

---

## 5. Detailed Test Specifications for Shell Hooks

### 5.1 `tests/test_narrator_hook.sh` Specification

#### Current State
- Currently tests:
  1. Anchor: Daemon PID guard (`narrator_pid_is_ours` used, bare `kill -0` absent).
  2. `suppress=1 bails immediately (delta=0)`
  3. `no session anywhere bails (delta=0)`
  4. `session id present but no marker → bail (delta=0)`
  5. `payload session matches marker → fire (delta=1)`
  6. `env session matches marker (no payload) → fire (delta=1)`
- Missing coverage required by Dispatch:
  - Antigravity payload `{"conversationId": "conv-1"}`
  - Dual payload `{"session_id": "s1", "conversationId": "c1"}` (precedence verification)
  - Global mute (`$HOME/.claude/auto-speech.disabled`)
  - Non-dict / malformed payloads (`[...]`, `"string"`, `123`, `malformed`, `""`)

#### Required Test Cases and Assertions

```bash
# -----------------------------------------------------------------------------
# 1. Anchor Verifications
# -----------------------------------------------------------------------------
# Verify narrator_hook.sh contains global mute check
grep -q 'auto-speech.disabled' "$HOOK" || fail "anchor:narrator-hook-global-mute" "hook missing auto-speech.disabled check"

# Verify narrator_hook.sh contains conversationId support
grep -q 'conversationId' "$HOOK" || fail "anchor:narrator-hook-conversationId" "hook missing conversationId support"

# -----------------------------------------------------------------------------
# 2. Antigravity Payload Tests ({"conversationId": "conv-1"})
# -----------------------------------------------------------------------------
# Case 2a: Antigravity payload with marker present -> fires (delta = 1)
# Setup:
#   Payload: {"hook_event_name":"PreToolUse","tool_name":"Bash","conversationId":"conv-1"}
#   Marker:  $tmp_home/.claude/auto-speech-narrate-sessions/conv-1 created
#   Env:     CLAUDE_CODE_SESSION_ID unset, AUTO_SPEECH_SUPPRESS_HOOKS unset
# Assert:
#   delta = 1, hook exits 0

# Case 2b: Antigravity payload with marker absent -> bails (delta = 0)
# Setup:
#   Payload: {"hook_event_name":"PreToolUse","tool_name":"Bash","conversationId":"conv-unmarked"}
#   Marker:  absent
# Assert:
#   delta = 0, hook exits 0

# -----------------------------------------------------------------------------
# 3. Dual Payload Precedence Tests ({"session_id": "s1", "conversationId": "c1"})
# -----------------------------------------------------------------------------
# Case 3a: session_id takes precedence when session_id marker is present
# Setup:
#   Payload: {"hook_event_name":"PreToolUse","tool_name":"Bash","session_id":"s1","conversationId":"c1"}
#   Marker:  $tmp_home/.claude/auto-speech-narrate-sessions/s1 created (c1 NOT created)
# Assert:
#   delta = 1 (session_id was selected, marker matched, hook fired)

# Case 3b: conversationId ignored when session_id marker is absent
# Setup:
#   Payload: {"hook_event_name":"PreToolUse","tool_name":"Bash","session_id":"s1","conversationId":"c1"}
#   Marker:  $tmp_home/.claude/auto-speech-narrate-sessions/c1 created (s1 NOT created)
# Assert:
#   delta = 0 (session_id was selected, s1 marker absent -> hook bailed, did NOT fall back to c1)

# -----------------------------------------------------------------------------
# 4. Global Mute Tests ($HOME/.claude/auto-speech.disabled)
# -----------------------------------------------------------------------------
# Case 4a: Claude Code session enrolled but globally muted -> bails (delta = 0)
# Setup:
#   Payload: {"hook_event_name":"PreToolUse","tool_name":"Bash","session_id":"sid-mute"}
#   Marker:  $tmp_home/.claude/auto-speech-narrate-sessions/sid-mute created
#   Mute:    touch $tmp_home/.claude/auto-speech.disabled
# Assert:
#   delta = 0, hook exits 0

# Case 4b: Antigravity session enrolled but globally muted -> bails (delta = 0)
# Setup:
#   Payload: {"hook_event_name":"PreToolUse","tool_name":"Bash","conversationId":"conv-mute"}
#   Marker:  $tmp_home/.claude/auto-speech-narrate-sessions/conv-mute created
#   Mute:    touch $tmp_home/.claude/auto-speech.disabled
# Assert:
#   delta = 0, hook exits 0

# -----------------------------------------------------------------------------
# 5. Non-Dict and Malformed Payload Tests
# -----------------------------------------------------------------------------
# Case 5a: JSON array payload -> bails cleanly (delta = 0)
# Setup:
#   Payload: ["PreToolUse", "Bash", {"session_id":"arr-sess"}]
# Assert:
#   delta = 0, exit 0, no unhandled pipe errors

# Case 5b: JSON string payload -> bails cleanly (delta = 0)
# Setup:
#   Payload: "simple string payload"
# Assert:
#   delta = 0, exit 0

# Case 5c: JSON number payload -> bails cleanly (delta = 0)
# Setup:
#   Payload: 123456
# Assert:
#   delta = 0, exit 0

# Case 5d: Malformed non-JSON payload -> bails cleanly (delta = 0)
# Setup:
#   Payload: this is not valid json
# Assert:
#   delta = 0, exit 0

# Case 5e: Empty payload -> bails cleanly (delta = 0)
# Setup:
#   Payload: ""
# Assert:
#   delta = 0, exit 0
```

#### Production Script Remediation Contract (`plugin/scripts/shell/narrator_hook.sh`)
To allow the above tests to pass, `plugin/scripts/shell/narrator_hook.sh` must be updated with:
1. Global mute check placed immediately after reading `$PAYLOAD` and suppress guard:
   ```bash
   DISABLED_MARKER="$HOME/.claude/auto-speech.disabled"
   if [[ -e "$DISABLED_MARKER" ]]; then
       exit 0
   fi
   ```
2. Session ID resolution using object-defensive jq query:
   ```bash
   SESSION_ID=""
   if command -v jq >/dev/null 2>&1; then
       SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r 'if type == "object" then (.session_id // .conversationId) // "" else "" end' 2>/dev/null || true)"
   fi
   if [[ -z "$SESSION_ID" ]]; then
       SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}"
   fi
   if [[ -z "$SESSION_ID" ]]; then
       exit 0
   fi
   ```
3. Object-type check when building the event JSONL:
   ```bash
   if printf '%s' "$PAYLOAD" | jq -e 'type == "object"' >/dev/null 2>&1; then
       EVENT="$(printf '%s' "$PAYLOAD" | jq -c \
           --arg cwd "$PWD" \
           --arg ts "$TS" \
           --arg pid "$$" \
           '{
               ts: ($ts | tonumber),
               cwd: $cwd,
               pid: ($pid | tonumber),
               event: (.hook_event_name // "unknown"),
               payload: .
           }' 2>>"$ERR_LOG")" || EVENT=""
   else
       EVENT="$(jq -cn \
           --arg raw "$PAYLOAD" \
           --arg cwd "$PWD" \
           --arg ts "$TS" \
           --arg pid "$$" \
           '{
               ts: ($ts | tonumber),
               cwd: $cwd,
               pid: ($pid | tonumber),
               event: "unknown",
               payload: { raw: $raw }
           }' 2>>"$ERR_LOG")" || EVENT=""
   fi
   ```

---

### 5.2 `tests/test_autoplay_shim.sh` Specification

#### Current State
- Currently tests:
  1. Shim thinness and 3-arg contract (`beacon_mtime`, `transcript_path`, `session_id`).
  2. Hook opt-in semantics (opt-in directory referenced, opt-out absent).
  3. Hook daemon PID guard (`narrator_pid_is_ours`).
  4. Shared PID guard functional checks (foreign live PID, dead PID, empty PID).
  5. Functional: Hook exits silently for unenrolled session (`test-session-1`).
  6. Functional: Hook proceeds for enrolled session (`test-session-3`).
  7. Functional: Global mute overrides enrolled session (`test-session-2`).
- Missing coverage required by Dispatch:
  - Antigravity payload `{"conversationId": "conv-1"}`
  - Global mute verification with Antigravity payload
  - Non-dict / malformed payloads (`[...]`, `"string"`, `123`, `malformed`, `""`)

#### Required Test Cases and Assertions

```bash
# -----------------------------------------------------------------------------
# 1. Antigravity Payload Tests ({"conversationId": "ag-conv-1"})
# -----------------------------------------------------------------------------
# Case 1a: Enrolled Antigravity session -> hook proceeds and touches beacon
# Setup:
#   Payload: {"conversationId":"ag-session-1","transcriptPath":"/nonexistent.jsonl"}
#   Enroll:  mkdir -p "$FAKE_HOME/.claude/auto-speech-autoplay-enabled"
#            touch "$FAKE_HOME/.claude/auto-speech-autoplay-enabled/ag-session-1"
#   Exec:    HOME="$FAKE_HOME" AUTO_SPEECH_AUTOPLAY_MIN_LEN=99999999 bash "$HOOK" <<<"$PAYLOAD"
# Assert:
#   Exit code == 0
#   Beacon exists: /tmp/auto-speech-last-stop.ag-session-1
#   Teardown: rm -f /tmp/auto-speech-last-stop.ag-session-1

# Case 1b: Unenrolled Antigravity session -> hook bails silently
# Setup:
#   Payload: {"conversationId":"ag-session-unenrolled","transcriptPath":"/nonexistent.jsonl"}
#   Enroll:  (no marker created)
#   Exec:    HOME="$FAKE_HOME" bash "$HOOK" <<<"$PAYLOAD"
# Assert:
#   Exit code == 0
#   Beacon does NOT exist: /tmp/auto-speech-last-stop.ag-session-unenrolled absent

# -----------------------------------------------------------------------------
# 2. Global Mute Tests with Antigravity Payload
# -----------------------------------------------------------------------------
# Case 2: Enrolled Antigravity session under global mute -> hook bails silently
# Setup:
#   Payload: {"conversationId":"ag-session-muted","transcriptPath":"/nonexistent.jsonl"}
#   Enroll:  touch "$FAKE_HOME/.claude/auto-speech-autoplay-enabled/ag-session-muted"
#   Mute:    touch "$FAKE_HOME/.claude/auto-speech.disabled"
#   Exec:    HOME="$FAKE_HOME" bash "$HOOK" <<<"$PAYLOAD"
# Assert:
#   Exit code == 0
#   Beacon does NOT exist: /tmp/auto-speech-last-stop.ag-session-muted absent

# -----------------------------------------------------------------------------
# 3. Non-Dict and Malformed Payload Tests
# -----------------------------------------------------------------------------
# Case 3a: Array payload: ["Stop", "/nonexistent.jsonl"]
# Exec:   HOME="$FAKE_HOME" bash "$HOOK" <<< '["Stop", "/nonexistent.jsonl"]'
# Assert: Exit code == 0, no beacon created

# Case 3b: Plain string payload: "Stop hook payload"
# Exec:   HOME="$FAKE_HOME" bash "$HOOK" <<< '"Stop hook payload"'
# Assert: Exit code == 0, no beacon created

# Case 3c: Number payload: 98765
# Exec:   HOME="$FAKE_HOME" bash "$HOOK" <<< '98765'
# Assert: Exit code == 0, no beacon created

# Case 3d: Non-JSON raw text: invalid json stop event
# Exec:   HOME="$FAKE_HOME" bash "$HOOK" <<< 'invalid json stop event'
# Assert: Exit code == 0, no beacon created

# Case 3e: Empty payload: ""
# Exec:   HOME="$FAKE_HOME" bash "$HOOK" <<< ''
# Assert: Exit code == 0, no beacon created
```

#### Production Script Remediation Contract (`plugin/scripts/shell/autoplay_hook.sh`)
In `plugin/scripts/shell/autoplay_hook.sh`, lines 37-40 currently read:
```bash
if command -v jq >/dev/null 2>&1; then
    TRANSCRIPT_PATH="$(printf '%s' "$PAYLOAD" | jq -r '(.transcript_path // .transcriptPath) // ""' 2>/dev/null || true)"
    SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r '(.session_id // .conversationId) // ""' 2>/dev/null || true)"
fi
```
To harden against jq error emissions on non-object inputs:
```bash
if command -v jq >/dev/null 2>&1; then
    TRANSCRIPT_PATH="$(printf '%s' "$PAYLOAD" | jq -r 'if type == "object" then (.transcript_path // .transcriptPath) // "" else "" end' 2>/dev/null || true)"
    SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r 'if type == "object" then (.session_id // .conversationId) // "" else "" end' 2>/dev/null || true)"
fi
```
This ensures zero stderr noise and consistent empty-string returns on array, string, or number payloads.

---

## 6. Slash Command and Installer Specifications

### 6.1 `tests/test_install_plugin.sh` Specification & Verification
`tests/test_install_plugin.sh` was empirically verified on the worktree tree. All 12/12 assertions passed cleanly:

1. `curated install links exactly 8 commands`:
   - Enforces keep-set in `plugin/commands/`: `auto-speech-app.md`, `auto-speech-autoplay-mode.md`, `auto-speech-autoplay-off.md`, `auto-speech-autoplay-on.md`, `auto-speech-doctor.md`, `auto-speech-replay.md`, `auto-speech-scope.md`, `auto-speech-speak.md`.
2. `root file recorded with the clone path`:
   - Enforces write of `$PROJECT_ROOT` to `$HOME/.config/auto-speech/root`.
3. `curated install links ~/.local/bin/auto-speech`:
   - Enforces symlink `$HOME/.local/bin/auto-speech -> $PROJECT_ROOT/bin/auto-speech`.
4. `doctor block resolves root from ~/.config/auto-speech/root`:
   - Extracts bash block from `plugin/commands/auto-speech-doctor.md` and executes against sandbox root.
5. `guard fires when root file is absent`:
   - Validates error message `auto-speech: project root not configured` and non-zero exit when root file is removed.
6. `--with-extras links 21 commands`:
   - Verifies 8 curated + 13 extras = 21 commands installed.
7. `plain re-run preserves opted-in extras`:
   - Validates idempotency: plain re-run retains 21 commands if extras were previously opted in.
8. `prune removes non-keep-set links into plugin/commands/`:
   - Verifies that foreign symlinks in `~/.claude/commands` pointing into `plugin/commands/` are unlinked.
9. `prune removes dangling links into plugin/commands-extra/`:
   - Verifies that dangling symlinks pointing into `plugin/commands-extra/` are unlinked.
10. `uninstall removes the recorded root file`:
    - Verifies that `setup/uninstall.sh` removes `$HOME/.config/auto-speech/root`.
11. `uninstall removes ~/.local/bin/auto-speech symlink`:
    - Verifies that `setup/uninstall.sh` removes `$HOME/.local/bin/auto-speech`.
12. `no hardcoded absolute project paths in command files`:
    - Verifies grep for `/Users/joshua` across `plugin/commands`, `plugin/commands-extra`, and `bin` returns 0 hits.

### 6.2 21 Slash Commands Inventory and Delegation Matrix

| # | Command File | Tier | Purpose | Execution Target | Exit Codes Supported |
|---|--------------|------|---------|------------------|----------------------|
| 1 | `auto-speech-doctor.md` | Curated | System diagnostics | `doctor.py` / `auto-speech doctor` | 0=Healthy, 1=Fail |
| 2 | `auto-speech-speak.md` | Curated | Synthesize prior response | `run_extract.sh` -> `cli_rewrite.py` -> `speak.py` | 0=Spoke, non-zero=Fail |
| 3 | `auto-speech-replay.md` | Curated | Replay audio cache entry | `run_replay.sh` / `auto-speech replay` | 0=Replayed, 2=No entry, non-zero=Fail |
| 4 | `auto-speech-autoplay-on.md` | Curated | Enroll session in autoplay | Shell marker creation | 0=OK |
| 5 | `auto-speech-autoplay-off.md` | Curated | Withdraw session enrollment | Shell marker deletion | 0=OK |
| 6 | `auto-speech-autoplay-mode.md` | Curated | Configure rewrite mode | `autoplay_config.py` & TOML write | 0=OK |
| 7 | `auto-speech-scope.md` | Curated | Set solo session scope | `autoplay_scope.py` & solo marker | 0=OK |
| 8 | `auto-speech-app.md` | Curated | Launch web dashboard | `start_webapp.sh` | 0=Started |
| 9 | `auto-speech-autoplay-status.md` | Extra | View autoplay status | `autoplay_status.sh` / `auto-speech autoplay status` | 0=OK |
| 10 | `auto-speech-end.md` | Extra | Seek to end of playback | `run_control.sh end` / `auto-speech seek end` | 0=OK, 2=No session |
| 11 | `auto-speech-pause.md` | Extra | Pause active playback | `run_control.sh pause` / `auto-speech pause` | 0=Paused, 2=No session |
| 12 | `auto-speech-resume.md` | Extra | Resume paused playback | `run_control.sh resume` / `auto-speech resume` | 0=Resumed, 2=No session |
| 13 | `auto-speech-restart.md` | Extra | Restart active playback | `run_control.sh restart` / `auto-speech restart` | 0=Restarted, 2=No session |
| 14 | `auto-speech-seek.md` | Extra | Seek playback position | `run_control.sh seek` / `auto-speech seek` | 0=Seeked, 2=No session, 4=Bad arg |
| 15 | `auto-speech-narrate-on.md` | Extra | Enroll session in narration | Starts daemon & creates marker | 0=OK |
| 16 | `auto-speech-narrate-off.md` | Extra | Withdraw session narration | Removes marker | 0=OK |
| 17 | `auto-speech-narrate-status.md` | Extra | View narrator status | `narrator_service_status.sh` / `auto-speech narrate status` | 0=OK |
| 18 | `auto-speech-narrate-stop.md` | Extra | Stop narrator daemon | `narrator_service_stop.sh` / `auto-speech narrate stop` | 0=OK |
| 19 | `auto-speech-narrate-config.md` | Extra | View/edit narrator config | `narrator_config.py` / `auto-speech narrate config` | 0=OK |
| 20 | `auto-speech-narrate-install.md` | Extra | Install MLX narrate deps | `uv sync --extra narrate` & downloads model | 0=Installed |
| 21 | `auto-speech-update.md` | Extra | Force update & sync deps | `bootstrap.sh --force` / `auto-speech update --force` | 0=Updated |

### 6.3 Syntax and Path Verification
- **Bash Syntax**: Tested with `bash -n` across all 21 markdown code blocks; verified 0 syntax errors.
- **Portability**: Grepped for `/Users/joshua` across `plugin/commands/*.md`, `plugin/commands-extra/*.md`, and `bin/*`; verified 0 hardcoded paths. All paths dynamically derive from `PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"`.

---

## 7. Full Test Suite Regression Verification

### 7.1 Test Runner Inventory (`tests/run_all.sh`)
The test runner organizes the test suite into 4 execution tiers:

1. **Cheap Unit Tests (`CHEAP`, 36 test suites)**:
   - `test_state_machine.py`: State transitions and guards.
   - `test_auto_speech_log.py`: Log formatting and size rotation.
   - `test_doctor.py`: Health checks and diagnostic status.
   - `test_config_validation.py`: TOML validation schemas.
   - `test_self_update.py`: Self-update reconciliation and lock verification.
   - `test_playback_state.py`: Playback state machine.
   - `test_narrator_state.py`: Narrator daemon state tracking.
   - `test_worker_lifecycle.py`: Worker process lifecycle and signals.
   - `test_staleness_beacon.py`: Sub-second beacon mtime comparison.
   - `test_playback_fifo.py`: Playback FIFO queue operations.
   - `test_dedup_guard.py`: Speech deduplication cache.
   - `test_autoplay_gate.py`: Global mute gate checks.
   - `test_autoplay_scope.py`: Solo scope and spotlight filtering.
   - `test_autoplay_enrollment.py`: Opt-in session enrollment markers.
   - `test_leftover_optout_docs.py`: Documentation audit against opt-out references.
   - `test_autoplay_worker.py`: Autoplay worker execution and queuing.
   - `test_say_worker.py`: Say worker compatibility and fallbacks.
   - `test_replay_control.py`: Replay ordinal selection and caching.
   - `test_mcp_server.py`: Full 6-tool MCP server suite (1258 lines).
   - `test_job_tracker.py`: Background job state tracking.
   - `test_narrator_phase_classifier.py`: Tool narration phase heuristic.
   - `test_narrator_summarizer.py`: Narration text summarization.
   - `test_narrator_config.py`: Narrator configuration loader.
   - `test_autoplay_config.py`: Autoplay configuration loader.
   - `test_message_selector.py`: JSONL transcript extraction and turn selection.
   - `test_transcript_locator.py`: Session transcript file discovery.
   - `test_fibonacci.py`: Exponential backoff calculation.
   - `test_cli_rewrite.py`: Claude CLI prompt generation and execution.
   - `test_mlx_summarizer.py`: Local MLX LLM summarization.
   - `test_narrator_service.py`: Central daemon, dual-wire socket, priority queue.
   - `test_queue_robustness.py`: Priority queue concurrency and queue facade.
   - `test_daemon_client.py`: In-process socket client with retry and pooling.
   - `test_apple_say_engine.py`: Native Apple TTS synthesis engine.
   - `test_native_audio_sink.py`: Synchronous mpv playback sink.
   - `test_speak_client.py`: Direct socket communication client.
   - `test_cli_dispatcher.py`: CLI dispatcher (1047 lines, all 12 subcommands).

2. **Shell Script Tests (`SHELL_TESTS`, 8 test suites)**:
   - `test_autoplay_shim.sh`: Shim contract and Stop hook gating.
   - `test_narrator_hook.sh`: Narrator hook session gating and PID guards.
   - `test_narrator_service_status.sh`: Daemon status reporting.
   - `test_compute_hash.sh`: Content hash calculation.
   - `test_install_idempotent.sh`: Hook installation idempotency.
   - `test_install_plugin.sh`: Command symlinks and root recording (12/12).
   - `test_bootstrap_hook_idempotent.sh`: Bootstrap hook idempotency.
   - `test_deps_locked.sh`: uv.lock and pyproject.toml sync verification.

3. **Web Server Tests (`WEB`, 1 test suite)**:
   - `test_synthesize_endpoint.py`: Flask synthesis API endpoint and caching.

4. **Heavy Audio Pipeline Tests (`HEAVY`, 5 test suites)**:
   - `test_chunk_planner.py`: Audio chunking and sentence boundary splitting.
   - `test_wav_concat.py`: Atomic WAV header concatenation.
   - `test_resilient_synthesizer.py`: Split-tree synthesis resilience.
   - `test_segment_producer.py`: Audio segment streaming producer.
   - `test_playback_consumer.py`: Audio segment playback consumer.

### 7.2 Benchmark Test Run Verification
- Executed `bash tests/run_all.sh` inside `/Users/joshua/Developer/auto-speech-worktree`.
- **Result**:
  - `ran: 50`
  - `failed: 0`
  - `all tests passed` (100% green pass).

---

## 8. Summary of Specific Tasks for Milestone M3 Execution Crew

1. **Update `plugin/scripts/shell/narrator_hook.sh`**:
   - Add `$DISABLED_MARKER` check (`~/.claude/auto-speech.disabled`).
   - Update session ID parsing with `jq` to support `(.session_id // .conversationId)`.
   - Update event formatting with `jq -e 'type == "object"'` to handle non-dict payloads gracefully.
2. **Update `tests/test_narrator_hook.sh`**:
   - Add anchor check for `auto-speech.disabled` and `conversationId`.
   - Add test case for Antigravity payload `{"conversationId": "conv-1"}` with marker present (delta=1) and absent (delta=0).
   - Add test case for dual payload `{"session_id": "s1", "conversationId": "c1"}` verifying `session_id` precedence.
   - Add test case for global mute with Claude Code payload and Antigravity payload (delta=0).
   - Add test cases for non-dict payloads (array, string, number, malformed, empty) verifying delta=0 and exit 0.
3. **Update `plugin/scripts/shell/autoplay_hook.sh`**:
   - Guard `jq` calls with `if type == "object"` to prevent stderr error logs on non-dict payloads.
4. **Update `tests/test_autoplay_shim.sh`**:
   - Add test case for Antigravity payload `{"conversationId": "ag-session-1"}` verifying beacon creation.
   - Add test case for Antigravity payload under global mute verifying beacon suppression.
   - Add test cases for non-dict payloads (array, string, number, malformed, empty) verifying exit 0 and zero beacon files created.
5. **Re-verify Entire Harness**:
   - Run `bash tests/test_narrator_hook.sh` (all tests passing).
   - Run `bash tests/test_autoplay_shim.sh` (all tests passing).
   - Run `bash tests/test_install_plugin.sh` (12/12 passing).
   - Run `bash tests/run_all.sh` (all 50+ test suites passing).
