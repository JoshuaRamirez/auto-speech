# Adversarial Challenge Handoff Report — Milestone M3 Shell Hooks

**Author**: `teamwork_preview_challenger` (`challenger_m3_1`)  
**Target Repository**: `/Users/joshua/Developer/auto-speech-worktree`  
**Target Files**:
- `plugin/scripts/shell/autoplay_hook.sh`
- `plugin/scripts/shell/narrator_hook.sh`  
**Overall Risk Assessment**: LOW  
**Verdict**: **APPROVE**  

---

## 1. Observation

### 1.1 Baseline Test Suite Execution
- **Command**: `bash tests/run_all.sh` executed inside `/Users/joshua/Developer/auto-speech-worktree`
- **Output**:
  ```text
  ====================
  ran:    50
  failed: 0
  all tests passed
  ```
- **Code Linter**: `.venv/bin/ruff check plugin/` returned 0 errors (`All checks passed!`).

### 1.2 Hook Implementation Analysis
- **`autoplay_hook.sh`**:
  - Line 41-43: Nested invocation guard:
    ```bash
    if [[ "${AUTO_SPEECH_SUPPRESS_HOOKS:-}" == "1" ]]; then
        exit 0
    fi
    ```
  - Line 47-49: Global mute guard:
    ```bash
    if [[ -e "$DISABLED_MARKER" ]]; then
        exit 0
    fi
    ```
  - Line 55: Session ID resolution supporting both Claude Code and Antigravity:
    ```bash
    SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r '(.session_id // .conversationId // empty)' 2>/dev/null || true)"
    ```
  - Line 65-70: Solo scoping with fallback for unidentifiable sessions:
    ```bash
    if [[ -n "$SESSION_ID" ]] && [[ -e "$SOLO_MARKER" ]]; then
        SOLO_ID="$(tr -d '[:space:]' < "$SOLO_MARKER" 2>/dev/null || true)"
        if [[ -n "$SOLO_ID" ]] && [[ "$SOLO_ID" != "$SESSION_ID" ]]; then
            exit 0
        fi
    fi
    ```
  - Line 83-85: Opt-in gating check:
    ```bash
    if [[ -z "$SESSION_ID" ]] || [[ ! -e "$SESSION_ENROLL_DIR/$SESSION_ID" ]]; then
        exit 0
    fi
    ```
  - Line 110-114: Per-session staleness beacon:
    ```bash
    if [[ -n "$SESSION_ID" ]]; then
        BEACON="$BEACON_DEFAULT.$SESSION_ID"
    else
        BEACON="$BEACON_DEFAULT"
    fi
    ```

- **`narrator_hook.sh`**:
  - Line 25-27: Nested invocation guard (`AUTO_SPEECH_SUPPRESS_HOOKS=1` -> `exit 0`).
  - Line 31-34: Global mute guard (`$DISABLED_MARKER` -> `exit 0`).
  - Line 44-46: Dual session key resolution:
    ```bash
    SESSION_ID="$(printf '%s' "$PAYLOAD" | jq -r '(.session_id // .conversationId // empty)' 2>/dev/null || true)"
    ```
  - Line 53-56: Session opt-in verification:
    ```bash
    MARKER="$HOME/.claude/auto-speech-narrate-sessions/$SESSION_ID"
    if [[ ! -e "$MARKER" ]]; then
        exit 0
    fi
    ```
  - Line 93-118: Safe JSON object type checking with fallback for non-dict payloads redirecting errors to `$ERR_LOG`.

