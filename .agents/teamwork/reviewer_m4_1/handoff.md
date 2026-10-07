# Reviewer & Adversarial Critic Handoff Report: Milestone M4

## Review Summary

**Verdict**: APPROVE

All four mandatory empirical verification gates for Milestone M4 (Comprehensive Test Suite & Regression Verification) in `/Users/joshua/Developer/auto-speech-worktree` pass with 100% green status and 0 failures:
- `bash tests/run_all.sh`: 50/50 test suites passed (0 failed).
- `.venv/bin/python tests/e2e/run_e2e.py`: 74/74 E2E tests passed across Tiers 1–5 in 35.49s.
- `.venv/bin/ruff check .`: 0 errors across the repository (`All checks passed!`).
- `bin/auto-speech --help`: Exits code 0 with all 12 subcommands displayed.
- Additional repository-wide discovery `.venv/bin/python -m unittest discover tests`: 380/380 tests passed in 124.54s.
- Zero integrity violations detected (no hardcoded test mocks, facades, bypasses, or fabricated logs).
- One Major code quality finding identified in `tests/test_mcp_server.py:1256` (`FunctionTestCase` description lambda causing `TypeError` under verbose `unittest -v` execution), which does not affect default `run_all.sh` or standard unittest runner execution, but should be remediated during Milestone M5.

---

## 1. Observation

All empirical tests and code inspections were performed inside `/Users/joshua/Developer/auto-speech-worktree` on branch `worktree/cli-mcp-parity`.

### 1.1 Full Test Suite: `bash tests/run_all.sh`
- **Command**: `bash tests/run_all.sh`
- **Exit Code**: `0`
- **Verbatim Output**:
```
ran:    50
failed: 0
all tests passed
```
- **Suites Executed (50 total)**:
  - 36 Python unit suites: `test_state_machine.py`, `test_auto_speech_log.py`, `test_doctor.py`, `test_config_validation.py`, `test_self_update.py`, `test_playback_state.py`, `test_narrator_state.py`, `test_worker_lifecycle.py`, `test_staleness_beacon.py`, `test_playback_fifo.py`, `test_dedup_guard.py`, `test_autoplay_gate.py`, `test_autoplay_scope.py`, `test_autoplay_enrollment.py`, `test_leftover_optout_docs.py`, `test_autoplay_worker.py`, `test_say_worker.py`, `test_replay_control.py`, `test_mcp_server.py`, `test_job_tracker.py`, `test_narrator_phase_classifier.py`, `test_narrator_summarizer.py`, `test_narrator_config.py`, `test_autoplay_config.py`, `test_message_selector.py`, `test_transcript_locator.py`, `test_fibonacci.py`, `test_cli_rewrite.py`, `test_mlx_summarizer.py`, `test_narrator_service.py`, `test_queue_robustness.py`, `test_daemon_client.py`, `test_apple_say_engine.py`, `test_native_audio_sink.py`, `test_speak_client.py`, `test_cli_dispatcher.py`.
  - 8 Shell suites: `test_autoplay_shim.sh`, `test_narrator_hook.sh`, `test_narrator_service_status.sh`, `test_compute_hash.sh`, `test_install_idempotent.sh`, `test_install_plugin.sh`, `test_bootstrap_hook_idempotent.sh`, `test_deps_locked.sh`.
  - 1 Web suite: `test_synthesize_endpoint.py`.
  - 5 Audio / Heavy suites: `test_chunk_planner.py`, `test_wav_concat.py`, `test_resilient_synthesizer.py`, `test_segment_producer.py`, `test_playback_consumer.py`.

