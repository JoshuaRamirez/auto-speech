# Handoff Report: Milestone M2 Remediation Review (reviewer_m2_r2_1)

## Review Summary

**Verdict**: APPROVE

---

## 1. Observation

### Code Implementations & Fix Verification
1. **`plugin/scripts/python/narrator_service.py` (lines 477–501)**:
   In `NarratorService.dispatch_json` under action `"play_cache"`:
   ```python
   elif action == "play_cache":
       source_hash = payload.get("source_hash")
       if (
           not isinstance(source_hash, str)
           or len(source_hash) != 64
           or not all(c in "0123456789abcdef" for c in source_hash)
       ):
           return {
               "status": "error",
               "error_code": "INVALID_PAYLOAD",
               "message": "Invalid or missing source_hash (expected 64-character lowercase hex string)",
           }

       try:
           hit = self._cache.lookup(source_hash)
       except Exception:
           hit = None

       if hit is None:
           return {
               "status": "error",
               "error_code": "CACHE_MISS",
               "message": f"Cache miss for {source_hash}",
           }
   ```
   Direct empirical execution of `.venv/bin/python tests/test_challenger_m2_cache_stress.py` executes 17 test cases verifying:
   - Missing or `None` `source_hash` returns `INVALID_PAYLOAD` (pass).
   - Non-string `source_hash` (e.g. `int`, `list`) returns `INVALID_PAYLOAD` (pass).
   - Off-by-one lengths (0, 3, 63, 65, 128 characters) return `INVALID_PAYLOAD` (pass).
   - Non-hex characters (e.g. `'g'`, `'z'`, uppercase `'X'`, whitespace, punctuation, null bytes) return `INVALID_PAYLOAD` (pass).
   - Syntactically valid 64-hex hash missing from `CacheStore` returns `CACHE_MISS` (pass).

2. **`plugin/scripts/python/replay.py` (lines 32–38)**:
   ```python
   def _is_mocked(cls: Any) -> bool:
       """Detect if NativeAudioSink or its play method has been replaced by a unittest mock."""
       if not isinstance(cls, type) or hasattr(cls, "mock_calls") or hasattr(cls, "_mock_return_value"):
           return True
       play_fn = getattr(cls, "play", None)
       return play_fn is not None and hasattr(play_fn, "mock_calls")
   ```
   Inspecting `getattr(cls, "play", None)` detects method-level mocks applied via `mock.patch.object(replay.NativeAudioSink, "play")` while maintaining 100% parity with `http_routing._is_sink_mocked`.

3. **`tests/test_challenger_m2_stress.py` (lines 286–314)**:
   In `test_replay_mock_preservation_method_mock_gap_finding`:
   ```python
   rc = replay.main(["--ordinal", "1"])
   self.assertEqual(rc, replay.EXIT_OK)

   # Method mock must be invoked and daemon routing must NOT be called:
   self.assertEqual(mock_play.call_count, 1)
   mock_play.assert_called_once_with(self.promoted_wav)
   self.assertEqual(daemon_sink.play.call_count, 0)
   ```
   Verified that when `replay.NativeAudioSink.play` is patched, execution bypasses daemon socket routing (`daemon_sink.play.call_count == 0`) and directly invokes the local method mock (`mock_play.call_count == 1`).

### Empirical Test Execution Results
- `.venv/bin/python tests/test_challenger_m2_cache_stress.py`: 17/17 tests passed in 0.664s.
- `.venv/bin/python tests/test_challenger_m2_stress.py`: 19/19 tests passed in 8.462s.
- `.venv/bin/python tests/test_replay_control.py`: 10/10 tests passed.
- `.venv/bin/python tests/test_synthesize_endpoint.py`: 12/12 tests passed.
- `.venv/bin/python tests/test_narrator_service.py`: 26/26 tests passed.
- `.venv/bin/python tests/test_native_audio_sink.py`: 12/12 tests passed.
- `.venv/bin/python tests/e2e/run_e2e.py`: 74/74 tests passed across Tiers 1–5 in 31.94s.
- `bash tests/run_all.sh --web`: 12/12 tests passed.
- `.venv/bin/ruff check .`: 0 errors / all checks passed.