### 1.3 Empirical Test Execution Results (`tests/test_challenger_m3.py`)
- **Command**: `.venv/bin/python tests/test_challenger_m3.py`
- **Output**:
  ```text
  === Test 1: Antigravity Payload (conversationId) ===
  1A autoplay enrolled: rc=0, err=''
    -> autoplay_hook properly extracted conversationId and touched beacon.
  1B autoplay unenrolled: rc=0, err=''
    -> autoplay_hook properly suppressed unenrolled conversationId.
  1C narrator enrolled: rc=0, delta=1, err=''
    -> narrator_hook logged event with conversationId correctly.
  1D narrator unenrolled: rc=0, delta=0, err=''
    -> narrator_hook suppressed unenrolled conversationId.
  Test 1 PASSED!

  === Test 2: Dual Payload Precedence (session_id > conversationId) ===
    -> Case 2A (only sid enrolled): Fired for sid as expected.
    -> Case 2B (only cid enrolled): Bailed cleanly because session_id took precedence and was unenrolled.
    -> Case 2C (both enrolled): Produced beacon for sid, proving precedence.
  Test 2 PASSED!

  === Test 3: Global Mute Enforcement (~/.claude/auto-speech.disabled) ===
  Test 3 PASSED! (Both hooks exit 0 with delta=0 and no beacon created under global mute)

  === Test 4: Latency Benchmark (<50ms Target) ===
  Latency benchmarks over 50 iterations each:
    autoplay_hook (suppressed)    : min=13.69ms | avg=16.46ms | med=15.25ms | p95=20.16ms | max=39.88ms
    narrator_hook (suppressed)    : min=11.17ms | avg=13.10ms | med=12.79ms | p95=15.23ms | max=18.69ms
    autoplay_hook (muted)         : min=13.99ms | avg=15.64ms | med=15.45ms | p95=17.83ms | max=18.62ms
    narrator_hook (muted)         : min=11.50ms | avg=13.43ms | med=13.22ms | p95=16.29ms | max=16.52ms
  Test 4 PASSED! (All fast-path exits well below 50ms)

  === Test 5: Non-dict & Malformed JSON Payloads ===
    autoplay [json_array        ]: rc=0, stderr_len=0
    narrator [json_array        ]: rc=0, stderr_len=0, delta=0
    autoplay [json_string       ]: rc=0, stderr_len=0
    narrator [json_string       ]: rc=0, stderr_len=0, delta=0
    autoplay [json_number       ]: rc=0, stderr_len=0
    narrator [json_number       ]: rc=0, stderr_len=0, delta=0
    autoplay [empty_string      ]: rc=0, stderr_len=0
    narrator [empty_string      ]: rc=0, stderr_len=0, delta=0
    autoplay [whitespace        ]: rc=0, stderr_len=0
    narrator [whitespace        ]: rc=0, stderr_len=0, delta=0
    autoplay [malformed_braces  ]: rc=0, stderr_len=0
    narrator [malformed_braces  ]: rc=0, stderr_len=0, delta=0
    autoplay [raw_text          ]: rc=0, stderr_len=0
    narrator [raw_text          ]: rc=0, stderr_len=0, delta=0
    autoplay [json_bool_true    ]: rc=0, stderr_len=0
    narrator [json_bool_true    ]: rc=0, stderr_len=0, delta=0
    autoplay [json_bool_false   ]: rc=0, stderr_len=0
    narrator [json_bool_false   ]: rc=0, stderr_len=0, delta=0
    autoplay [json_null         ]: rc=0, stderr_len=0
    narrator [json_null         ]: rc=0, stderr_len=0, delta=0
    autoplay [nested_array      ]: rc=0, stderr_len=0
    narrator [nested_array      ]: rc=0, stderr_len=0, delta=0
  Test 5 PASSED! (All non-dict/malformed payloads exit 0 with 0 stderr and no side-effects)

  === Test 6: Solo-scoping with Antigravity Sessions ===
  Test 6 PASSED! (Solo-scoping correctly arbitrates Antigravity session IDs)

  ==================================================
  ALL 6 CHALLENGER M3 EMPIRICAL STRESS TESTS PASSED!
  ==================================================
  ```

---

## 2. Logic Chain

1. **Antigravity Payload Extraction (Stress Test 1)**:
   - *Observation*: `autoplay_hook.sh:55` and `narrator_hook.sh:45` query `jq -r '(.session_id // .conversationId // empty)' 2>/dev/null || true`.
   - *Test Evidence*: Feeding payload `{"conversationId": "conv-adversary-1"}` into `autoplay_hook.sh` produced `/tmp/auto-speech-last-stop.conv-adversary-1` when enrolled, and yielded exit code 0 without creating the beacon when unenrolled. For `narrator_hook.sh`, feeding the payload logged an event with `payload.conversationId == "conv-adversary-1"` when enrolled, and delta=0 when unenrolled.
   - *Inference*: Antigravity sessions are correctly identified, enrolled, gated, and tracked across both hooks.