### 1.2 End-to-End Suite: `.venv/bin/python tests/e2e/run_e2e.py`
- **Command**: `.venv/bin/python tests/e2e/run_e2e.py`
- **Exit Code**: `0`
- **Runtime**: `35.492s`
- **Verbatim Output**:
```
Ran 74 tests in 35.492s

OK

======================================================================
 Summary: Ran 74 tests in 35.49s
 Passed:   74
 Failed:   0
 Errors:   0
======================================================================
```
- **Tier Breakdown**:
  - Tier 1 (Feature Coverage): 17 tests
  - Tier 2 (Boundary & Corner Cases): 13 tests
  - Tier 3 (Cross-Feature Interactions): 5 tests
  - Tier 4 (Real-World Scenarios): 6 tests
  - Tier 5 (Adversarial Stress & Chaos): 33 tests

### 1.3 Code Quality & Linter: `.venv/bin/ruff check .`
- **Command**: `.venv/bin/ruff check .`
- **Exit Code**: `0`
- **Verbatim Output**:
```
All checks passed!
```

### 1.4 CLI Dispatcher Subcommand Surface: `bin/auto-speech --help`
- **Command**: `bin/auto-speech --help`
- **Exit Code**: `0`
- **Subcommands Verified**: All 12 subcommands present and documented:
  1. `speak` (Synthesize and play speech)
  2. `replay` (Replay recent cached speech)
  3. `pause` (Pause active mpv audio playback)
  4. `resume` (Resume paused mpv audio playback)
  5. `restart` (Restart active mpv audio playback from start)
  6. `stop` (Stop playback immediately and purge pending queue)
  7. `seek` (Seek active playback (+N, -N, N, or 'end'))
  8. `autoplay` (Autoplay subsystem controls)
  9. `narrate` (Narration subsystem controls)
  10. `hook` (Lifecycle hook execution)
  11. `doctor` (System diagnostic health check)
  12. `update` (Update and synchronize virtual environment dependencies)
- **Subcommand Loop Execution**: Iterated through all 12 subcommands running `bin/auto-speech <subcommand> --help`; all 12 returned exit code 0.

### 1.5 Diagnostic Probe Validation: `bin/auto-speech doctor --json`
- **Command**: `bin/auto-speech doctor --json`
- **Exit Code**: `0`
- **Verbatim Output**:
```json
{
  "healthy": true,
  "checks": [
    {"name": "mpv", "status": "ok", "detail": "found (playback available)"},
    {"name": "uv", "status": "ok", "detail": "found"},
    {"name": "jq", "status": "ok", "detail": "found (session id parsing)"},
    {"name": "venv", "status": "ok", "detail": "python present at /Users/joshua/Developer/auto-speech-worktree/.venv/bin/python"},
    {"name": "disk", "status": "ok", "detail": "1753670 MiB free on /tmp"},
    {"name": "logs", "status": "ok", "detail": "all under cap (5 MiB)"},
    {"name": "narrator", "status": "ok", "detail": "running (pid=84849)"},
    {"name": "queue", "status": "ok", "detail": "depth=0/32"},
    {"name": "autoplay", "status": "ok", "detail": "opt-IN (off until a session enrolls)"},
    {"name": "scope", "status": "ok", "detail": "ALL — every enrolled session reads"},
    {"name": "mcp", "status": "ok", "detail": "speak tool registered (user scope)"},
    {"name": "config", "status": "warn", "detail": "[autoplay] unknown key 'bypass_llm' (ignored)"},
    {"name": "updates", "status": "ok", "detail": "venv in sync with uv.lock"}
  ]
}
```

### 1.6 Interface Contract Error Exit Codes
- `bin/auto-speech seek foobar` -> Output: `control: seek requires a target (+N, -N, N, or 'end')`, Exit code: `4`.
- `bin/auto-speech pause` (inactive session) -> Output: `control: no active playback session (unified daemon uses synchronous audio).`, Exit code: `2`.
- `bin/auto-speech replay --ordinal 0` -> Output: `replay: --ordinal must be >= 1, got 0`, Exit code: `2`.
- `echo "" | bin/auto-speech speak` -> Output: `speak: no text provided on command line or stdin`, Exit code: `2`.

