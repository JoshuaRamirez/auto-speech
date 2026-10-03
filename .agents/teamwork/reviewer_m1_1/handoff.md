# Review & Adversarial Challenge Report: Milestone M1

**Reviewer**: `reviewer_m1_1` (roles: `reviewer`, `critic`)  
**Working Directory**: `/Users/joshua/Developer/auto-speech/.agents/teamwork/reviewer_m1_1`  
**Milestone**: M1 (In-Process TTSEngine & NativeAudioSink)  
**Date**: 2026-10-03  
**Verdict**: **APPROVE**  
**Overall Risk Assessment**: **LOW**

---

## 1. Observation

1. **Reviewed Artifacts**:
   - `plugin/scripts/python/native_audio_sink.py` (Lines 1–165): Implements `NativeAudioSink` with synchronous `mpv` invocation via `subprocess.Popen` and `proc.communicate(timeout=timeout)`. CLI invocation passes `[mpv_bin, "--really-quiet", "--no-video", "--keep-open=no", "--idle=no", str(wav_path)]`. Manages concurrency via `_playback_lock` (serializes playback) and `_state_lock` (guards `_proc` and `_interrupted`). Implements graceful interrupt via `SIGTERM` escalating to `SIGKILL` if unhandled after 0.5s.
   - `plugin/scripts/python/narrator_service.py` (Lines 1–652):
     - Lines 185–189: Injects `NativeAudioSink`, `TTSEngine`, `ResilientSynthesizer`, and `VoiceProfile`.
     - Lines 245–247: Invokes `self._sink.interrupt()` on OS termination signals (`SIGTERM`, `SIGINT`).
     - Lines 292–300: Accesses `_classifier` using `classifier = getattr(self, "_classifier", None)`, guarding against missing attribute on uninitialized/mocked instances.
     - Lines 374–376: Invokes `self._sink.interrupt()` immediately on `UserPromptSubmit` event.
     - Lines 501–527: Defines `_ensure_tts_initialized()`, loading model weights and synthesizer strictly inside the `_tts_worker` thread to preserve Apple MLX single-thread stream affinity.
     - Lines 572–606: Refactors `_speak()` to synthesize in-process with `self._synth.synthesize_one(line, self._profile, temp_wav)` and play synchronously with `self._sink.play(temp_wav)`. Unlinks `temp_wav`, `.partial`, and fragment files in `finally:`.
     - Verbatim grep results across `narrator_service.py`:
       - `_speak_script`: 0 matches.
       - `run_speak.sh`: 0 matches.
       - `time.sleep`: 0 matches.
       - `wave.open`: 0 matches.
       - `SessionDir`: 0 matches.
       - `pkill`: 0 matches.
       - `SIGKILL`: 0 matches.
   - `tests/test_native_audio_sink.py` (Lines 1–285): 12 comprehensive unit and integration tests covering missing files, missing `mpv`, successful playback, non-zero exit code error raising, interrupt with SIGTERM, escalation to SIGKILL, idle interrupt no-op, custom binary override, timeout handling, concurrent serialization, and real system `mpv` smoke tests.
   - `tests/test_narrator_service.py` (Lines 1–541): 21 unit tests covering daemon lifecycle, PID reuse protection, sweep logic, chunk parsing, drop-oldest queue overflow, resume across split reads, and 6 new in-process speech tests.

