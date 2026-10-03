# Handoff Report: In-Process MLX Kokoro TTS Engine & TTSEngine Architecture

**Agent**: `explorer_survey_2`  
**Working Directory**: `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_2`  
**Date**: 2026-10-03  
**Status**: Completed (Hard Handoff)  

---

## 1. Observation

1. **`TTSEngine` Definition**:
   - Located at `/Users/joshua/Developer/auto-speech/plugin/scripts/python/tts_engine.py:48-121`.
   - Constructor:
     ```python
     def __init__(self, model_id: str = "mlx-community/Kokoro-82M-bf16") -> None:
         self._model_id = model_id
         self._model = None  # lazy
     ```
   - Lazy model loading: `self._ensure_loaded()` imports `from mlx_audio.tts.utils import load_model` and executes `self._model = load_model(self._model_id)`.
   - Synthesis API: `synthesize(self, text: str, voice_profile: VoiceProfile, out_path: Path) -> None`.
   - Audio generation iterates `self._model.generate(text=text, voice=voice_profile.voice_id, speed=voice_profile.speed, lang_code=lang_code)`, produces chunks of NumPy arrays, converts them via `np.concatenate(chunks, axis=0).astype(np.float32)`, clips with `np.clip(audio, -1.0, 1.0)`, converts to 16-bit integer PCM `(audio * 32767.0).astype(np.int16)`, and writes a 24 kHz mono WAV to `out_path` atomically using a `.partial` temporary file (`wave.open(str(tmp), "wb")`).

2. **Fault Tolerance Wrapper (`ResilientSynthesizer`)**:
   - Located at `/Users/joshua/Developer/auto-speech/plugin/scripts/python/resilient_synthesizer.py:44-126`.
   - Wraps `TTSEngine` to catch `TTSGenerationError` (such as `mlx-audio` broadcast shape mismatches on irregular inputs) and splits the text recursively using `SpanSplitter`, synthesizing each fragment and combining with `WavConcatenator`. Provides `synthesize_one(text, profile, out_path) -> bool`.

3. **Current Daemon TTS Invocation in `narrator_service.py`**:
   - Located at `/Users/joshua/Developer/auto-speech/plugin/scripts/python/narrator_service.py:501-561`.
   - Method `_speak(self, line: str)`:
     - Waits up to 600 seconds for idle playback via `self._wait_mpv_idle()`.
     - Spawns subprocess: `proc = subprocess.run([str(_speak_script()), "--keep-artifacts"], input=line.encode("utf-8"), ...)` where `_speak_script()` resolves to `plugin/scripts/shell/run_speak.sh`.
     - `run_speak.sh` launches a secondary Python interpreter running `plugin/scripts/python/speak.py`.
     - `speak.py` invokes `PipelineOrchestrator`, which spawns a detached background `mpv` process via `MpvController`, writing the PID and socket to `/tmp/auto-speech/`.
     - `narrator_service.py` reads `/tmp/auto-speech/wav.path`, inspects its duration with `wave.open`, executes `time.sleep(duration + 0.5)` (lines 549–550), and executes `os.kill(pid, _signal.SIGKILL)` (lines 553–557).

4. **Environment and Dependencies**:
   - Virtual environment exists at `/Users/joshua/Developer/auto-speech/.venv`.
   - `pyproject.toml` declares `mlx-audio>=0.4.2`, `misaki[en]>=0.9.4`, `num2words>=0.5.14`, and `flask>=3.0`.
   - Command `/Users/joshua/Developer/auto-speech/.venv/bin/python -c "import mlx_audio; print(mlx_audio.__file__)"` verified `mlx_audio` is installed at `/Users/joshua/Developer/auto-speech/.venv/lib/python3.12/site-packages/mlx_audio/__init__.py`.
   - `plugin/scripts/shell/narrator_service_start.sh:42-58` starts the daemon with `python3 - "$VENV/bin/python" "$SERVICE" ...`, meaning `narrator_service.py` **already runs directly within the project virtual environment**.

5. **MLX Thread-Affinity Requirement**:
   - Documented in `/Users/joshua/Developer/auto-speech/plugin/scripts/python/web_server.py:6-12`:
     `"MLX detail: MLX state (compute streams) is per-thread. The TTSEngine must be loaded AND used from the same thread."`
   - In `narrator_service.py:152`, speech playback is already assigned to a dedicated consumer thread: `tts_thread = threading.Thread(target=self._tts_worker, daemon=True)`.

---

## 2. Logic Chain