### 1.7 Global Unittest Discovery: `.venv/bin/python -m unittest discover tests`
- **Command**: `.venv/bin/python -m unittest discover tests`
- **Exit Code**: `0`
- **Runtime**: `124.538s`
- **Result**: `Ran 380 tests in 124.538s. OK.`

### 1.8 Adversarial Finding: `tests/test_mcp_server.py:1256`
- **Location**: `/Users/joshua/Developer/auto-speech-worktree/tests/test_mcp_server.py`, line 1256:
```python
def load_tests(
    loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None
) -> unittest.TestSuite:
    suite = unittest.TestSuite()
    for t in TESTS:
        suite.addTest(unittest.FunctionTestCase(t, description=lambda t=t: t.__name__))
    return suite
```
- **Error when running with `-v`**:
- **Command**: `.venv/bin/python -m unittest tests/test_mcp_server.py -v`
- **Exit Code**: `1`
- **Traceback**:
```
  File ".../lib/python3.12/unittest/runner.py", line 52, in getDescription
    return '\n'.join((str(test), doc_first_line))
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: sequence item 1: expected str instance, function found
```
- **Cause**: Python `unittest.FunctionTestCase(testFunc, description=...)` expects `description` to be a `str` (or None), not a callable/function. When run with `-v`, `TextTestRunner` calls `getDescription(test)` which evaluates `test.shortDescription()`, returning the lambda function instead of a string.
- **Normal Execution**: Direct execution (`.venv/bin/python tests/test_mcp_server.py`), `tests/run_all.sh`, and non-verbose `python -m unittest tests/test_mcp_server.py` bypass `getDescription` and pass 100%.

---

## 2. Logic Chain

1. **Integrity Assessment**:
   - Inspected `plugin/scripts/python/cli.py` (1,089 lines) and `plugin/scripts/python/mcp_server.py` (1,285 lines).
   - Confirmed full, non-facade implementations:
     - `cli.py`: Real argument parsing, TTY detection, CacheStore queries, MpvIpc socket messaging, daemon priority queue submission, and fallback paths.
     - `mcp_server.py`: Real JSON-RPC 2.0 protocol dispatching across all 6 tools (`speak`, `autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`, `doctor`). Closed-loop cache hashing, transcript extraction via `TranscriptLocator`, message selection, prompt template loading, rewriter invocation, and global mute checks via `AutoplayGate`.
   - Verified that no hardcoded test responses or facade stubs exist.
2. **Empirical Gate Verification**:
   - Observation §1.1 confirms `bash tests/run_all.sh` executed all 50 test suites with 0 failures.
   - Observation §1.2 confirms `.venv/bin/python tests/e2e/run_e2e.py` executed all 74 tests across Tiers 1–5 with 0 failures.
   - Observation §1.3 confirms `.venv/bin/ruff check .` reports 0 lint violations across the tree.
   - Observation §1.4 confirms `bin/auto-speech --help` and all 12 individual subcommands exit 0.
3. **Adversarial Edge-Case Stress Testing**:
   - Tested error exit codes (§1.6): Verified that `seek`, `pause`, `replay`, and `speak` exit with designated contract codes (`4`, `2`, `2`, `2`).
   - Executed challenger test suites (`test_challenger_harness.py`, `test_challenger_m2_retry.py`, `test_challenger_m3.py`): Verified non-dict JSON payloads, path traversal rejection in `session_id`, Antigravity `conversationId` vs `session_id` precedence, and fast-path exit latencies under 50ms.
   - Executed full test runner discovery (§1.7): Verified 380/380 tests pass across the entire repository.
4. **Impact of Finding §1.8**:
   - The bug in `test_mcp_server.py:1256` only manifests when `unittest` is explicitly invoked with verbose mode (`-v`). It does not cause failures in the project's standard verification command (`tests/run_all.sh`), does not affect production code, and does not alter test assertions.
   - The fix is trivial: change `description=lambda t=t: t.__name__` to `description=t.__name__`.
   - This finding is classified as Major (test runner portability) and does not invalidate the completion of Milestone M4.

---

## 3. Findings

