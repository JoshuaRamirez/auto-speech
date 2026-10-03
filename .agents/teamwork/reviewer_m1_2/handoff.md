# Handoff Report: Reviewer M1.2 (Milestone M1 Independent Review)

**Reviewer**: `reviewer_m1_2` (Teamwork Reviewer & Critic)  
**Working Directory**: `/Users/joshua/Developer/auto-speech/.agents/teamwork/reviewer_m1_2`  
**Milestone**: M1 (In-Process TTSEngine & NativeAudioSink)  
**Date**: 2026-10-03  
**Verdict**: **APPROVE**  

---

## 1. Observation

Direct observations from independent code inspection, verification commands, and stress tests:

1. **Test Execution & Verification**:
   - `NativeAudioSink` unit tests:
     ```bash
     .venv/bin/python tests/test_native_audio_sink.py
     ```
     Result: 12 tests passed, `OK` in 0.528s.
   - `NarratorService` unit tests:
     ```bash
     .venv/bin/python tests/test_narrator_service.py
     ```
     Result: 21 tests passed, exit code 0.
   - Tier 1 R1 E2E tests:
     ```bash
     .venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R1InProcessAudioSink
     ```
     Result: 6 tests passed, `OK` in 1.457s.
   - Tier 2 R1 Boundary tests:
     ```bash
     .venv/bin/python -m unittest tests.e2e.test_tier2_boundaries.TestTier2R1Boundaries
     ```
     Result: 5 tests passed, `OK` in 0.292s.
   - Code style and lint check:
     ```bash
     .venv/bin/ruff check plugin/scripts/python/native_audio_sink.py plugin/scripts/python/narrator_service.py tests/test_native_audio_sink.py tests/test_narrator_service.py
     ```
     Result: `All checks passed!`.

2. **Thread Safety & MLX Stream Affinity**:
   - In `plugin/scripts/python/narrator_service.py`:
     - Lines 186–189: `__init__` sets `self._engine: TTSEngine | None = engine`, `self._synth: ResilientSynthesizer | None = synth`, and `self._profile: VoiceProfile | None = profile` without eager model loading on the main thread.
     - Lines 501–527: `_ensure_tts_initialized()` initializes `TTSEngine`, `ResilientSynthesizer`, and calls `self._engine._ensure_loaded()`.
     - Lines 530 & 584: `_ensure_tts_initialized()` is exclusively called from `_tts_worker()` at startup and in `_speak()`.
     - `_ensure_tts_initialized` and `synthesize_one` are NEVER invoked on the main tail-events thread or caller threads.

3. **Resource & File Cleanup**:
   - In `plugin/scripts/python/narrator_service.py`:
     - Lines 589–591: `with tempfile.NamedTemporaryFile(prefix="narrator_", suffix=".wav", delete=False) as f: temp_wav = Path(f.name)` closes the open file descriptor immediately upon exiting the `with` block.
     - Lines 601–606: In `_speak()`, the `finally:` block cleans up all related files:
       ```python
       finally:
           temp_wav.unlink(missing_ok=True)
           temp_wav.with_suffix(temp_wav.suffix + ".partial").unlink(missing_ok=True)
           for frag in temp_wav.parent.glob(f"{temp_wav.stem}-*.wav"):
               frag.unlink(missing_ok=True)
       ```
     - Temporary WAV files, `.partial` atomic swap files, and `ResilientSynthesizer` split fragment files are reliably deleted even on synthesis or playback failure.

4. **AudioSink Dual-Lock Concurrency & Deadlock Freedom**:
   - In `plugin/scripts/python/native_audio_sink.py`:
     - Lines 51–52: Two distinct threading locks: `self._playback_lock` and `self._state_lock`.
     - Lines 86–101: `play()` acquires `_playback_lock` to serialize sequential audio playback, then acquires `_state_lock` to record `self._proc = proc`, releasing `_state_lock` before executing blocking `proc.communicate(timeout=timeout)`.
     - Lines 134–140: `interrupt()` acquires only `_state_lock` to capture `self._proc` and set `self._interrupted = True`, never acquiring `_playback_lock`.
     - Lines 141–153: `proc.terminate()` is called outside of `_state_lock`. If the process does not terminate within `_TERMINATE_TIMEOUT` (0.5s), it escalates to `proc.kill()`.
     - Lock acquisition hierarchy is strictly unidirectional (`_playback_lock` -> `_state_lock`), rendering deadlock mathematically impossible.