2. **Executed Verification Commands & Verbatim Output**:
   - `.venv/bin/python tests/test_native_audio_sink.py`:
     ```
     test_concurrent_playback_serialized (__main__.TestNativeAudioSink.test_concurrent_playback_serialized) ... ok
     test_custom_mpv_path_override (__main__.TestNativeAudioSink.test_custom_mpv_path_override) ... ok
     test_interrupt_active_playback (__main__.TestNativeAudioSink.test_interrupt_active_playback) ... ok
     test_interrupt_escalates_to_kill (__main__.TestNativeAudioSink.test_interrupt_escalates_to_kill) ... ok
     test_interrupt_when_idle_is_noop (__main__.TestNativeAudioSink.test_interrupt_when_idle_is_noop) ... ok
     test_missing_mpv_raises_mpv_not_installed (__main__.TestNativeAudioSink.test_missing_mpv_raises_mpv_not_installed) ... ok
     test_missing_wav_file_raises_filenotfound (__main__.TestNativeAudioSink.test_missing_wav_file_raises_filenotfound) ... ok
     test_play_non_zero_exit_raises_playback_error (__main__.TestNativeAudioSink.test_play_non_zero_exit_raises_playback_error) ... ok
     test_play_successful_invokes_mpv (__main__.TestNativeAudioSink.test_play_successful_invokes_mpv) ... ok
     test_play_timeout_raises_playback_error (__main__.TestNativeAudioSink.test_play_timeout_raises_playback_error) ... ok
     test_real_interrupt_smoke (__main__.TestNativeAudioSink.test_real_interrupt_smoke) ... ok
     test_real_playback_smoke (__main__.TestNativeAudioSink.test_real_playback_smoke) ... ok

     ----------------------------------------------------------------------
     Ran 12 tests in 0.557s

     OK
     ```
   - `.venv/bin/python tests/test_narrator_service.py`:
     ```
       ok  test_existing_pid_returns_none_when_pid_file_absent
       ok  test_existing_pid_returns_none_for_dead_pid
       ok  test_existing_pid_reclaims_live_non_daemon_pid
       ok  test_pid_is_our_daemon_true_for_matching_cmdline
       ok  test_pid_is_our_daemon_false_for_recycled_pid
       ok  test_pid_is_our_daemon_false_for_dead_pid
       ok  test_sweep_removes_old_session_markers_but_not_fresh_ones
       ok  test_sweep_tolerates_missing_dirs
       ok  test_process_chunk_filters_events_without_session_marker
       ok  test_process_chunk_suppresses_stop_event_flush
       ok  test_enqueue_under_cap_keeps_all
       ok  test_enqueue_over_cap_drops_oldest
       ok  test_enqueue_into_zero_free_slots_is_bounded
       ok  test_tail_resumes_a_line_split_across_two_reads
       ok  test_config_max_queue_depth_default_and_override
       ok  test_speak_invokes_synthesizer_and_audio_sink
       ok  test_speak_skips_unspeakable_content
       ok  test_speak_cleans_up_on_synthesis_fault
       ok  test_speak_cleans_up_on_playback_failure
       ok  test_process_chunk_user_prompt_submit_interrupts_audio_sink
       ok  test_tts_worker_recovers_from_item_error
     narrator_service: 21 tests passed
     ```
   - `.venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R1InProcessAudioSink`:
     ```
     ......
     ----------------------------------------------------------------------
     Ran 6 tests in 1.414s

     OK
     ```
   - `.venv/bin/ruff check plugin/scripts/python/native_audio_sink.py plugin/scripts/python/narrator_service.py tests/test_native_audio_sink.py tests/test_narrator_service.py`:
     ```
     All checks passed!
     ```

---

## 2. Logic Chain

1. **Integrity Verification**:
   - Scrutinized source code for dummy facades, test cheating, or hardcoded return values.
   - `NativeAudioSink` executes real subprocesses via Python standard library `subprocess.Popen` with verified CLI arguments. In-process integration tests verify actual execution and real system `mpv` smoke tests pass cleanly.
   - `NarratorService` genuinely wires `ResilientSynthesizer` and `NativeAudioSink` into the `_tts_worker` processing loop.
   - No integrity violations detected.

2. **Requirement R1 Conformance**:
   - `PROJECT.md` and `ORIGINAL_REQUEST.md` R1 require `NativeAudioSink` to play synchronously via `mpv --really-quiet --no-video --keep-open=no --idle=no <wav_path>`, blocking until playback finishes without detached sessions or `time.sleep()`. Observed implementation in `native_audio_sink.py:90-105` matches verbatim.
   - R1 requires instantiating `TTSEngine` directly in-process and eliminating `run_speak.sh`, `time.sleep()`, `wave.open`, and `SIGKILL`/`pkill`. Observed grep and inspection confirmed 100% removal from `narrator_service.py`.

3. **Concurrency & Thread Safety**:
   - Lock model analysis:
     - `NativeAudioSink.play()` acquires `_playback_lock` to serialize all playback calls sequentially across callers. Inside `_playback_lock`, it temporarily acquires `_state_lock` to register the active `Popen` object.
     - `NativeAudioSink.interrupt()` acquires only `_state_lock` to grab the running process handle and sets `_interrupted = True`, releasing `_state_lock` before signaling `proc.terminate()`.
     - Because `interrupt()` never acquires `_playback_lock`, lock acquisition is strictly hierarchical (`_playback_lock` -> `_state_lock` or `_state_lock` alone). Deadlock between playback and interrupt is impossible.
   - Stream Affinity: Apple MLX arrays and stream contexts are thread-local. `_ensure_tts_initialized()` is called on `_tts_worker` during worker startup and before synthesis, ensuring weights are loaded and evaluated within the worker thread.

4. **Robustness & Clean Cleanup**:
   - In `_speak()`, all temporary WAV files, `.partial` files, and split chunk files are cleaned up in `finally:`.
   - On playback failures or Kokoro generation faults, `_speak()` logs and catches exceptions, unlinking temp files and allowing `_tts_worker` to proceed to subsequent queued items without crashing the daemon.

