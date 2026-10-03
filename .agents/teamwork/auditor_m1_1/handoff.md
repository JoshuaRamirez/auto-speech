# Forensic Audit Report: Milestone M1 (In-Process TTSEngine & NativeAudioSink)

**Work Product**: Milestone M1 Implementation  
- `plugin/scripts/python/native_audio_sink.py`
- `plugin/scripts/python/narrator_service.py`
- `tests/test_native_audio_sink.py`
- `tests/test_narrator_service.py`

**Profile**: General Project  
**Integrity Mode**: `development` (per `ORIGINAL_REQUEST.md` line 14)  
**Verdict**: **CLEAN**

---

### Phase Results

- **Static Analysis & Facade Detection**: **PASS**  
  Zero mock returns, zero hardcoded test outputs, zero dummy stubs found. `NativeAudioSink` authentically launches `mpv` via `subprocess.Popen` with all mandatory flags (`--really-quiet`, `--no-video`, `--keep-open=no`, `--idle=no`), blocks via `proc.communicate()`, and coordinates termination through `proc.terminate()` and `proc.kill()`.
- **Legacy Hack Elimination Check**: **PASS**  
  `narrator_service.py` has completely eliminated:
  - `run_speak.sh` (0 occurrences)
  - `_speak_script()` (0 occurrences)
  - `SessionDir` (0 occurrences)
  - `wave.open` duration calculation (0 occurrences)
  - `time.sleep` (0 occurrences in logic; only mentioned in a historical docstring)
  - `SIGKILL` / `pkill` (0 occurrences; replaced by `sink.interrupt()`)
  - `_wait_mpv_idle()` (0 occurrences)
  - Duplicate dead code on `UserPromptSubmit` (removed)
  - Line 237 `_classifier` bug (fixed with `getattr(self, "_classifier", None)`)
- **Empirical Subprocess & Runtime Verification**: **PASS**  
  Direct execution of `NativeAudioSink` against `/opt/homebrew/bin/mpv` confirmed real subprocess spawning, true synchronous blocking (0.520s elapsed for 0.2s WAV), sub-second interrupt capability (0.335s), and zero orphan mpv processes left running (`pgrep -x mpv` returned empty).
- **Test Suite Execution**: **PASS**  
  - `tests/test_native_audio_sink.py`: 12/12 passed (0.531s)
  - `tests/test_narrator_service.py`: 21/21 passed (0.428s)
  - `tests/e2e/test_tier1_features.py::TestTier1R1InProcessAudioSink`: 6/6 passed (1.427s)
  - `ruff check`: 0 errors across all 4 files
- **Adversarial Stress Testing**: **PASS**  
  - Concurrency: 4 concurrent caller threads across separate threads were strictly serialized by `_playback_lock` (total time 2.041s).
  - Corrupt audio file handling: cleanly raised `PlaybackError` with mpv exit code 2.
  - Resource leakage: temporary WAV files, `.partial` files, and fragment files generated during `_speak` are guaranteed unlinked in `finally:` blocks even under synthesis and playback exceptions.

---

## 1. Observation

### Exact File Paths & Code Line Inspections

1. **`plugin/scripts/python/native_audio_sink.py`**:
   - Lines 86–105: Real subprocess invocation with required flags and synchronous blocking:
     ```python
     with self._playback_lock:
         with self._state_lock:
             self._interrupted = False
             proc = subprocess.Popen(
                 [
                     mpv_bin,
                     "--really-quiet",
                     "--no-video",
                     "--keep-open=no",
                     "--idle=no",
                     str(wav_path),
                 ],
                 stdout=subprocess.DEVNULL,
                 stderr=subprocess.PIPE,
             )
             self._proc = proc

         try:
             stdout, stderr = proc.communicate(timeout=timeout)
             retcode = proc.returncode
     ```
   - Lines 129–153: Targeted, non-system-wide interrupt:
     ```python
     def interrupt(self) -> None:
         with self._state_lock:
             proc = self._proc
             if proc is None or proc.poll() is not None:
                 return
             self._interrupted = True

         try:
             proc.terminate()
             try:
                 proc.wait(timeout=self._TERMINATE_TIMEOUT)
             except subprocess.TimeoutExpired:
                 proc.kill()
                 try:
                     proc.wait(timeout=self._KILL_TIMEOUT)
                 except (subprocess.TimeoutExpired, ProcessLookupError):
                     pass
         except ProcessLookupError:
             pass
     ```

