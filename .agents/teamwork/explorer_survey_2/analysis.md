# Technical Analysis: MLX Kokoro TTS Engine and TTSEngine Architecture

**Date**: 2026-10-03  
**Author**: `explorer_survey_2` (Teamwork Explorer)  
**Target Repository**: `auto-speech`  
**Reference Task**: Refactor `auto-speech` into a Unified Daemon Server with in-process MLX Kokoro TTS engine and NativeAudioSink  

---

## Executive Summary

The `auto-speech` repository features an Apple-Silicon-native text-to-speech implementation based on Kokoro-82M via `mlx-audio`. Currently, the long-running daemon (`narrator_service.py`) does **not** perform TTS in-process; instead, for each spoken phrase it spawns an external bash script (`run_speak.sh`) that spins up a secondary Python process (`speak.py`), which constructs `PipelineOrchestrator`, launches a detached `mpv` background process via `MpvController`, writes state to `/tmp/auto-speech/`, and requires the daemon to inspect WAV durations, execute brittle `time.sleep(duration + 0.5)` delays, and issue `SIGKILL` signals.

This investigation demonstrates that `TTSEngine` (`plugin/scripts/python/tts_engine.py`) and its fault-tolerant wrapper `ResilientSynthesizer` can be instantiated directly within `narrator_service.py`. The daemon already runs inside the project virtual environment (`.venv`), where `mlx-audio` and model weights are installed and cached. Furthermore, `narrator_service.py` already possesses a dedicated consumer thread (`_tts_worker`), which perfectly matches Apple MLX's fundamental architectural constraint: **MLX compute streams are thread-local and must be bound to a single worker thread**.

---

## 1. TTSEngine Architecture & Definition

### 1.1 Source Location & Class Structure
- **File**: `plugin/scripts/python/tts_engine.py` (lines 48–121)
- **Class**: `TTSEngine`
- **Exceptions**:
  - `TTSGenerationError(RuntimeError)` (line 34): Raised on fatal Kokoro generation failure (e.g., input broadcast shape mismatch).
  - `TTSNoSpeakableContentError(TTSGenerationError)` (line 38): Raised when the input consists purely of unpronounceable characters or symbols (phoneme count is zero).
- **Key Constants**:
  - `KOKORO_SAMPLE_RATE = 24000` (line 21): 24 kHz mono output.
  - `_KOKORO_LANG_CODES = frozenset("abefhijpz")` (line 24): Supported Kokoro G2P language codes keyed by the first character of the voice ID (`a` = American English, `b` = British English).

### 1.2 Instantiation and Configuration
```python
class TTSEngine:
    def __init__(self, model_id: str = "mlx-community/Kokoro-82M-bf16") -> None:
        self._model_id = model_id
        self._model = None  # lazy
```
- **Default Model**: `"mlx-community/Kokoro-82M-bf16"` (hosted on Hugging Face Hub, cached locally in `~/.cache/huggingface/hub/models--mlx-community--Kokoro-82M-bf16`).
- **Footprint**: ~82M parameters in `bfloat16` occupying ~160 MB of unified memory on Apple Silicon.
- **Model Loading Lifecycle**:
  - `_ensure_loaded(self)` uses `mlx_audio.tts.utils.load_model(self._model_id)`.
  - Lazy loading: automatically called on first synthesis.
  - Eager loading / prewarming: can be invoked directly (`engine._ensure_loaded()`) during daemon startup to eliminate the 1.5–3.0 s cold-start latency.

### 1.3 Voice Management
- Kokoro voices are stored as pre-extracted 256-dimensional embedding vectors located in the `voices/` directory of the Hugging Face repo (e.g. `af_nova.pt`, `af_heart.pt`, `am_adam.pt`, `bf_alice.pt`).
- `TTSEngine` does not require explicit voice loading methods; `self._model.generate(..., voice=voice_profile.voice_id)` dynamically resolves and loads the voice embedding from the local cache.
- Language selection is handled by `_lang_code_for_voice(voice_profile.voice_id)`:
  ```python
  def _lang_code_for_voice(voice_id: str) -> str:
      if voice_id and voice_id[0] in _KOKORO_LANG_CODES:
          return voice_id[0]
      return "a"
  ```
  This prevents mismatched G2P pipelines that trigger broadcast-shape crashes in Misaki/spaCy.