---

## 3. Caveats & Coverage Gaps

1. **Non-M1 Regression in `test_narrator_phase_classifier.py`**:
   - Running `test_narrator_phase_classifier.py` fails on `test_category_change_closes_phase` because `PhaseClassifier` has a default of `max_events_per_phase=1` (modified in an earlier commit).
   - This test was NOT modified in M1 and is outside M1's assigned scope (`native_audio_sink.py`, `narrator_service.py`, `test_native_audio_sink.py`, `test_narrator_service.py`).
   - Risk level: **Low** for M1. Milestone M3/M4 or subsequent maintenance should align the unit test fixture with the classifier's configuration parameter.
2. **Subsequent Milestones**:
   - M2 will add the UNIX socket server to `narrator_service.py` and convert `speak.py` into a thin client.
   - M3 will remove obsolete files (`run_speak.sh`, `pipeline.py`, `short_path.py`, `mpv_controller.py`, `session_dir.py`).

---

## 4. Adversarial Challenge & Stress-Test Results

| Scenario | Attack / Stress Angle | Expected Behavior | Observed / Verified Behavior | Result |
|---|---|---|---|---|
| **Rapid Concurrency** | Multiple threads invoking `sink.play()` simultaneously | Sequential FIFO execution; no overlapping mpv processes | `test_concurrent_playback_serialized` verified `max_active == 1` | **PASS** |
| **Mid-Playback Interrupt** | `sink.interrupt()` called while `mpv` is actively streaming | Immediate process termination, `play()` returns cleanly without exception | `test_interrupt_active_playback` and `test_real_interrupt_smoke` verified <0.6s exit with `was_interrupted == True` | **PASS** |
| **Unresponsive mpv** | Process ignores `SIGTERM` | Escalates to `SIGKILL` after 0.5s timeout | `test_interrupt_escalates_to_kill` verified `terminate()` then `kill()` called | **PASS** |
| **Idle Interruption** | `sink.interrupt()` called with no active playback | Idempotent no-op; does not crash | `test_interrupt_when_idle_is_noop` passed cleanly | **PASS** |
| **Playback Faults** | Audio device error (non-zero exit code) or timeout | Raises `PlaybackError`, temp files unlinked | `test_play_non_zero_exit_raises_playback_error`, `test_play_timeout_raises_playback_error`, and `test_speak_cleans_up_on_playback_failure` passed | **PASS** |
| **Synthesis Fault** | `ResilientSynthesizer` throws `TTSGenerationError` | Exception caught, logged, temp files unlinked, worker continues | `test_speak_cleans_up_on_synthesis_fault` passed cleanly | **PASS** |
| **Missing mpv Binary** | Binary missing from system `PATH` | Cleanly raises `MpvNotInstalledError` | `test_missing_mpv_raises_mpv_not_installed` passed | **PASS** |

---

## 5. Conclusion

The Milestone M1 implementation meets all functional and architectural requirements specified in `PROJECT.md` and `ORIGINAL_REQUEST.md`:
- `NativeAudioSink` is fully implemented and tested with robust synchronization and clean process interrupt semantics.
- `narrator_service.py` is refactored for in-process `TTSEngine` with MLX single-thread stream affinity, eliminating external `run_speak.sh` script execution, duration guessing `time.sleep()`, `wave.open()`, `SessionDir`, and `pkill`/`SIGKILL`.
- Line 237 `_classifier` bug is resolved.
- 12/12 unit tests in `test_native_audio_sink.py`, 21/21 unit tests in `test_narrator_service.py`, and 6/6 E2E tests in `TestTier1R1InProcessAudioSink` pass.
- Ruff linter reports 0 errors.

**Verdict**: **APPROVE**

---

## 6. Verification Method

To independently reproduce and verify this review:

1. **NativeAudioSink Unit Tests**:
   ```bash
   .venv/bin/python tests/test_native_audio_sink.py
   ```
   *Expected outcome*: 12 tests pass, `OK`.

2. **NarratorService Unit Tests**:
   ```bash
   .venv/bin/python tests/test_narrator_service.py
   ```
   *Expected outcome*: 21 tests pass, `narrator_service: 21 tests passed`.

3. **Tier 1 R1 E2E Feature Tests**:
   ```bash
   .venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R1InProcessAudioSink
   ```
   *Expected outcome*: 6 tests pass, `OK`.

4. **Code Linter**:
   ```bash
   .venv/bin/ruff check plugin/scripts/python/native_audio_sink.py plugin/scripts/python/narrator_service.py tests/test_native_audio_sink.py tests/test_narrator_service.py
   ```
   *Expected outcome*: `All checks passed!`.
