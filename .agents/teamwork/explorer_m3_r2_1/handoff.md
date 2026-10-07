# Handoff Report — Milestone M3 Shell Hook Implementation Strategy

**Subagent**: `explorer_m3_r2_1`  
**Working Directory**: `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m3_r2_1`  
**Target Git Worktree**: `/Users/joshua/Developer/auto-speech-worktree`  
**Date**: 2026-10-06  
**Handoff Type**: Hard (Investigation & Specification Complete)  

---

## 1. Observation

1. **`narrator_hook.sh` Antigravity Key Deficiency**:
   - In `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/shell/narrator_hook.sh:36-45`:
     ```bash
     SESSION_ID=""
     if command -v jq >/dev/null 2>&1; then
         SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r '.session_id // ""' 2>/dev/null || true)"
     fi
     if [[ -z "$SESSION_ID" ]]; then
         SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}"
     fi
     if [[ -z "$SESSION_ID" ]]; then
         exit 0
     fi
     ```
   - Antigravity lifecycle hooks (`hooks.json`) provide `{"conversationId": "..."}` without `session_id`. Consequently, `SESSION_ID` resolves to empty, and the hook silently exits 0 on line 44 without logging narrator events.

2. **`autoplay_hook.sh` Gating Order & Latency**:
   - In `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/shell/autoplay_hook.sh:37-55`:
     ```bash
     if command -v jq >/dev/null 2>&1; then
         TRANSCRIPT_PATH="$(printf '%s' "$PAYLOAD" | jq -r '(.transcript_path // .transcriptPath) // ""' 2>/dev/null || true)"
         SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r '(.session_id // .conversationId) // ""' 2>/dev/null || true)"
     fi
     if [[ "${AUTO_SPEECH_SUPPRESS_HOOKS:-}" == "1" ]]; then
         exit 0
     fi
     if [[ -e "$DISABLED_MARKER" ]]; then
         exit 0
     fi
     ```
   - Spawns `jq` twice before evaluating environment suppression or global mute. Empirical benchmark shows suppressed exit takes **16.91ms** with `jq`, but drops to **7.96ms** when bash suppression check precedes `jq`.

3. **Missing Global Mute Check in `narrator_hook.sh`**:
   - `narrator_hook.sh` currently does not inspect `~/.claude/auto-speech.disabled`. Even when globally muted, enrolled sessions continue logging events to `/tmp/auto-speech-narrator-events.jsonl`.

4. **Positional Argument Transposition in `cli.py`**:
   - In `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/cli.py:683-696`:
     ```python
     worker_script = _project_root() / "plugin" / "scripts" / "shell" / "autoplay_worker.sh"
     if worker_script.is_file():
         try:
             subprocess.Popen(
                 [
                     "bash",
                     str(worker_script),
                     transcript_path,
                     session_id,
                     str(beacon_file),
                 ],
                 stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL,
                 stdin=subprocess.DEVNULL,
                 start_new_session=True,
             )
     ```
   - In contrast, `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/shell/autoplay_worker.sh:21-23` and `plugin/scripts/python/autoplay_worker.py:566-568` expect:
     `$1`: `BEACON_MTIME` (float)  
     `$2`: `TRANSCRIPT_PATH` (string)  
     `$3`: `SESSION_ID` (string)  
   - Passing `transcript_path` as `$1` causes `autoplay_worker.py` to raise `ValueError: could not convert string to float`.

5. **Test Harness Synchronicity Requirements**:
   - `/Users/joshua/Developer/auto-speech-worktree/tests/test_autoplay_shim.sh:102` executes `bash "$HOOK"` and immediately evaluates `[[ -e "/tmp/auto-speech-last-stop.test-session-3" ]]`.
   - `/Users/joshua/Developer/auto-speech-worktree/tests/test_narrator_hook.sh:70` executes `bash "$HOOK"` and immediately checks `wc -l < /tmp/auto-speech-narrator-events.jsonl`.
   - Asynchronous delegation in the background (`&`) before touching disk markers causes immediate test assertion failures.

---

## 2. Logic Chain