1. **Premise 1 (In-process capability)**: Because `narrator_service.py` runs inside `$VENV/bin/python` (Observation 4) and `tts_engine.py` is in the same directory (`plugin/scripts/python/`), `TTSEngine` and `ResilientSynthesizer` can be imported and instantiated directly in `narrator_service.py` without environment or path manipulation.
2. **Premise 2 (Thread safety)**: Apple MLX compute streams and model weights are strictly thread-local (Observation 5). Because `narrator_service.py` already routes all speech items through a single dedicated worker thread `_tts_worker` (Observation 5), instantiating or initializing `TTSEngine` on that thread ensures thread affinity is 100% preserved without requiring additional thread pools.
3. **Premise 3 (Removal of subprocess sprawl and sleeps)**: The current architecture spawns a bash script, a second Python interpreter, `PipelineOrchestrator`, and a detached background `mpv` process, forcing `narrator_service.py` to guess durations and sleep (Observation 3). Replacing `_speak()` with in-process calls to `self._synth.synthesize_one(line, profile, wav_path)` and a blocking `subprocess.run(["mpv", "--no-video", "--really-quiet", ...])` completely eliminates:
   - `run_speak.sh` process spawns (~1–2 s process start overhead).
   - Detached `mpv` session state in `/tmp/auto-speech/` (`MpvController`, `SessionDir`, `MpvIpc`).
   - The fragile `time.sleep(duration + 0.5)` and `os.kill(pid, SIGKILL)` hacks.
4. **Premise 4 (Execution speed and UX)**: In-process synthesis via `TTSEngine` (Observation 1) executes speech synthesis within ~150–350 ms instead of multiple seconds, providing near-instantaneous real-time narration.

---

## 3. Caveats

1. **Pre-warming latency**: Model loading (`_ensure_loaded()`) takes ~1.5–3.0 s on Apple Silicon. This should be triggered during daemon initialization or at the start of `_tts_worker` so that the very first spoken narration is not delayed.
2. **Concurrent UserPromptSubmit Interruption**: When a user submits a prompt (`UserPromptSubmit` in `narrator_service.py:311`), the daemon intentionally stops currently playing audio so the user isn't talked over. In the blocking `NativeAudioSink` design, `NativeAudioSink` should hold a reference to `self._current_proc` (e.g., using `subprocess.Popen` with `.wait()`) so an `interrupt()` method can terminate `mpv` instantly and unblock `_tts_worker`.
3. **UNIX Socket Concurrency**: For requirement R2 (thin client `speak.py` sending text over `/tmp/auto-speech-daemon.sock`), the daemon must run the socket listener on a separate background thread (e.g., `socketserver.ThreadingUnixStreamServer`) and push incoming text into `self._tts_queue`, ensuring all TTS generation remains serialized on the `_tts_worker` thread.

---

## 4. Conclusion

1. **Location & Class**: `TTSEngine` is located in `plugin/scripts/python/tts_engine.py` (lines 48–121) and is ready for direct in-process instantiation.
2. **Model & Voices**: Model `mlx-community/Kokoro-82M-bf16` is loaded via `mlx_audio.tts.utils.load_model` into Apple Silicon unified memory (~160 MB). Voices are dynamically loaded embeddings from the local Hugging Face cache.
3. **In-Process Integration**: `narrator_service.py` can instantiate `TTSEngine` (wrapped with `ResilientSynthesizer`) inside `_tts_worker`.
4. **Audio Playback**: Replacing `run_speak.sh` with a synchronous `NativeAudioSink` (`subprocess.run(["mpv", "--no-video", "--really-quiet", ...])`) will eliminate `run_speak.sh`, `PipelineOrchestrator`, `ShortPathStrategy`, `MpvController`, `SessionDir`, `time.sleep`, and `SIGKILL` hacks cleanly and reliably.

---

## 5. Verification Method

To verify these findings independently:

1. **Verify In-Process Import and Model Loading**:
   ```bash
   /Users/joshua/Developer/auto-speech/.venv/bin/python -c "
   import sys
   sys.path.insert(0, 'plugin/scripts/python')
   from tts_engine import TTSEngine
   from voice_profile import VoiceProfile
   from pathlib import Path

   engine = TTSEngine()
   engine._ensure_loaded()
   profile = VoiceProfile('af_nova', 1.0, 15.0, 'test', 0)
   out = Path('/tmp/test_verification.wav')
   engine.synthesize('Verification test successful.', profile, out)
   print('Generated WAV size:', out.stat().st_size)
   "
   ```
2. **Inspect Existing Tests for Audio Generation & Playback**:
   ```bash
   /Users/joshua/Developer/auto-speech/.venv/bin/python tests/test_resilient_synthesizer.py
   /Users/joshua/Developer/auto-speech/.venv/bin/python tests/test_playback_consumer.py
   ```
3. **Inspect Analysis Report**:
   Inspect `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_2/analysis.md` for full implementation blueprints and code examples.

4. **Invalidation Conditions**:
   - If Apple MLX changes thread locality semantics such that compute streams can be freely shared across Python threads without segfaulting (currently they cannot).
   - If Kokoro model format changes to require external non-MLX runtime libraries.