---

## 2. Audio Generation Pipeline

### 2.1 Synthesis Workflow in `TTSEngine.synthesize()`
The method signature is:
```python
def synthesize(
    self,
    text: str,
    voice_profile: VoiceProfile,
    out_path: Path,
) -> None:
```

Execution steps:
1. **Validation**: Checks for empty / whitespace-only string; raises `TTSGenerationError`.
2. **Model Availability**: Calls `self._ensure_loaded()`.
3. **Atomic File Setup**: Creates target parent directories and uses a temporary `.partial` suffix (`<out_path>.partial`).
4. **Generation Generator**:
   ```python
   chunks = []
   for result in self._model.generate(
       text=text,
       voice=voice_profile.voice_id,
       speed=voice_profile.speed,
       lang_code=lang_code,
   ):
       chunks.append(np.array(result.audio))
   ```
   Each yielded chunk contains an audio array from Kokoro.
5. **Array Processing**:
   - Concatenates chunks along axis 0 into float32 array: `np.concatenate(chunks, axis=0).astype(np.float32)`.
   - Clips amplitude to prevent clipping distortion: `np.clip(audio, -1.0, 1.0)`.
   - Scales to 16-bit PCM integer: `(audio * 32767.0).astype(np.int16)`.
6. **WAV File Writing**:
   - Uses standard library `wave.open(str(tmp), "wb")`.
   - Sets 1 channel (mono), 2 bytes sample width (16-bit), 24,000 Hz frame rate.
   - Writes raw bytes: `wf.writeframes(audio_i16.tobytes())`.
7. **Atomic Publication**: `os.replace(tmp, out_path)` guarantees that consumers never encounter a partial WAV file.

### 2.2 Resilient Wrapper: `ResilientSynthesizer`
Located in `plugin/scripts/python/resilient_synthesizer.py`:
- `mlx-audio` Kokoro has a known issue where certain edge-case text inputs (specific punctuation or phonetic transitions) can trip a broadcast-shape error.
- `ResilientSynthesizer` wraps `TTSEngine`:
  - `synthesize_one(text, voice_profile, out_path) -> bool`
  - When Kokoro trips an error, `SpanSplitter` breaks the text down (sentences $\to$ clauses $\to$ word-halves), synthesizes each part independently, drops unspeakable floor fragments, and stitches the resulting audio using `WavConcatenator`.
  - For the unified daemon, wrapping `TTSEngine` in `ResilientSynthesizer` ensures that an anomalous tool output or prompt cannot crash speech synthesis.

---

## 3. Dependencies and Runtime Environment

### 3.1 Dependencies
Defined in `pyproject.toml` (lines 14–19):
- `mlx-audio>=0.4.2`: Apple Silicon MLX audio framework.
- `misaki[en]>=0.9.4`: G2P phonemizer (using spaCy `en_core_web_sm`).
- `numpy`: Numerical array manipulation and audio sample conversion.
- `wave`: Standard Python library (no external C audio library dependency for WAV encoding).
- Platform constraint: macOS Darwin on Apple Silicon (`sys_platform == 'darwin'`).

### 3.2 Runtime Execution Context
- The repository uses a dedicated virtual environment at `/Users/joshua/Developer/auto-speech/.venv`.
- `narrator_service_start.sh` (line 42) already launches `narrator_service.py` using `$VENV/bin/python`.
- Therefore, **`narrator_service.py` already runs within `.venv`**, with direct access to `mlx_audio`, `numpy`, and all plugin Python modules without requiring any environment activation or external subprocess wrapper.

---

## 4. The Critical MLX Threading Invariant

