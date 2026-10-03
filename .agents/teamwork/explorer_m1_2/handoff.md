# Handoff Report: In-Process TTSEngine and ResilientSynthesizer Integration for `narrator_service.py`

**Agent:** `explorer_m1_2`  
**Working Directory:** `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_2`  
**Handoff Type:** Hard (Milestone M1 investigation complete)  

---

## 1. Observation

### Obs 1. Legacy Process Sprawl & Duration Sleep Hacks in `narrator_service.py`
- **File:** `plugin/scripts/python/narrator_service.py` (643 lines)
- **External Sprawl Wrapper:** Line 109 defines `_speak_script()` resolving to `plugin/scripts/shell/run_speak.sh`.
- **Subprocess Interpreter Launch:** Line 530 calls:
  ```python
  proc = subprocess.run(
      [str(speak), "--keep-artifacts"],
      input=line.encode("utf-8"),
      capture_output=True,
      timeout=120,
      env=env,
  )
  ```
  This spawns a secondary shell and Python interpreter running `speak.py` for every single tool narration utterance.
- **Duration Guessing & Hard Sleep:** Lines 542–550 read the audio path from `SessionDir` and compute audio duration via `wave.open()`, followed by `time.sleep(duration + 0.5)`:
  ```python
  wav_path = SessionDir.wav_path_path().read_text().strip()
  with wave.open(wav_path, "r") as f:
      duration = f.getnframes() / float(f.getframerate())
  _log(f"sleeping for wav duration: {duration:.2f}s")
  time.sleep(duration + 0.5)  # slight buffer
  ```
- **SIGKILL & Idle Polling Hacks:**
  - Lines 552–557 read `SessionDir.read_pid()` and execute `os.kill(pid, _signal.SIGKILL)` immediately when the sleep timer expires.
  - Lines 562–597 define `_wait_mpv_idle()`, which loops polling `SessionDir.is_mpv_running()` and `MpvIpc.send(["get_property", "eof-reached"])`, falling back to `os.kill(pid, signal.SIGKILL)`.
  - Lines 318 and 334 execute global `subprocess.run(["pkill", "-9", "mpv"], capture_output=True)`.
- **Dead Duplicate Code:** Lines 317–331 and lines 334–349 contain identical duplicate blocks under `if event_type == "UserPromptSubmit":`. Because line 331 ends with `continue`, the entire second block (lines 333–350) is unreachable dead code.

### Obs 2. Apple MLX Single-Thread Stream Affinity
- **File:** `plugin/scripts/python/web_server.py:6` explicitly documents:
  > *"MLX detail: MLX state (compute streams) is per-thread. The TTSEngine must be loaded AND used from the same thread."*
- **File:** `plugin/scripts/python/tts_engine.py` (121 lines) defines `TTSEngine`, which lazy-loads the model via `_ensure_loaded()` (`from mlx_audio.tts.utils import load_model`) and synthesizes via `self._model.generate()`.
- **File:** `plugin/scripts/python/narrator_service.py` runs a dedicated background daemon thread for audio consumption:
  ```python
  tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
  ```
  Only `_tts_worker` consumes items from `self._tts_queue` and calls `self._speak()`.

### Obs 3. Fault-Tolerant Synthesis & Audio Sink Interfaces
- **File:** `plugin/scripts/python/resilient_synthesizer.py` (126 lines) wraps `TTSEngine`.
  - `synthesize_one(text, profile, out_path) -> bool`: Splits faulty spans via `SpanSplitter`, recovers speakable fragments, concatenates to `out_path` via `WavConcatenator`, unlinks intermediate fragments, and returns `True` if audio exists or `False` if the text has no pronounceable phonemes.
- **File:** `PROJECT.md:74-85` defines the `NativeAudioSink` contract:
  ```python
  class NativeAudioSink:
      def play(self, wav_path: Path) -> None:
          """Plays wav file synchronously via mpv, blocking until playback finishes."""
      def interrupt(self) -> None:
          """Terminates any currently active mpv playback process."""
  ```
- **File:** `plugin/scripts/python/voice_profile_store.py` (39 lines) reads and writes `VoiceProfile` JSON records. File `config/voice_calibration.json` exists in the repository with calibrated parameters for `af_nova` at speed 1.0 (throughput 15.84 chars/sec).

### Obs 4. Unit Test Crash at Line 237 of `narrator_service.py`
- Executing `.venv/bin/python tests/test_narrator_service.py` produces:
  ```
  Traceback (most recent call last):
    File ".../tests/test_narrator_service.py", line 306, in test_tail_resumes_a_line_split_across_two_reads
      svc._tail_events()
    File ".../plugin/scripts/python/narrator_service.py", line 237, in _tail_events
      if hasattr(self._classifier, "_current") and self._classifier._current:
                 ^^^^^^^^^^^^^^^^
  AttributeError: 'NarratorService' object has no attribute '_classifier'
  ```
- Line 237 directly accesses `self._classifier` without defensive attribute checking (`getattr(self, "_classifier", None)`), causing unit tests that construct `NarratorService` via `__new__` to crash.

---

## 2. Logic Chain

1. **Elimination of Process Sprawl via In-Process `TTSEngine` (from Obs 1, Obs 2):**
   - The legacy `_speak()` spent seconds launching an external shell script and secondary Python interpreter for every tool narration.
   - Instantiating `TTSEngine` directly in `narrator_service.py` removes `run_speak.sh` and eliminates inter-process translation entirely.
2. **Honoring Apple MLX Thread Affinity (from Obs 2):**
   - Because MLX compute streams are per-thread, loading the model in the main thread and synthesizing in `_tts_worker` causes Metal stream faults.
   - Instantiating `TTSEngine` and executing `_ensure_loaded()` directly on the `_tts_worker` thread guarantees that model loading and all subsequent `synthesize_one()` calls share the exact same thread context.
