# Handoff Report: Milestone M2 Iteration 2 Remediation (worker_m2_r2)

## 1. Observation

### Initial Failures & Code Gaps Observed

1. **`plugin/scripts/python/narrator_service.py` (lines 477–485 prior to fix)**:
   In `NarratorService.dispatch_json` under action `"play_cache"`:
   ```python
   elif action == "play_cache":
       source_hash = str(payload.get("source_hash", "")).strip().lower()
       if len(source_hash) != 64 or not all(c in "0123456789abcdef" for c in source_hash):
           return {
               "status": "error",
               "error_code": "CACHE_MISS",
               "message": f"Cache miss for {source_hash}",
           }
   ```
   Direct tool execution of `.venv/bin/python tests/test_challenger_m2_cache_stress.py` produced 3 test failures:
   ```
   FAIL: test_play_cache_invalid_hex_chars_returns_invalid_payload (__main__.TestPlayCacheErrorHandling.test_play_cache_invalid_hex_chars_returns_invalid_payload)
   AssertionError: 'CACHE_MISS' != 'INVALID_PAYLOAD'
   - CACHE_MISS
   + INVALID_PAYLOAD
    : Expected INVALID_PAYLOAD for non-hex hash, but got CACHE_MISS! Full response: {'status': 'error', 'error_code': 'CACHE_MISS', 'message': 'Cache miss for gggggggggggggggggggggggggggggggggggggggggggggggggggggggggggggggg'}

   FAIL: test_play_cache_invalid_hex_length_returns_invalid_payload (__main__.TestPlayCacheErrorHandling.test_play_cache_invalid_hex_length_returns_invalid_payload)
   AssertionError: 'CACHE_MISS' != 'INVALID_PAYLOAD'
   - CACHE_MISS
   + INVALID_PAYLOAD
    : Expected INVALID_PAYLOAD for invalid length 0, but got CACHE_MISS! Full response: {'status': 'error', 'error_code': 'CACHE_MISS', 'message': 'Cache miss for '}

   FAIL: test_play_cache_missing_or_non_string_source_hash_returns_invalid_payload (__main__.TestPlayCacheErrorHandling.test_play_cache_missing_or_non_string_source_hash_returns_invalid_payload)
   AssertionError: 'CACHE_MISS' != 'INVALID_PAYLOAD'
   - CACHE_MISS
   + INVALID_PAYLOAD
    : Expected INVALID_PAYLOAD for payload {'action': 'play_cache'}, but got CACHE_MISS! Full response: {'status': 'error', 'error_code': 'CACHE_MISS', 'message': 'Cache miss for '}
   ```
   Running `bash tests/run_all.sh --hermetic` similarly resulted in:
   ```
   ====================
   ran:    43
   failed: 1
     - test_challenger_m2_cache_stress.py
   ```

2. **`plugin/scripts/python/replay.py` (lines 32–34 prior to fix)**:
   `replay._is_mocked` checked only:
   ```python
   def _is_mocked(cls: Any) -> bool:
       """Detect if NativeAudioSink has been replaced by a unittest mock."""
       return not isinstance(cls, type) or hasattr(cls, "mock_calls") or hasattr(cls, "_mock_return_value")
   ```
   When callers applied `mock.patch.object(replay.NativeAudioSink, "play") as mock_play`, `NativeAudioSink` remained a class (`isinstance(cls, type)` was `True`), causing `_is_mocked` to return `False`. In contrast, `plugin/scripts/python/http_routing.py:129-136` checked `getattr(sink, "play", None)` for `hasattr(play_fn, "mock_calls")`.

3. **`tests/test_challenger_m2_stress.py` (lines 286–319 prior to fix)**:
   In `test_replay_mock_preservation_method_mock_gap_finding`, the test previously recorded the limitation rather than enforcing the fix:
   ```python
   self.assertEqual(mock_play.call_count, 0)
   svc._tts_queue.join()
   self.assertEqual(daemon_sink.play.call_count, 1)
   ```

---

## 2. Logic Chain

