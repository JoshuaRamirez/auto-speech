# Handoff Report: Milestone M1 (In-Process TTSEngine & NativeAudioSink)

**Worker**: `worker_m1`  
**Working Directory**: `/Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m1`  
**Milestone**: M1 (In-Process TTSEngine & NativeAudioSink)  
**Date**: 2026-10-03  

---

## 1. Observation

1. **Initial State & Regressions**:
   - `tests/test_narrator_service.py` failed at line 306 due to `AttributeError: 'NarratorService' object has no attribute '_classifier'` at `plugin/scripts/python/narrator_service.py:237`:
     ```
     AttributeError: 'NarratorService' object has no attribute '_classifier'
     ```
   - `narrator_service.py` contained legacy process sprawl and hacks:
     - `_speak_script()` referencing `plugin/scripts/shell/run_speak.sh` (line 109).
     - Subprocess execution of `run_speak.sh` via `subprocess.run([str(speak), "--keep-artifacts"])` (line 530).
     - Reading `/tmp/auto-speech/wav.path` via `SessionDir.wav_path_path()` (line 545).
     - Audio duration calculation via `wave.open()` and `time.sleep(duration + 0.5)` (lines 546–550).
     - Global and PID kills via `subprocess.run(["pkill", "-9", "mpv"])` (lines 318, 334) and `os.kill(pid, _signal.SIGKILL)` (line 555).
     - Polling wait loop `_wait_mpv_idle()` with `SessionDir` and `MpvIpc` (lines 562–597).
     - Duplicate unreachable dead code block on `UserPromptSubmit` (lines 333–350).
   - `plugin/scripts/python/native_audio_sink.py` and `tests/test_native_audio_sink.py` did not exist.
   - `tests/e2e/test_tier1_features.py` had R1 failures due to missing `native_audio_sink.py` and un-migrated `narrator_service.py`.

2. **Executed Commands & Output**:
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
     Ran 12 tests in 0.637s

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
     Ran 6 tests in 1.522s

     OK
     ```
   - `.venv/bin/ruff check plugin/scripts/python/native_audio_sink.py plugin/scripts/python/narrator_service.py tests/test_native_audio_sink.py tests/test_narrator_service.py`:
     ```
     All checks passed!
     ```

---

## 2. Logic Chain

1. **NativeAudioSink Implementation**:
   - `NativeAudioSink` was constructed with a dual-lock synchronization model: `_playback_lock` serializes playback sequentially across threads (guaranteeing FIFO playback without audio collisions), while `_state_lock` guards `self._proc` inspection.
   - `play()` invokes `subprocess.Popen([mpv_bin, "--really-quiet", "--no-video", "--keep-open=no", "--idle=no", str(wav_path)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)` followed by `proc.communicate()`. This blocks the thread synchronously until mpv completes playback.
   - `interrupt()` accesses `_state_lock`, captures `proc`, and sends `proc.terminate()` (SIGTERM), unblocking `proc.communicate()` in milliseconds. If the process does not terminate within 0.5s, it escalates to `proc.kill()` (SIGKILL).
   - If playback was interrupted, `play()` returns cleanly (`None`) without raising an exception, preventing spurious errors on normal user turn interruptions.

2. **NarratorService Refactoring & MLX Thread Affinity**:
   - In `NarratorService.__init__`, `self._sink = sink or NativeAudioSink()` is instantiated, while `_engine`, `_synth`, and `_profile` are optional parameters (allowing test mocks) default-initialized to `None`.
   - Apple MLX requires that model loading and array execution happen on the same thread due to thread-local compute streams. In `_ensure_tts_initialized()`, `TTSEngine`, `ResilientSynthesizer`, and `VoiceProfile` are initialized and loaded on the dedicated `_tts_worker` thread.
   - In `_speak()`, text is synthesized directly in-process via `self._synth.synthesize_one(line, self._profile, temp_wav)`. If valid audio is generated, `self._sink.play(temp_wav)` blocks until playback completes.
   - In a `finally:` block, `temp_wav`, any `.partial` files, and split fragments are unlinked, guaranteeing zero file leakage.

3. **Removal of Legacy Hacks & Bug Fix**:
   - Line 237 bug: `hasattr(self._classifier, "_current")` was evaluated before ensuring `self` possessed `_classifier`. Replaced with `classifier = getattr(self, "_classifier", None)` and checked `current.events`, making `_tail_events()` robust even when constructed via `__new__`.
   - On `UserPromptSubmit`, replaced `subprocess.run(["pkill", "-9", "mpv"])` with `self._sink.interrupt()`, eliminating indiscriminate system-wide process kills. Deleted the redundant duplicate code block.
   - Deleted `_speak_script()`, `SessionDir`, `wave.open` duration calculation, `time.sleep()`, `SIGKILL`, and `_wait_mpv_idle()`.

4. **Verification**:
   - 12/12 unit tests in `tests/test_native_audio_sink.py` pass.
   - 21/21 unit tests in `tests/test_narrator_service.py` pass (15 original tests + 6 new in-process tests).
   - 6/6 R1 E2E tests in `tests/e2e/test_tier1_features.py` pass.
   - 0 lint violations across all modified files.

---

## 3. Caveats

- Milestone M1 specifically addresses R1 (In-Process TTSEngine and NativeAudioSink). Milestones M2 (daemon UNIX domain socket server and thin `speak.py` client) and M3 (deleting obsolete files `run_speak.sh`, `pipeline.py`, `short_path.py`, `mpv_controller.py`, `session_dir.py`) are assigned to subsequent milestones per `PROJECT.md` and `DISPATCH.md`.

---

## 4. Conclusion

Milestone M1 is complete:
1. `plugin/scripts/python/native_audio_sink.py` is implemented and verified.
2. `plugin/scripts/python/narrator_service.py` is fully refactored for in-process `TTSEngine` and synchronous `NativeAudioSink`, with line 237 fixed and all playback sleep/kill hacks eliminated.
3. Unit test suites in `tests/test_native_audio_sink.py` and `tests/test_narrator_service.py` pass 100%.
4. Tier 1 R1 E2E tests pass 100%.

---

## 5. Verification Method

To independently verify the implementation:

1. **Run NativeAudioSink Unit Tests**:
   ```bash
   .venv/bin/python tests/test_native_audio_sink.py
   ```
   *Expected outcome*: 12 tests pass, `OK`.

2. **Run NarratorService Unit Tests**:
   ```bash
   .venv/bin/python tests/test_narrator_service.py
   ```
   *Expected outcome*: 21 tests pass, exit code 0.

3. **Run Tier 1 R1 E2E Feature Tests**:
   ```bash
   .venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R1InProcessAudioSink
   ```
   *Expected outcome*: 6 tests pass, `OK`.

4. **Run Linter**:
   ```bash
   .venv/bin/ruff check plugin/scripts/python/native_audio_sink.py plugin/scripts/python/narrator_service.py tests/test_native_audio_sink.py tests/test_narrator_service.py
   ```
   *Expected outcome*: `All checks passed!`.