3. **Synchronous Hardware Ownership via `NativeAudioSink` (from Obs 1, Obs 3):**
   - The legacy architecture launched `mpv` in a detached background session and guessed playback duration with `wave.open()` + `time.sleep()`, followed by `SIGKILL`.
   - By delegating playback to `NativeAudioSink.play(temp_wav)`, `subprocess.run(["mpv", "--really-quiet", ...])` blocks naturally until the audio hardware finishes playing the file.
   - The thread wakes up immediately upon audio completion, making duration calculations, `time.sleep()`, `SessionDir`, `_wait_mpv_idle()`, and `SIGKILL` completely obsolete.
4. **Leak-Free Temporary File Lifecycle (from Obs 1, Obs 3):**
   - Synthesizing to a unique temporary file allocated via `tempfile.NamedTemporaryFile(prefix="narrator_", suffix=".wav", delete=False)` ensures isolated files per utterance.
   - Enclosing synthesis and playback in a `try...finally:` block ensures that the primary temporary WAV, any `.partial` files, and any split fragments generated by `ResilientSynthesizer` are unconditionally unlinked when playback finishes or faults.
5. **Worker Thread Resilience & Interruption (from Obs 1, Obs 3):**
   - Handling exceptions inside `_speak()` and `_tts_worker()` ensures that bad spans or audio errors log warnings rather than crashing the worker loop.
   - `NativeAudioSink` can be initialized in `NarratorService.__init__` so that `self._sink.interrupt()` can be cleanly invoked from the main thread on `UserPromptSubmit` (replacing `pkill -9 mpv`) and in signal handlers.

---

## 3. Caveats

1. **Missing `native_audio_sink.py` Implementation:**
   `native_audio_sink.py` is being developed concurrently by `explorer_m1_1`. The specification here conforms strictly to the interface contract defined in `PROJECT.md:74-85` (`play(wav_path: Path) -> None` and `interrupt() -> None`).
2. **First-Load Prewarm Duration:**
   When `_tts_worker` starts up, `_ensure_loaded()` loads the 82M Kokoro model into unified memory. On Apple Silicon M-series chips, this takes ~0.8–1.5 seconds. Because this occurs asynchronously in the background thread at daemon boot, it does not block the main tail loop.
3. **Hermetic Test Environments:**
   Real Kokoro model weights (~300MB bf16) should not be downloaded during CI unit tests. The implementation supports dependency injection in `NarratorService.__init__(*, sink=..., engine=..., synth=..., profile=...)` to allow instant mocking in hermetic test suites.

---

## 4. Conclusion

1. **Exact Code Changes Specified:**
   - Detailed in `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_2/analysis.md`.
   - Replaces `_speak()` with in-process `ResilientSynthesizer.synthesize_one()` + `NativeAudioSink.play()` + `finally:` file cleanup.
   - Implements `_ensure_tts_initialized()` to honor Apple MLX single-thread stream affinity on `_tts_worker`.
   - Implements `_load_profile_or_fallback()` using `config/voice_calibration.json` with configuration override support.
   - Removes `_speak_script()`, `_wait_mpv_idle()`, `SessionDir`, `wave.open`, `time.sleep`, and `SIGKILL`.
   - Replaces `pkill -9 mpv` with `self._sink.interrupt()`.
   - Deletes duplicate unreachable dead code block at lines 333–350.
2. **Line 237 Bug Fix:**
   - Replace `if hasattr(self._classifier, "_current") and self._classifier._current:` with `classifier = getattr(self, "_classifier", None)` and check `classifier is not None`.
3. **Execution Ready:**
   The implementer can apply the exact code blocks specified in `analysis.md` §8 without ambiguity.

---

## 5. Verification Method

### 1. Existing Test Suite Verification
Run the unit test for `narrator_service.py`:
```bash
.venv/bin/python tests/test_narrator_service.py
```
- **Prior state:** Failed at line 237 (`AttributeError: 'NarratorService' object has no attribute '_classifier'`).
- **Expected state:** 15/15 tests pass.

### 2. In-Process Synthesis & Playback Mock Unit Tests
Implement unit tests in `tests/test_narrator_service.py` validating:
1. `test_speak_synthesizes_and_plays`: Verifies that `_speak` calls `synthesize_one`, calls `sink.play`, and deletes temporary files.
2. `test_speak_skips_unspeakable_content`: Verifies that `synthesize_one returning False` does not trigger playback and cleans up files.
3. `test_speak_cleans_up_on_synthesis_fault`: Verifies that synthesis errors are logged, files are cleaned up, and no exception leaks.
4. `test_speak_cleans_up_on_playback_failure`: Verifies that playback exceptions are handled cleanly without crashing the caller.
5. `test_tts_worker_recovers_from_item_error`: Verifies that the worker thread handles errors and continues processing subsequent items.

### 3. File Inspection
Inspect `plugin/scripts/python/narrator_service.py`:
- Verify no remaining references to `_speak_script`, `run_speak.sh`, `time.sleep`, `wave.open`, `SessionDir`, or `SIGKILL`.
- Verify `ResilientSynthesizer`, `TTSEngine`, and `NativeAudioSink` imports and usages.
- Verify that `temp_wav.unlink(missing_ok=True)` and partial/fragment cleanup exist in a `finally:` block.

### Invalidation Conditions
- If Apple MLX allows multi-thread model sharing without stream faults (would allow earlier loading, but in-thread loading remains safer regardless).
- If `NativeAudioSink` alters its contract from `play(wav_path: Path) -> None`.