1. **Root Cause Analysis of BUG-M2-01 (`play_cache` validation)**:
   - Per RFC §4.1.2 (`reports/AutoSpeech-Sublimation-RFC-2026-10-04-074610.md`), `error_code` distinguishes schema/payload errors (`INVALID_PAYLOAD`) from cache misses (`CACHE_MISS`).
   - Coercing `payload.get("source_hash", "")` via `str()` converted `None`, missing keys, integers, and lists into strings without validation, and then returned `CACHE_MISS` for invalid lengths or characters.
   - **Remediation**: Explicitly validate `source_hash = payload.get("source_hash")` to check that `source_hash` is an `isinstance(source_hash, str)`, exactly 64 characters long, and comprised exclusively of lowercase hex characters (`0123456789abcdef`). Return `error_code: "INVALID_PAYLOAD"` with message `"Invalid or missing source_hash (expected 64-character lowercase hex string)"` upon failure. If valid, proceed to lookup; only return `error_code: "CACHE_MISS"` if the cache lookup fails.

2. **Root Cause Analysis of BUG-M2-02 (`replay._is_mocked` parity)**:
   - Unit tests frequently mock individual methods rather than entire classes using `mock.patch.object(replay.NativeAudioSink, "play")`.
   - `replay.py`'s fallback check only checked if `NativeAudioSink` class was replaced or had `mock_calls`. When `play` was patched on the class, `_is_mocked(NativeAudioSink)` returned `False`, causing `_route_play_cache_to_daemon()` to be invoked instead of executing the method mock.
   - **Remediation**: Updated `replay._is_mocked` to check `play_fn = getattr(cls, "play", None)` and return `True` if `play_fn is not None and hasattr(play_fn, "mock_calls")`. This brings `replay.py` into 100% behavioral alignment with `http_routing._is_sink_mocked`.

3. **Verification and Assertion Realignment in `test_challenger_m2_stress.py`**:
   - Updated `test_replay_mock_preservation_method_mock_gap_finding` to assert the resolved behavior: when `replay.NativeAudioSink.play` is patched, `_is_mocked` detects it, intercepts execution, calls `mock_play` once with the promoted WAV, and does NOT route playback to the daemon socket (`mock_play.call_count == 1`, `daemon_sink.play.call_count == 0`).

---

## 3. Caveats

- **Scope Boundary**: Changes were strictly confined to `plugin/scripts/python/narrator_service.py`, `plugin/scripts/python/replay.py`, and `tests/test_challenger_m2_stress.py`. No extraneous refactoring was performed.
- **Darwin Audio Device**: Tests were executed hermetically and with mock sinks. Real hardware playback via `mpv` was verified via E2E test runs.

---

## 4. Conclusion

All reported bugs from the Challenger M2 stress audits (BUG-M2-01 and BUG-M2-02) have been genuinely resolved:
1. `narrator_service.py` now discriminates between `INVALID_PAYLOAD` (malformed or missing `source_hash`) and `CACHE_MISS` (valid hash missing from `CacheStore`).
2. `replay.py` accurately identifies method mocks on `NativeAudioSink.play`, preserving unit test isolation and preventing unintended socket calls.
3. All 8 verification gates pass with 100% success rate (0 failures, 0 errors, 0 ruff lint violations).

---

## 5. Verification Method

To independently verify this implementation, execute the following commands from the repository root:

1. **Challenger M2 Cache Stress Suite**:
   ```bash
   .venv/bin/python tests/test_challenger_m2_cache_stress.py
   ```
   *Result*: 17/17 tests PASS (0 failures, 0 errors).

2. **Challenger M2 Stress Suite**:
   ```bash
   .venv/bin/python tests/test_challenger_m2_stress.py
   ```
   *Result*: 19/19 tests PASS (0 failures, 0 errors).

3. **Replay Control Unit Suite**:
   ```bash
   .venv/bin/python tests/test_replay_control.py
   ```
   *Result*: 10/10 tests PASS (0 failures, 0 errors).

4. **Synthesize Endpoint Suite**:
   ```bash
   .venv/bin/python tests/test_synthesize_endpoint.py
   ```
   *Result*: 12/12 tests PASS (0 failures, 0 errors).

5. **Full Hermetic Suite**:
   ```bash
   bash tests/run_all.sh --hermetic
   ```
   *Result*: 43/43 suites PASS (0 failed).

6. **Web Suite**:
   ```bash
   bash tests/run_all.sh --web
   ```
   *Result*: 1/1 suite (12/12 tests) PASS.

7. **Full End-to-End Suite**:
   ```bash
   .venv/bin/python tests/e2e/run_e2e.py
   ```
   *Result*: 74/74 tests PASS across Tiers 1–5 (0 failures, 0 errors).

8. **Lint & Code Formatting**:
   ```bash
   .venv/bin/ruff check .
   ```
   *Result*: All checks passed (0 violations).