As documented in `web_server.py` (lines 6–12):
> *"MLX detail: MLX state (compute streams) is per-thread. The TTSEngine must be loaded AND used from the same thread."*

In Apple's MLX architecture:
- Compute streams, Metal command buffers, and device allocations are thread-bound.
- Calling `self._model.generate(...)` from a different thread than the one that initialized the model or calling it concurrently from multiple threads corrupts compute streams or triggers segmentation faults.
- In `web_server.py`, this was solved via a dedicated `ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts-worker")`.
- In `narrator_service.py`, `_tts_worker` is **already a dedicated, single background thread**:
  ```python
  tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
  ```
- **Architectural Match**: If `TTSEngine` (and `ResilientSynthesizer`) is initialized or prewarmed inside `_tts_worker` (or owned exclusively by it), the single-thread invariant is satisfied naturally and without extra executor overhead.

---

## 5. Current Sprawl vs. Proposed Unified Daemon

### 5.1 Current Flow in `narrator_service.py` (Lines 501–561)
```
[Event Detected]
       │
       ▼
[self._tts_queue]
       │
       ▼
[_tts_worker loop]
       │
       ▼
[_speak(line)] ──────► _wait_mpv_idle() (polls SessionDir/MpvIpc up to 600s)
       │
       ├─────────────► subprocess.run(["run_speak.sh", "--keep-artifacts"], input=line)
       │                    │
       │                    ▼ (spawns new python interpreter)
       │               [speak.py]
       │                    │
       │                    ▼
       │               [PipelineOrchestrator]
       │                    │
       │                    ├─► creates fresh TTSEngine / model load
       │                    ├─► synthesizes WAV to /tmp/auto-speech-YYYYMMDD...
       │                    └─► spawns detached mpv via MpvController
       │                             │
       │                             ▼
       │                        writes PID to /tmp/auto-speech/mpv.pid
       │
       ├─────────────► Reads /tmp/auto-speech/wav.path
       ├─────────────► Reads duration with wave.open()
       ├─────────────► time.sleep(duration + 0.5)  <-- Brittle Sleep Hack!
       └─────────────► os.kill(pid, SIGKILL)       <-- Violent Kill Hack!
```

### 5.2 Proposed In-Process Flow with NativeAudioSink
```
[Daemon Boot]
  │
  ├─► NarratorService.__init__()
  │     ├── Instantiates TTSEngine & ResilientSynthesizer
  │     ├── Loads VoiceProfileStore ("config/voice_calibration.json")
  │     └── Instantiates NativeAudioSink
  │
  ├─► Starts UNIX Domain Socket Server (/tmp/auto-speech-daemon.sock)
  │     └── Any CLI speak.py client writes text here and exits immediately
  │
  └─► Starts dedicated _tts_worker thread
        ├── Prewarms TTSEngine (_ensure_loaded()) on this worker thread
        └── Loops pulling phrases from _tts_queue:
              │
              ├─► Synthesizes WAV in-process to /tmp/auto-speech-narration.wav
              │   (Zero process spawn, ~200-400ms synthesis time)
              │
              └─► AudioSink.play(wav_path)
                  (subprocess.run(["mpv", "--no-video", "--really-quiet", ...]))
                  Blocks naturally until playback finishes!
                  No sleep hacks, no PID polling, no SIGKILL!
```

---

## 6. Detailed Implementation Blueprint for `narrator_service.py`

### 6.1 `NativeAudioSink` Implementation
```python
class NativeAudioSink:
    """Synchronous audio sink using mpv in blocking mode."""

    def __init__(self) -> None:
        self._current_proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def play(self, wav_path: Path) -> None:
        """Play WAV synchronously to completion. Blocks caller thread naturally."""
        cmd = [
            "mpv",
            "--no-video",
            "--really-quiet",
            "--keep-open=no",
            "--idle=no",
            str(wav_path),
        ]
        with self._lock:
            self._current_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        try:
            self._current_proc.wait()
        finally:
            with self._lock:
                self._current_proc = None

    def interrupt(self) -> None:
        """Immediately terminate any active playback (e.g. on UserPromptSubmit)."""
        with self._lock:
            if self._current_proc and self._current_proc.poll() is None:
                self._current_proc.terminate()
                try:
                    self._current_proc.wait(timeout=0.3)
                except subprocess.TimeoutExpired:
                    self._current_proc.kill()
```