### Diagnostic Finding on `tests/test_challenger_m3_caller_realignment.py`
During full test run verification, `tests/run_all.sh --hermetic` halted at `test_challenger_m3_caller_realignment.py`. Direct investigation into process stacks revealed:
- `test_challenger_m3_caller_realignment.py` (line 535) sets up a mock cache entry `meta.json` omitting the required field `'chars_per_second_at_creation'`.
- `CacheStore.list_by_recency()` rejected the entry as an orphan, causing `replay.py` to fall back to `config/cache/aaaaaaaaaaaaaaaa/full.wav` (1.925s duration).
- Running `replay.py` in an unmocked subprocess invokes `/opt/homebrew/bin/mpv` targeting real hardware CoreAudio (`MacBook Pro Speakers`).
- This defect is strictly confined to the Milestone M3 test harness (`test_challenger_m3_caller_realignment.py`) and is completely unrelated to Milestone M2 remediation code.

---

## 2. Logic Chain

1. **RFC §4.1.1 & §4.1.2 Schema Conformance (BUG-M2-01)**:
   - RFC §4.1.1 specifies `PlayCacheRequest` requires `"source_hash"` with regex pattern `^[0-9a-f]{64}$`.
   - RFC §4.1.2 specifies `error_code` distinguishes schema/payload errors (`INVALID_PAYLOAD`) from cache misses (`CACHE_MISS`).
   - Prior code converted inputs via `str(payload.get("source_hash", "")).strip().lower()`, which improperly returned `CACHE_MISS` for empty, malformed, non-hex, or missing hash parameters.
   - The remediation in `narrator_service.py:477-501` enforces rigorous type and regex validation (`isinstance(source_hash, str)`, length 64, lowercase hex characters) before any cache query. Invalid requests immediately return `INVALID_PAYLOAD`.
   - Genuine cache misses on syntactically valid hashes return `CACHE_MISS`.
   - Handled cache lookup exceptions safely by setting `hit = None`, preventing daemon crashes on corrupt cache directories.

2. **Mock Preservation and Test Isolation (BUG-M2-02)**:
   - When unit tests patch a class method (`mock.patch.object(NativeAudioSink, "play")`), the class object itself remains an instance of `type` and does not contain `mock_calls` on the class itself.
   - The prior `_is_mocked` implementation failed to inspect class attributes, causing `replay.py` to misidentify patched sinks as unmocked and route playback to the live daemon socket.
   - The updated `_is_mocked` inspects `play_fn = getattr(cls, "play", None)` and returns `True` if `hasattr(play_fn, "mock_calls")`. This guarantees mock interception occurs before socket communication is attempted.
   - The updated assertion in `test_challenger_m2_stress.py` validates that `mock_play` is called once and `daemon_sink.play` is not called.

3. **Integrity Violation Audit**:
   - Zero hardcoded test outputs or return values embedded in source code.
   - Zero facade or dummy mock logic in production files.
   - All logic is generic, defensive, and fully adheres to project conventions.

---

## 3. Caveats

- **Scope Boundary**: Review was scoped strictly to Milestone M2 remediations (`narrator_service.py`, `replay.py`, `tests/test_challenger_m2_stress.py`).
- **M3 Test Suite Note**: `tests/test_challenger_m3_caller_realignment.py` is an unmerged Milestone M3 test file containing a malformed `meta.json` fixture; this should be remediated during Milestone M3 work.

---

## 4. Conclusion

The remediations implemented by `worker_m2_r2` for Milestone M2 are **fully verified, robust, and mathematically sound**.
- BUG-M2-01 is genuinely fixed with strict RFC §4.1.1/§4.1.2 schema compliance.
- BUG-M2-02 is genuinely fixed with robust method-mock detection parity.
- All target unit, stress, web, and E2E suites pass with 0 failures and 0 ruff lint errors.
- Verdict: **APPROVE**.

---

## 5. Verification Method

To independently reproduce this verification:

1. **M2 Cache Stress Verification**:
   ```bash
   .venv/bin/python tests/test_challenger_m2_cache_stress.py
   ```
   Expected: 17/17 tests pass.

2. **M2 Concurrency & SAO Stress Verification**:
   ```bash
   .venv/bin/python tests/test_challenger_m2_stress.py
   ```
   Expected: 19/19 tests pass.

3. **Core Unit Suites**:
   ```bash
   .venv/bin/python tests/test_replay_control.py
   .venv/bin/python tests/test_synthesize_endpoint.py
   .venv/bin/python tests/test_narrator_service.py
   .venv/bin/python tests/test_native_audio_sink.py
   ```
   Expected: All tests pass.

4. **Full E2E Suite (Tiers 1–5)**:
   ```bash
   .venv/bin/python tests/e2e/run_e2e.py
   ```
   Expected: 74/74 tests pass.

5. **Linter & Code Quality**:
   ```bash
   .venv/bin/ruff check .
   ```
   Expected: 0 errors.