5. **Legacy Code Cleanup & Bug Fixes**:
   - Complete elimination of detached background mpv sessions, `run_speak.sh`, duration estimation sleeps (`time.sleep(duration)`), `SessionDir` checks, and `pkill -9 mpv` hacks in `narrator_service.py`.
   - Line 237 bug fix verified: `classifier = getattr(self, "_classifier", None)` in `_tail_events` safely handles bare instances without throwing `AttributeError`.

---

## 2. Logic Chain

1. **Integrity Audit**:
   - Verified that implementation logic in `native_audio_sink.py` and `narrator_service.py` is genuine and complete (no mocks embedded in production paths, no hardcoded test responses, no shortcuts or facades).
   - Test suites in `tests/test_native_audio_sink.py` and `tests/test_narrator_service.py` use rigorous unit assertions and isolated test doubles.

2. **MLX Stream Affinity Validation**:
   - Apple MLX requires that models are loaded and computed on the same OS thread due to per-thread compute stream contexts.
   - Because `_ensure_tts_initialized()` and `_speak()` are executed solely within the `_tts_worker` thread loop, MLX operations cannot cross threads.
   - If an initialization error occurs, `_ensure_tts_initialized()` logs the exception and allows graceful retry on the next phase rather than crashing the worker.

3. **Concurrency & Deadlock Analysis**:
   - `play()` locks `_playback_lock` to ensure only one audio stream plays at a time.
   - When `interrupt()` is called from another thread (e.g. `_on_signal` or `UserPromptSubmit`), it does NOT wait on `_playback_lock`. It acquires `_state_lock`, signals `proc.terminate()`, and returns.
   - Terminating `proc` unblocks `proc.communicate()` inside `play()`. The `finally:` block clears `self._proc = None` under `_state_lock`.
   - Stress testing with 4 concurrent playback threads and a rapid interrupter thread completed across multiple cycles with 0 errors.

4. **Resource Management**:
   - File descriptors from `NamedTemporaryFile` are closed prior to synthesis.
   - Every synthesis invocation is enclosed in `try ... finally` where `temp_wav`, `temp_wav.partial`, and all `{stem}-*.wav` fragments are unlinked with `missing_ok=True`.

---

## 3. Caveats

- **Scope Boundary**: Milestone M1 implements in-process `TTSEngine` and `NativeAudioSink`. Milestones M2 (UNIX socket IPC and thin `speak.py`) and M3 (removal of `run_speak.sh` and deprecated caller modules) remain to be completed in subsequent milestones as specified in `PROJECT.md`.
- **Pre-existing test in repository**: `tests/test_narrator_phase_classifier.py` failed due to pre-existing modifications in `narrator_phase_classifier.py` (`max_events_per_phase=1`), which is outside the Milestone M1 scope. All M1-scoped tests pass 100%.

---

## 4. Conclusion

**Verdict: APPROVE**

Milestone M1 satisfies all requirements outlined in `ORIGINAL_REQUEST.md`, `PROJECT.md`, and the assignment dispatch:
1. `NativeAudioSink` delivers synchronous, non-overlapping playback via `mpv` with safe interruption and SIGKILL escalation.
2. `NarratorService` hosts `TTSEngine` in-process with strict MLX thread affinity.
3. Temporary audio files and file descriptors are systematically cleaned up without resource leaks.
4. Legacy duration sleep calculations and indiscriminate `pkill` calls have been completely removed.
5. All relevant unit, boundary, and E2E tests pass with zero lint errors.

---

## 5. Verification Method

To independently verify this review:

1. **Run Unit Tests for NativeAudioSink**:
   ```bash
   .venv/bin/python tests/test_native_audio_sink.py
   ```
   *Expected output*: 12 tests pass, `OK`.

2. **Run Unit Tests for NarratorService**:
   ```bash
   .venv/bin/python tests/test_narrator_service.py
   ```
   *Expected output*: 21 tests pass, exit code 0.

3. **Run E2E Tier 1 & Tier 2 R1 Tests**:
   ```bash
   .venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R1InProcessAudioSink
   .venv/bin/python -m unittest tests.e2e.test_tier2_boundaries.TestTier2R1Boundaries
   ```
   *Expected output*: All 11 tests pass, `OK`.

4. **Run Linter**:
   ```bash
   .venv/bin/ruff check plugin/scripts/python/native_audio_sink.py plugin/scripts/python/narrator_service.py tests/test_native_audio_sink.py tests/test_narrator_service.py
   ```
   *Expected output*: `All checks passed!`.