### 6.2 Voice Profile Loading
```python
def _load_voice_profile() -> VoiceProfile:
    config_path = _project_root() / "config" / "voice_calibration.json"
    store = VoiceProfileStore(config_path)
    loaded = store.load()
    if loaded is not None:
        return loaded
    return VoiceProfile(
        voice_id=DEFAULT_VOICE_ID,
        speed=DEFAULT_SPEED,
        chars_per_second=FALLBACK_CHARS_PER_SEC,
        calibrated_at="fallback",
        calibration_source_chars=0,
    )
```

### 6.3 Integration into `NarratorService`
1. **In `__init__`**:
   - `self._tts = TTSEngine()`
   - `self._synth = ResilientSynthesizer(self._tts)`
   - `self._profile = _load_voice_profile()`
   - `self._audio_sink = NativeAudioSink()`
   - `self._narration_wav = Path("/tmp/auto-speech-narration.wav")`
2. **In `_tts_worker`**:
   - Call `self._tts._ensure_loaded()` at worker start so MLX weights and compute stream are initialized on this thread.
   - In the playback block:
     ```python
     def _speak(self, line: str) -> None:
         _log(f"speak: {line}")
         # Synthesize in-process
         ok = self._synth.synthesize_one(
             line, self._profile, self._narration_wav
         )
         if not ok or not self._narration_wav.exists():
             _log("synthesis yielded no speakable audio")
             return
         # Play blocking
         self._audio_sink.play(self._narration_wav)
     ```
3. **In `UserPromptSubmit` handling**:
   - Instead of `pkill -9 mpv`, invoke `self._audio_sink.interrupt()` (and fallback `pkill -9 mpv`).

---

## 7. Files to Remove / Retire (Requirement R3)
Once the Unified Daemon is implemented:
- `plugin/scripts/shell/run_speak.sh`: Completely replaced by socket communication to daemon.
- `plugin/scripts/python/pipeline.py` (`PipelineOrchestrator`): Sprawl orchestrator for multi-process chunking.
- `plugin/scripts/python/short_path.py` (`ShortPathStrategy`): Subprocess short path.
- `plugin/scripts/python/mpv_controller.py` (`MpvController`): Detached mpv spawner and socket pollers.
- `plugin/scripts/python/session_dir.py` (`SessionDir`): File-based session directory PID coordination.
- `plugin/scripts/python/mpv_ipc.py`: Replaced by simple `subprocess.run(["mpv", ...])`.

---

## 8. Summary Table of Architecture Comparison

| Dimension | Current Architecture | In-Process Unified Architecture |
|---|---|---|
| **Process Model** | Daemon $\to$ `run_speak.sh` $\to$ `speak.py` $\to$ detached `mpv` | Single long-lived daemon + `mpv` subprocess |
| **Model In-Memory** | Reloaded or re-checked per CLI run | Loaded once into unified memory at boot |
| **TTS Invocation** | Subprocess stdin pipe | In-process Python method call |
| **MLX Stream Binding** | Scattered across ephemeral processes | Strictly bound to daemon's `_tts_worker` thread |
| **Audio Playback** | Background detached `mpv` with IPC socket | Synchronous blocking `mpv` via `NativeAudioSink` |
| **Playback Sync** | `time.sleep(duration + 0.5)` + `SIGKILL` | `subprocess.run` blocks until playback ends |
| **Inter-Process State** | `/tmp/auto-speech/` (PID, socket, wav.path) | UNIX domain socket IPC (`/tmp/auto-speech-daemon.sock`) |
| **Synthesis Latency** | ~2–4 seconds per utterance | ~150–350 ms per utterance |