2. **`plugin/scripts/python/narrator_service.py`**:
   - Lines 501–527: Thread-affinity initialization on dedicated `_tts_worker` thread:
     ```python
     def _ensure_tts_initialized(self) -> None:
         if self._synth is not None and self._profile is not None:
             return
         try:
             if self._profile is None:
                 self._profile = _load_profile_or_fallback(self._config)
             if self._engine is None:
                 model_id = self._config.get("tts_model", "mlx-community/Kokoro-82M-bf16")
                 self._engine = TTSEngine(model_id=model_id)
             if self._synth is None:
                 self._synth = ResilientSynthesizer(self._engine, log=_log)
             _log(f"loading tts engine on worker thread (model={self._engine.model_id})...")
             self._engine._ensure_loaded()
             _log("tts engine ready on worker thread")
         except Exception as exc:
             _log(f"error initializing tts engine on worker thread: {exc!r}")
     ```
   - Lines 572–606: In-process synthesis, synchronous audio sink playback, and comprehensive temporary file cleanup:
     ```python
     def _speak(self, line: str) -> None:
         line = line.strip()
         if not line:
             return
         _log(f"speak: {line}")
         self._ensure_tts_initialized()
         if self._synth is None or self._sink is None or self._profile is None:
             _log(f"tts engine not available; dropped narration: {line[:50]!r}")
             return
         with tempfile.NamedTemporaryFile(prefix="narrator_", suffix=".wav", delete=False) as f:
             temp_wav = Path(f.name)
         try:
             has_audio = self._synth.synthesize_one(line, self._profile, temp_wav)
             if has_audio and temp_wav.exists() and temp_wav.stat().st_size > 0:
                 _log(f"playing audio ({temp_wav.stat().st_size} bytes)...")
                 self._sink.play(temp_wav)
             else:
                 _log(f"no speakable audio generated for: {line[:50]!r}")
         except Exception as exc:
             _log(f"speak error: {exc!r}")
         finally:
             temp_wav.unlink(missing_ok=True)
             temp_wav.with_suffix(temp_wav.suffix + ".partial").unlink(missing_ok=True)
             for frag in temp_wav.parent.glob(f"{temp_wav.stem}-*.wav"):
                 frag.unlink(missing_ok=True)
     ```
   - Lines 373–375: Non-destructive interruption on `UserPromptSubmit`:
     ```python
     # Immediately interrupt any currently playing audio so the user isn't talked over!
     if hasattr(self, "_sink") and self._sink is not None:
         self._sink.interrupt()
     ```
   - Line 292: Line 237 bug fix:
     ```python
     classifier = getattr(self, "_classifier", None)
     if classifier and hasattr(classifier, "_current") and classifier._current:
     ```

3. **Legacy Hack Elimination Search Results**:
   - `ripgrep "run_speak" plugin/scripts/python/narrator_service.py` → 0 matches
   - `ripgrep "_speak_script" plugin/scripts/python/narrator_service.py` → 0 matches
   - `ripgrep "SessionDir" plugin/scripts/python/narrator_service.py` → 0 matches
   - `ripgrep "wave" plugin/scripts/python/narrator_service.py` → 0 matches
   - `ripgrep "sleep" plugin/scripts/python/narrator_service.py` → 1 match (docstring on line 575 only)
   - `ripgrep "SIGKILL" plugin/scripts/python/narrator_service.py` → 0 matches
   - `ripgrep "pkill" plugin/scripts/python/narrator_service.py` → 0 matches

4. **Empirical Independent Execution Commands & Results**:
   - Unit tests:
     ```bash
     .venv/bin/python tests/test_native_audio_sink.py
     ```
     Result: `Ran 12 tests in 0.531s ... OK`
   - Narrator service tests:
     ```bash
     .venv/bin/python tests/test_narrator_service.py
     ```
     Result: `narrator_service: 21 tests passed`
   - Tier 1 E2E tests:
     ```bash
     .venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R1InProcessAudioSink
     ```
     Result: `Ran 6 tests in 1.427s ... OK`
   - Linter:
     ```bash
     .venv/bin/ruff check plugin/scripts/python/native_audio_sink.py plugin/scripts/python/narrator_service.py tests/test_native_audio_sink.py tests/test_narrator_service.py
     ```
     Result: `All checks passed!`
   - Live empirical verification of `NativeAudioSink` with real `/opt/homebrew/bin/mpv`:
     - 0.2s WAV playback: blocked for `0.520s` (PASSED)
     - 3.0s WAV playback interrupted after 0.2s: stopped in `0.335s`, `was_interrupted == True`, active mpv PIDs: `""` (PASSED)
     - Corrupt audio file: raised `PlaybackError: mpv exited with code 2` (PASSED)
     - 4 concurrent threads: serialized sequentially, total runtime `2.041s` (PASSED)
     - File cleanup in `_speak`: temporary `.wav`, `.partial`, and fragments verified removed under normal, synthesis crash, and playback crash scenarios (PASSED).