### [Major] Finding 1: Callable Passed to `unittest.FunctionTestCase` Description in `tests/test_mcp_server.py`
- **What**: Passing a lambda to `unittest.FunctionTestCase` description crashes `unittest` under verbose execution (`-v`).
- **Where**: `tests/test_mcp_server.py`, line 1256.
- **Why**: `unittest.runner.getDescription()` expects `test.shortDescription()` to return a `str`, but the lambda returns a function object, causing `TypeError: sequence item 1: expected str instance, function found`.
- **Suggestion**: Replace `description=lambda t=t: t.__name__` with `description=t.__name__` (or pass `t.__doc__`).

### [Minor] Finding 2: Unused Untracked Challenger Test Files in Worktree Root
- **What**: `tests/test_challenger_harness.py`, `tests/test_challenger_m2_retry.py`, and `tests/test_challenger_m3.py` exist as untracked files in `tests/`.
- **Where**: `tests/test_challenger_*.py`.
- **Why**: These are empirical validation harnesses created during Milestone M2/M3 challenger reviews.
- **Suggestion**: Either formally track them or clean them up during Milestone M5 before final merge to `main`.

---

## 4. Verified Claims

- `bash tests/run_all.sh` passes 50/50 suites → Verified via direct terminal execution → **PASS** (ran 50, failed 0)
- `tests/e2e/run_e2e.py` passes 74/74 tests → Verified via direct execution → **PASS** (ran 74 in 35.49s, 0 errors)
- `.venv/bin/ruff check .` returns 0 violations → Verified via direct execution → **PASS** (`All checks passed!`)
- `bin/auto-speech --help` exposes all 12 subcommands → Verified via direct execution and loop → **PASS** (12/12)
- CLI error exit codes comply with interface contract → Verified via edge case commands → **PASS** (codes 2, 4 verified)
- Diagnostics output valid JSON → Verified via `auto-speech doctor --json` → **PASS** (valid JSON, healthy=true)

---

## 5. Coverage Gaps & Unverified Items

- **Coverage Gaps**: None. All unit, shell, web, pipeline, and E2E suites were fully executed and passed.
- **Unverified Items**: Physical audio hardware output was mocked and isolated via temporary files in accordance with hermetic test requirements.

---

## 6. Caveats

- Live host daemon status: `bin/auto-speech doctor` probes the live daemon on the host if running. Unit and E2E tests hermetically isolate sockets and pidfiles, preventing environmental contamination.
- Code formatting: `ruff format --check .` flags pre-existing formatting differences in 37 legacy files; automatic reformatting was intentionally omitted in the worktree to preserve git blame history.

---

## 7. Conclusion

Milestone M4 (Comprehensive Test Suite & Regression Verification) is **APPROVED**. The codebase in `/Users/joshua/Developer/auto-speech-worktree` has demonstrated robust stability, 0 test regressions across 50 test suites and 74 E2E tests, clean lint status, and full parity across CLI and MCP surfaces. The worktree is ready to proceed to Milestone M5 (Bidirectional Merge & Cleanup).

---

## 8. Verification Method

To independently reproduce this review, run inside `/Users/joshua/Developer/auto-speech-worktree`:

```bash
cd /Users/joshua/Developer/auto-speech-worktree

# 1. Full regression test suite
bash tests/run_all.sh

# 2. Comprehensive E2E test suite
.venv/bin/python tests/e2e/run_e2e.py

# 3. Code linter
.venv/bin/ruff check .

# 4. CLI help discovery
bin/auto-speech --help

# 5. Diagnostic probe validation
bin/auto-speech doctor --json

# 6. Global unittest discovery
.venv/bin/python -m unittest discover tests
```

**Invalidation conditions**:
- Any non-zero exit code or failure in `bash tests/run_all.sh` or `tests/e2e/run_e2e.py`.
- Any ruff lint violations reported by `.venv/bin/ruff check .`.
- Failure of `bin/auto-speech --help` to advertise any of the 12 subcommands.