2. **Dual Payload Precedence (Stress Test 2)**:
   - *Observation*: jq expression `(.session_id // .conversationId)` evaluates `.session_id` first. If present and truthy, `.session_id` is returned and `.conversationId` is discarded.
   - *Test Evidence*: In Case 2B, when ONLY `conversationId` ("cid-precedence-2") was enrolled and `session_id` ("sid-precedence-1") was present in the payload, both hooks bailed with exit code 0, 0 events logged, and 0 beacons written. In Case 2C, when both were enrolled, the beacon created was `/tmp/auto-speech-last-stop.sid-precedence-1` and NEVER `cid-precedence-2`.
   - *Inference*: `session_id` has strict, deterministic precedence over `conversationId`.

3. **Global Mute Enforcement (Stress Test 3)**:
   - *Observation*: Both hooks check for existence of `$HOME/.claude/auto-speech.disabled` immediately after reading stdin and before launching any `jq` or worker subprocesses.
   - *Test Evidence*: When `$HOME/.claude/auto-speech.disabled` was created in isolated test homes, both hooks exited with returncode 0, 0 bytes on stderr, 0 events appended to `/tmp/auto-speech-narrator-events.jsonl`, and 0 beacons created, even when the sessions were actively enrolled.
   - *Inference*: Global mute short-circuits execution completely, cleanly, and silently.

4. **Latency Benchmark (Stress Test 4)**:
   - *Observation*: Both hooks bail at line 41/25 (suppression) or line 47/31 (mute) without spawning subshells or heavy external binaries.
   - *Test Evidence*: 50 iterations per condition measured via `time.perf_counter_ns()` demonstrated:
     - `autoplay_hook (suppressed)`: Min 13.69ms, Mean 16.46ms, Median 15.25ms, P95 20.16ms, Max 39.88ms.
     - `narrator_hook (suppressed)`: Min 11.17ms, Mean 13.10ms, Median 12.79ms, P95 15.23ms, Max 18.69ms.
     - `autoplay_hook (muted)`: Min 13.99ms, Mean 15.64ms, Median 15.45ms, P95 17.83ms, Max 18.62ms.
     - `narrator_hook (muted)`: Min 11.50ms, Mean 13.43ms, Median 13.22ms, P95 16.29ms, Max 16.52ms.
   - *Inference*: Fast-path exit times comfortably satisfy the <50ms threshold by a ~3x safety margin.

5. **Non-dict and Malformed Payload Handling (Stress Test 5)**:
   - *Observation*: Hook scripts pipe payload through `2>/dev/null || true` when running `jq`, and `narrator_hook.sh` checks `type == "object"` before constructing event records.
   - *Test Evidence*: Evaluated 11 adversarial inputs: JSON array (`[1, 2]`), JSON string (`"raw string"`), JSON number (`12345`), empty string (`""`), whitespace (`"   \n\t   "`), unclosed braces (`{"unclosed": "payload`), plain text, boolean true, boolean false, JSON null, and nested arrays. Every input resulted in returncode 0 and exactly 0 bytes written to stderr.
   - *Inference*: Malformed or non-dict inputs never crash the hooks or produce noisy stderr messages that would cause Claude Code hook invocation failures.

---

## 3. Caveats

- **Audio Playback Isolation**: During tests of `autoplay_hook.sh`, the spawned worker was tested with `AUTO_SPEECH_AUTOPLAY_MIN_LEN=99999999` to verify beacon creation and process detachment without invoking live speech synthesis or device playback during unit testing. Live audio playback is covered by the core suite in `test_native_audio_sink.py` and `test_synthesize_endpoint.py`.

---

## 4. Conclusion

The Milestone M3 shell hook implementations in `/Users/joshua/Developer/auto-speech-worktree` (`autoplay_hook.sh` and `narrator_hook.sh`):
1. Reliably extract and support Antigravity `.conversationId` sessions while preserving complete backward compatibility with Claude Code `.session_id`.
2. Strictly enforce payload precedence (`session_id > conversationId`).
3. Fully respect the global mute file (`~/.claude/auto-speech.disabled`), returning 0 without side effects.
4. Execute fast-path exit under suppression or mute in ~11–16 ms on average (well under 50 ms).
5. Gracefully handle non-dict and malformed payloads without errors or stderr leakage.

**Final Verdict: APPROVE.**

---

## 5. Verification Method

To independently reproduce and verify this assessment:
```bash
cd /Users/joshua/Developer/auto-speech-worktree
.venv/bin/python tests/test_challenger_m3.py
bash tests/run_all.sh
.venv/bin/ruff check plugin/
```
All commands must exit 0 with 0 errors.