---

## 2. Logic Chain

1. **Authenticity of Implementation**:
   - Observation: `native_audio_sink.py` defines `NativeAudioSink` using `subprocess.Popen` with exact mpv arguments, `proc.communicate(timeout=timeout)`, and `_playback_lock`.
   - Inferences: The implementation does not bypass execution or return hardcoded mock responses. When invoked with real audio files, it spawns the genuine mpv binary, blocks synchronously until playback completes, and captures return codes and stderr.
   - Conclusion: Authentic implementation, zero facade.

2. **Absence of Legacy Hacks**:
   - Observation: Rigorous pattern matching for `run_speak.sh`, `_speak_script`, `SessionDir`, `wave.open`, `time.sleep`, `SIGKILL`, and `pkill` yielded zero occurrences in `narrator_service.py`.
   - Inferences: `narrator_service.py` no longer launches external shell scripts or background mpv sessions, no longer guesses playback duration via `wave.open` + `time.sleep`, and no longer executes host-wide process kills.
   - Conclusion: All legacy hacks specified in Milestone M1 are genuinely eliminated.

3. **Concurrency and State Management**:
   - Observation: Empirical multi-threading test with 4 threads calling `play()` simultaneously executed with maximum concurrent active processes equal to 1, finishing in 2.041s.
   - Inferences: `_playback_lock` serializes playback across callers, while `_state_lock` prevents race conditions during `interrupt()`.
   - Conclusion: Safe concurrent usage guaranteed.

4. **Resource Management**:
   - Observation: Audio file unlinking tests demonstrated that `temp_wav`, `temp_wav.with_suffix(".wav.partial")`, and `f"{temp_wav.stem}-*.wav"` fragments are all cleaned up in the `finally:` block of `_speak`.
   - Inferences: No temporary audio file accumulation will occur under normal operations or exceptional states.
   - Conclusion: Resource hygiene verified.

---

## 3. Caveats

- **Scope Boundary**: This audit exclusively covers Milestone M1 (`NativeAudioSink`, in-process `TTSEngine`, and cleanup of `narrator_service.py`). Milestones M2 (UNIX socket server on `/tmp/auto-speech-daemon.sock` and thin `speak.py` client) and M3 (deletion of legacy files `run_speak.sh`, `pipeline.py`, `short_path.py`, `mpv_controller.py`, `session_dir.py`) are scheduled for subsequent milestones per `PROJECT.md`.
- **Pre-existing Working Tree State**: Working tree modifications in `narrator_phase_classifier.py` and `config/` predate Milestone M1 and were untouched by `worker_m1`.

---

## 4. Conclusion

Milestone M1 satisfies all requirements set forth in `ORIGINAL_REQUEST.md §R1` and `PROJECT.md`:
1. `NativeAudioSink` is authentically implemented, robustly synchronized, and fully verified with live `mpv` execution.
2. `narrator_service.py` genuinely instantiates and invokes `TTSEngine` and `ResilientSynthesizer` in-process with thread affinity.
3. Legacy process sprawl, duration-sleep hacks, and indiscriminate kill commands have been completely removed.
4. Unit tests and Tier 1 R1 E2E tests pass 100% with zero linter errors.

**Verdict**: **CLEAN**.

---

## 5. Verification Method

To independently re-verify these audit conclusions:

1. **Run NativeAudioSink Unit Tests**:
   ```bash
   .venv/bin/python tests/test_native_audio_sink.py
   ```
   *Expected result*: `Ran 12 tests in ~0.5s ... OK`

2. **Run NarratorService Unit Tests**:
   ```bash
   .venv/bin/python tests/test_narrator_service.py
   ```
   *Expected result*: `narrator_service: 21 tests passed`

3. **Run Tier 1 R1 E2E Feature Tests**:
   ```bash
   .venv/bin/python -m unittest tests.e2e.test_tier1_features.TestTier1R1InProcessAudioSink
   ```
   *Expected result*: `Ran 6 tests in ~1.4s ... OK`

4. **Run Linter**:
   ```bash
   .venv/bin/ruff check plugin/scripts/python/native_audio_sink.py plugin/scripts/python/narrator_service.py tests/test_native_audio_sink.py tests/test_narrator_service.py
   ```
   *Expected result*: `All checks passed!`

5. **Verify Elimination of Legacy Hacks in `narrator_service.py`**:
   ```bash
   grep -E "(run_speak|_speak_script|SessionDir|time\.sleep|pkill|SIGKILL)" plugin/scripts/python/narrator_service.py
   ```
   *Expected result*: 0 matches.