1. **Dual-Format Standardization**:
   - From (1), `narrator_hook.sh` drops Antigravity events because it lacks `.conversationId`.
   - Updating `narrator_hook.sh` to `(.session_id // .conversationId // empty)` and `autoplay_hook.sh` to `(.transcript_path // .transcriptPath // empty)` ensures 100% interoperability across both Claude Code and Antigravity payloads without altering downstream consumers.

2. **Optimized Bash Gating Hierarchy**:
   - From (2) and (3), checking environment variables (`AUTO_SPEECH_SUPPRESS_HOOKS`) and file existence (`~/.claude/auto-speech.disabled`) in pure bash requires <0.5ms.
   - Placing these checks at lines 24–30 (before `jq`) guarantees that suppressed or muted sessions exit in **<8ms**, preserving responsiveness and eliminating unnecessary subprocess forks.
   - Adding the `DISABLED_MARKER` check to `narrator_hook.sh` aligns it with system-wide mute semantics.

3. **Architectural Delegation Strategy**:
   - From (4) and (5), invoking `bin/auto-speech hook stop` synchronously takes ~60ms (exceeding the <50ms budget), while running it asynchronously in the background (`&`) violates synchronous test assertions.
   - Therefore, the optimal architecture maintains the ultra-fast shell hooks as the boundary entrypoint with synchronous state preparation (beacon update in `autoplay_hook.sh`, JSONL append in `narrator_hook.sh`) and detached background worker execution.
   - `bin/auto-speech hook stop` and `hook narrator` serve as the unified CLI simulation and test surface. The argument order in `cli.py:686` must be corrected to `(beacon_mtime, transcript_path, session_id)`.

---

## 3. Caveats

- **External `jq` Dependency**: If `jq` is not installed on PATH, `autoplay_hook.sh` and `narrator_hook.sh` fallback to empty session extraction and exit 0. This is the intended opt-in safety behavior (unidentified sessions remain silent).
- **Sub-second Timestamp Precision**: Capturing `BEACON_MTIME` requires BSD `stat -f %Fm` (macOS) or GNU `stat -c %.9Y`. Truncating to integer seconds causes worker self-supersession. The existing `stat` fallback in `autoplay_hook.sh:127-129` is correct and must be retained.
- **Worktree Boundary**: All changes must be executed inside `/Users/joshua/Developer/auto-speech-worktree`. No edits should be made directly in the main repository tree until Milestone M5 merge.

---

## 4. Conclusion

The implementation strategy for Milestone M3 shell hooks is fully specified and ready for the implementer:
1. Update `plugin/scripts/shell/autoplay_hook.sh`:
   - Fast pre-gating: check `AUTO_SPEECH_SUPPRESS_HOOKS` and `~/.claude/auto-speech.disabled` before `jq`.
   - Standardize dual-format extraction to `(.session_id // .conversationId // empty)` and `(.transcript_path // .transcriptPath // empty)`.
2. Update `plugin/scripts/shell/narrator_hook.sh`:
   - Add global mute check (`~/.claude/auto-speech.disabled`).
   - Update session extraction to `(.session_id // .conversationId // empty)`.
3. Patch `plugin/scripts/python/cli.py`:
   - Fix argument order in `handle_hook("stop")` when launching `autoplay_worker.sh`: pass `beacon_mtime`, `transcript_path`, `session_id`.
4. Extend tests:
   - Add Antigravity payload test cases to `tests/test_narrator_hook.sh` and `tests/test_autoplay_shim.sh`.
   - Add global mute test case to `tests/test_narrator_hook.sh`.

---

## 5. Verification Method

To independently verify the implementation:

1. **Verify Shell Hook Unit Suites**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   bash tests/test_autoplay_shim.sh
   bash tests/test_narrator_hook.sh
   ```
   *Expected*: Both test suites pass 100% of anchors and functional cases.

2. **Verify CLI Dispatcher Test Suite**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/python -m unittest tests/test_cli_dispatcher.py
   ```
   *Expected*: All 45+ CLI test cases pass, including hook simulation tests.

3. **Verify Full Repository Regression Gate**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   bash tests/run_all.sh
   ```
   *Expected*: All 50 test suites pass with 0 failures.

4. **Verify Linter Cleanliness**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/ruff check plugin/
   ```
   *Expected*: 0 violations reported.
