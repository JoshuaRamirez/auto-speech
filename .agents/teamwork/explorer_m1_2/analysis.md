# In-Process TTSEngine and ResilientSynthesizer Integration for `narrator_service.py`

**Milestone:** M1 (In-Process TTSEngine & NativeAudioSink)  
**Agent:** `explorer_m1_2`  
**Working Directory:** `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_2`  
**Target File:** `plugin/scripts/python/narrator_service.py`  

---

## 1. Executive Summary & Objective

In the legacy architecture, `NarratorService` in `plugin/scripts/python/narrator_service.py` dispatched speech synthesis and playback by invoking an external bash script (`plugin/scripts/shell/run_speak.sh`) via `subprocess.run()`. This shell script spun up a secondary Python interpreter executing `speak.py`, which in turn invoked `PipelineOrchestrator` and `MpvController`. `MpvController` launched `mpv` as a detached background session and wrote state into `/tmp/auto-speech/` (`wav.path`, `mpv.pid`). Back in `narrator_service.py`, `_speak()` read the WAV file, computed duration with `wave.open()`, slept for `time.sleep(duration + 0.5)`, and then forcefully terminated `mpv` with `SIGKILL`.

This design introduced severe latency (cold Python startup per tool narration), race conditions, audio cutoffs, zombie processes, and fragility.

The goal of this investigation is to specify the **exact in-process integration** of `TTSEngine` and `ResilientSynthesizer` into `narrator_service.py` on the `_tts_worker` thread, replacing `_speak_script()` and external process sprawl with direct synthesis and synchronous `NativeAudioSink` playback.

---

## 2. Component Inventory & Collaborator Contracts

### 2.1 `TTSEngine` (`plugin/scripts/python/tts_engine.py`)
- **Role:** Kokoro-82M TTS adapter using `mlx-audio`.
- **Initialization:** `TTSEngine(model_id="mlx-community/Kokoro-82M-bf16")`.
- **Model Loading:** Lazy via `self._ensure_loaded()`. Imports `from mlx_audio.tts.utils import load_model` and loads model into memory.
- **Synthesis Contract:**
  ```python
  def synthesize(self, text: str, voice_profile: VoiceProfile, out_path: Path) -> None:
  ```
  - Writes audio to `out_path.with_suffix(out_path.suffix + ".partial")` and atomically renames (`os.replace`) to `out_path` on success.
  - Raises `TTSGenerationError` on synthesis failure.
  - Raises `TTSNoSpeakableContentError(TTSGenerationError)` when the text contains zero speakable phonemes (e.g. pure punctuation, ASCII art).

### 2.2 `ResilientSynthesizer` (`plugin/scripts/python/resilient_synthesizer.py`)
- **Role:** Fault-tolerant wrapper around `TTSEngine` that protects against Kokoro shape/broadcast faults.
- **Initialization:** `ResilientSynthesizer(engine: TTSEngine, max_depth: int = 4, splitter: SpanSplitter | None = None, log = None)`.
- **Key Method:**
  ```python
  def synthesize_one(self, text: str, profile: VoiceProfile, out_path: Path) -> bool:
  ```
  - Returns `True` when `out_path` holds valid audio.
  - Returns `False` when text contains no speakable content (no file is written).
  - On Kokoro generation faults, splits text recursively using `SpanSplitter`, synthesizes speakable sub-spans, and concatenates them with `WavConcatenator.concat(parts, out_path)`, unlinking intermediate split fragments (`f"{out_path.stem}-{depth}_{i}.wav"`).
  - Can accept `log=_log` to funnel splitting logs into narrator's rotating log file.

### 2.3 `VoiceProfile` & `VoiceProfileStore` (`plugin/scripts/python/voice_profile*.py`)
- **Role:** Holds calibrated voice ID, speed, and characters-per-second throughput metrics.
- **Default Storage Location:** `_project_root() / "config" / "voice_calibration.json"`.
- **Existing Calibration in Repo:**
  ```json
  {
    "voice_id": "af_nova",
    "speed": 1.0,
    "chars_per_second": 15.84,
    "calibrated_at": "2026-10-03T06:17:00Z",
    "calibration_source_chars": 600
  }
  ```
- **Fallback Profile:** If `voice_calibration.json` is missing:
  - `voice_id = DEFAULT_VOICE_ID` ("af_nova")
  - `speed = DEFAULT_SPEED` (1.18 or config-specified)
  - `chars_per_second = FALLBACK_CHARS_PER_SEC` (15.0)

### 2.4 `NativeAudioSink` (`plugin/scripts/python/native_audio_sink.py`)
- **Role:** Synchronous audio player managing child `mpv` process.
- **Contract Defined in `PROJECT.md`:**
  ```python
  class NativeAudioSink:
      def play(self, wav_path: Path) -> None:
          """Plays wav file synchronously via mpv, blocking until playback finishes."""
      def interrupt(self) -> None:
          """Terminates any currently active mpv playback process."""
  ```
- **Invocation:** Synchronously executes `mpv --really-quiet --no-video --keep-open=no --idle=no <wav_path>`.
- **Interrupt Mechanism:** Terminates currently playing child process immediately without `pkill -9 mpv`.

---

## 3. Apple MLX Stream Affinity and Thread Safety

### 3.1 The Single-Thread Stream Affinity Principle
Apple MLX compute streams and Metal device dispatch state are **thread-local**. As documented in `plugin/scripts/python/web_server.py:6`:
> *"MLX detail: MLX state (compute streams) is per-thread. The TTSEngine must be loaded AND used from the same thread."*

If `TTSEngine` is initialized and loads its model on the main thread (or during an arbitrary import/init step), invoking `synthesize()` or `generate()` from the background `_tts_worker` thread causes stream mismatch issues, Metal command buffer failures, or memory corruption.

### 3.2 Architectural Solution: In-Worker Thread Initialization
In `NarratorService`:
- `self._sink = sink or NativeAudioSink()` is instantiated in `__init__` (since `NativeAudioSink` does not use MLX, and its `interrupt()` method must be accessible from the main thread during `UserPromptSubmit` and signal handlers).
- `self._engine`, `self._synth`, and `self._profile` are initialized as `None` in `__init__` (or injected for testing).
- When `_tts_worker()` starts execution on its dedicated background thread, it immediately executes `self._ensure_tts_initialized()`.
- `_ensure_tts_initialized()`:
  1. Instantiates `TTSEngine(model_id=...)`.
  2. Calls `self._engine._ensure_loaded()` on the worker thread, ensuring the MLX Kokoro model is loaded into the worker thread's MLX stream context.
  3. Instantiates `ResilientSynthesizer(self._engine, log=_log)`.
  4. Loads the active `VoiceProfile`.
- Because `self._speak()` is called exclusively from `_tts_worker()`, 100% of MLX operations (`load_model`, `model.generate()`, array conversions) occur strictly on the single `_tts_worker` thread.

### 3.3 Boot Latency & Resilient Recovery
- When `NarratorService.run()` executes `tts_thread.start()`, the background worker thread immediately warms up the model in memory.
- If model loading fails at boot (e.g. offline, missing weights), the error is logged without crashing the daemon process.
- On subsequent speak requests, `_ensure_tts_initialized()` retries initialization.
- If synthesis still fails, the item is dropped safely with an informative log line, and the queue remains healthy.

---

## 4. Voice Profile Loading & Configuration Resolution

To ensure compatibility with existing installations and configurable overrides, `narrator_service.py` resolves voice profiles with the following precedence:
1. Custom path specified in configuration (`config.get("voice_profile_path")`).
2. Shipped calibration file at `_project_root() / "config" / "voice_calibration.json"`.
3. Configuration overrides for `voice_id` and `speed` applied on top of the loaded profile.
4. Hardcoded defaults from `config_constants.py` (`DEFAULT_VOICE_ID = "af_nova"`, `DEFAULT_SPEED = 1.18`, `FALLBACK_CHARS_PER_SEC = 15.0`).

### Implementation Specification:
```python
def _default_voice_profile_path() -> Path:
    return _project_root() / "config" / "voice_calibration.json"


def _load_profile_or_fallback(config: dict | None = None) -> VoiceProfile:
    config = config or {}
    custom_path = config.get("voice_profile_path")
    path = Path(custom_path) if custom_path else _default_voice_profile_path()
    store = VoiceProfileStore(path)
    loaded = store.load()
    if loaded is not None:
        voice_id = config.get("voice_id", loaded.voice_id)
        speed = float(config.get("speed", loaded.speed))
        if voice_id != loaded.voice_id or speed != loaded.speed:
            return VoiceProfile(
                voice_id=voice_id,
                speed=speed,
                chars_per_second=loaded.chars_per_second,
                calibrated_at=loaded.calibrated_at,
                calibration_source_chars=loaded.calibration_source_chars,
            )
        return loaded

    voice_id = config.get("voice_id", DEFAULT_VOICE_ID)
    speed = float(config.get("speed", DEFAULT_SPEED))
    _log(
        f"no voice profile found at {store.path}; using fallback "
        f"voice={voice_id} speed={speed}"
    )
    return VoiceProfile(
        voice_id=voice_id,
        speed=speed,
        chars_per_second=FALLBACK_CHARS_PER_SEC,
        calibrated_at="fallback",
        calibration_source_chars=0,
    )
```

---

## 5. Specification for `_speak(self, line: str)`

The legacy `_speak()` method contained:
- `_wait_mpv_idle()` PRE-wait polling `SessionDir.is_mpv_running()` and `mpv` IPC socket for up to 600s.
- `subprocess.run([_speak_script(), "--keep-artifacts"], ...)` launching `run_speak.sh`.
- Reading `/tmp/auto-speech/wav.path`.
- Duration estimation using `wave.open()`.
- Hard sleep via `time.sleep(duration + 0.5)`.
- Reading `/tmp/auto-speech/mpv.pid` and `os.kill(pid, _signal.SIGKILL)`.
- POST-wait fallback calling `_wait_mpv_idle(15.0)`.

### New In-Process `_speak()` Specification:
```python
    def _speak(self, line: str) -> None:
        """Synthesize and play speech in-process on the _tts_worker thread.

        Replaces legacy run_speak.sh process sprawl and mpv duration sleep hacks.
        Uses ResilientSynthesizer to handle any Kokoro generation faults,
        plays synchronously via NativeAudioSink, and cleans up temporary WAV files.
        """
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

### Key Behavioral Invariants of New `_speak()`:
1. **Zero Interpreter Overhead:** Synthesis runs directly in-process via `ResilientSynthesizer.synthesize_one()`.
2. **Synchronous Hardware Playback:** `self._sink.play(temp_wav)` blocks naturally until `mpv` exits. The thread wakes up immediately when audio playback finishes—no sleeping, no polling.
3. **Leak-Proof File Cleanup:**
   - Primary WAV (`temp_wav`) is unlinked in `finally:`.
   - Partial file (`temp_wav.suffix + ".partial"`) is unlinked in `finally:`.
   - Any split fragments created by `ResilientSynthesizer` during fault recovery (`f"{temp_wav.stem}-*.wav"`) are unlinked in `finally:`.
4. **Resilience to Generation & Playback Exceptions:** Any unexpected exception during synthesis or playback is caught and logged; it never escapes `_speak()` to terminate the worker thread.

---

## 6. Worker Thread (`_tts_worker`) Lifecycle & Queue Safety

### 6.1 Worker Method Structure
```python
    def _tts_worker(self) -> None:
        _log("tts_worker thread started")
        self._ensure_tts_initialized()
        while True:
            phase = self._tts_queue.get()
            if phase is None:
                _log("tts_worker received sentinel; stopping")
                return
            try:
                if isinstance(phase, str):
                    self._speak(phase)
                elif isinstance(phase, dict):
                    _log(f"Dict received in worker: {phase}")
                    if phase.get("type") == "Stop":
                        summ = self._get_summarizer()
                        history = phase.get("history", "")
                        phases_count = phase.get("phases", 0)
                        session_id = phase.get("session_id", "")
                        
                        # SILENCE INVISIBLE CRON TURNS: If the turn was purely a cron tick with no user input, do not announce completion.
                        if "<!-- cron tick -->" in history and "User:" not in history.split("<!-- cron tick -->")[-1]:
                            _log("Silencing end-of-turn summary for invisible cron tick.")
                        else:
                            words = ""
                            if hasattr(summ, "generate_conversational"):
                                words = summ.generate_conversational(
                                    history, "Stop", phases_this_turn=phases_count, session_id=session_id
                                )
                            _log(f"OUTPUT: {words}")
                            if words:
                                self._speak(words)
                else:
                    _log(f"Phase received in worker: {phase}")
                    summ = self._get_summarizer()
                    line = summ.summarize(phase)
                    _log(f"Summarizer generated: {line}")
                    if line:
                        self._speak(line)
            except Exception as exc:
                _log(f"tts_worker error: {exc!r}")
            finally:
                self._tts_queue.task_done()
                self._update_depth(self._tts_queue.qsize())
```

### 6.2 Guarantees:
- **FIFO Playback:** Single consumer dequeues one item, synthesizes, plays to completion, and only then dequeues the next.
- **Accurate Depth Mirroring:** `self._update_depth()` is guaranteed in the `finally:` block of every queue item, updating `/tmp/auto-speech-narration-depth`.
- **Graceful Shutdown:** Enqueuing `None` breaks out of the loop and terminates the thread.
- **Worker Immunity:** Any failure in `_get_summarizer()`, `summarize()`, or `_speak()` is caught and logged. The worker loop remains active and responsive.

---

## 7. Catalog of Legacy Code & Hacks to Remove

| Line Range in Current `narrator_service.py` | Code / Artifact | Rationale for Removal |
|---|---|---|
| Lines 109–111 | `def _speak_script() -> Path:` | Removed. In-process `TTSEngine` eliminates `run_speak.sh`. |
| Lines 237–243 | `if hasattr(self._classifier, "_current")...` | Fixed. Must use `classifier = getattr(self, "_classifier", None)` to prevent `AttributeError` during `__new__` unit tests. |
| Lines 317–318 | `subprocess.run(["pkill", "-9", "mpv"], capture_output=True)` | Replaced with `if hasattr(self, "_sink") and self._sink is not None: self._sink.interrupt()`. |
| Lines 333–350 | Duplicate code block on `UserPromptSubmit` | Removed. Dead code unreachable due to `continue` at line 331. |
| Lines 501–561 | Legacy `_speak()` implementation (`_wait_mpv_idle`, `_speak_script`, `SessionDir`, `wave.open`, `time.sleep`, `os.kill(pid, SIGKILL)`) | Replaced with in-process `_speak()` using `ResilientSynthesizer` + `NativeAudioSink`. |
| Lines 562–597 | `def _wait_mpv_idle(...)` | Removed. Blocking `NativeAudioSink` renders mpv idle polling obsolete. |

---

## 8. Exact Before & After Code Specification

### 8.1 Imports Section
**Before (lines 15–39):**
```python
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from auto_speech_log import get_logger
from narrator_config import load_config
from narrator_phase_classifier import Category, Phase, PhaseClassifier
from narrator_state import (
    IDLE_SHUTDOWN,
    NOT_RUNNING,
    RUNNING,
    SIGNAL_SHUTDOWN,
    STARTING,
    NarratorStateMachine,
)
from narrator_summarizer import Summarizer, load_summarizer
```

**After:**
```python
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from auto_speech_log import get_logger
from config_constants import DEFAULT_SPEED, DEFAULT_VOICE_ID, FALLBACK_CHARS_PER_SEC
from native_audio_sink import NativeAudioSink
from narrator_config import load_config
from narrator_phase_classifier import Category, Phase, PhaseClassifier
from narrator_state import (
    IDLE_SHUTDOWN,
    NOT_RUNNING,
    RUNNING,
    SIGNAL_SHUTDOWN,
    STARTING,
    NarratorStateMachine,
)
from narrator_summarizer import Summarizer, load_summarizer
from resilient_synthesizer import ResilientSynthesizer
from tts_engine import TTSEngine, TTSGenerationError
from voice_profile import VoiceProfile
from voice_profile_store import VoiceProfileStore
```

---

### 8.2 Profile Loader (Replaces `_speak_script()`, lines 109–111)
**Before:**
```python
def _speak_script() -> Path:
    return _project_root() / "plugin" / "scripts" / "shell" / "run_speak.sh"
```

**After:**
```python
def _default_voice_profile_path() -> Path:
    return _project_root() / "config" / "voice_calibration.json"


def _load_profile_or_fallback(config: dict | None = None) -> VoiceProfile:
    config = config or {}
    custom_path = config.get("voice_profile_path")
    path = Path(custom_path) if custom_path else _default_voice_profile_path()
    store = VoiceProfileStore(path)
    loaded = store.load()
    if loaded is not None:
        voice_id = config.get("voice_id", loaded.voice_id)
        speed = float(config.get("speed", loaded.speed))
        if voice_id != loaded.voice_id or speed != loaded.speed:
            return VoiceProfile(
                voice_id=voice_id,
                speed=speed,
                chars_per_second=loaded.chars_per_second,
                calibrated_at=loaded.calibrated_at,
                calibration_source_chars=loaded.calibration_source_chars,
            )
        return loaded

    voice_id = config.get("voice_id", DEFAULT_VOICE_ID)
    speed = float(config.get("speed", DEFAULT_SPEED))
    _log(
        f"no voice profile found at {store.path}; using fallback "
        f"voice={voice_id} speed={speed}"
    )
    return VoiceProfile(
        voice_id=voice_id,
        speed=speed,
        chars_per_second=FALLBACK_CHARS_PER_SEC,
        calibrated_at="fallback",
        calibration_source_chars=0,
    )
```

---

### 8.3 `NarratorService.__init__` & `_on_signal`
**Before (lines 114–136, 187–192):**
```python
class NarratorService:
    def __init__(self) -> None:
        self._config = load_config()
        self._classifier = PhaseClassifier(
            silence_seconds=0.5, max_events_per_phase=1
        )
        self._max_queue = int(self._config.get("max_queue_depth", 32))
        self._tts_queue: queue.Queue = queue.Queue(
            maxsize=self._max_queue
        )
        self._dropped_phases = 0
        self._summarizer: Summarizer | None = None
        self._summarizer_lock = threading.Lock()
        self._last_event_ts = time.time()
        self._idle_shutdown = float(self._config["idle_shutdown_seconds"])
        self._stop = threading.Event()
        self._fsm = NarratorStateMachine()
        self._phases_this_turn = 0
...
    def _on_signal(self, signum, frame):  # noqa: ARG002
        _log(f"signal {signum} → shutting down")
        if self._fsm.can(SIGNAL_SHUTDOWN):
            self._fsm.transition(SIGNAL_SHUTDOWN)  # RUNNING → SIGNAL_SHUTDOWN
        self._stop.set()
```

**After:**
```python
class NarratorService:
    def __init__(
        self,
        *,
        sink: NativeAudioSink | None = None,
        engine: TTSEngine | None = None,
        synth: ResilientSynthesizer | None = None,
        profile: VoiceProfile | None = None,
    ) -> None:
        self._config = load_config()
        self._classifier = PhaseClassifier(
            silence_seconds=0.5, max_events_per_phase=1
        )
        self._max_queue = int(self._config.get("max_queue_depth", 32))
        self._tts_queue: queue.Queue = queue.Queue(
            maxsize=self._max_queue
        )
        self._dropped_phases = 0
        self._summarizer: Summarizer | None = None
        self._summarizer_lock = threading.Lock()
        self._last_event_ts = time.time()
        self._idle_shutdown = float(self._config["idle_shutdown_seconds"])
        self._stop = threading.Event()
        self._fsm = NarratorStateMachine()
        self._phases_this_turn = 0

        # In-process audio sink and TTS components
        self._sink: NativeAudioSink = sink if sink is not None else NativeAudioSink()
        self._engine: TTSEngine | None = engine
        self._synth: ResilientSynthesizer | None = synth
        self._profile: VoiceProfile | None = profile
...
    def _on_signal(self, signum, frame):  # noqa: ARG002
        _log(f"signal {signum} → shutting down")
        if self._fsm.can(SIGNAL_SHUTDOWN):
            self._fsm.transition(SIGNAL_SHUTDOWN)  # RUNNING → SIGNAL_SHUTDOWN
        self._stop.set()
        if hasattr(self, "_sink") and self._sink is not None:
            self._sink.interrupt()
```

---

### 8.4 Line 237 Bug Fix in `_tail_events`
**Before (lines 236–243):**
```python
            # Wall-clock phase flush
            if hasattr(self._classifier, "_current") and self._classifier._current:
                for sid, current in list(self._classifier._current.items()):
                    if time.time() - current.events[-1].ts > self._classifier._silence_seconds:
                        closed = self._classifier.flush(sid)
                        if closed:
                            self._maybe_enqueue(closed)
```

**After:**
```python
            # Wall-clock phase flush
            classifier = getattr(self, "_classifier", None)
            if classifier is not None and hasattr(classifier, "_current") and classifier._current:
                for sid, current in list(classifier._current.items()):
                    if time.time() - current.events[-1].ts > classifier._silence_seconds:
                        closed = classifier.flush(sid)
                        if closed:
                            self._maybe_enqueue(closed)
```

---

### 8.5 `UserPromptSubmit` Cleanup in `_process_chunk`
**Before (lines 316–350):**
```python
            if event_type == "UserPromptSubmit":
                if self._is_cron_tick(ev.get("payload", {}).get("transcriptPath")):
                    _log("Skipping UserPromptSubmit narration for background cron tick.")
                    continue

                # Immediately interrupt any currently playing audio so the user isn't talked over!
                import subprocess
                subprocess.run(["pkill", "-9", "mpv"], capture_output=True)
                
                self._classifier.flush(session_id)
                self._phases_this_turn = 0
                try:
                    summarizer = self._get_summarizer()
                    if hasattr(summarizer, "generate_conversational"):
                        history = ev.get("payload", {}).get("conversation_history", "")
                        words = summarizer.generate_conversational(history, "UserPromptSubmit", session_id=session_id)
                        if words:
                            self._tts_queue.put(words)
                except Exception as e:
                    _log(f"Failed to generate start words: {e}")
                continue
                    
                # Immediately interrupt any currently playing audio so the user isn't talked over!
                subprocess.run(["pkill", "-9", "mpv"], capture_output=True)
                
                self._classifier.flush(session_id)
                self._phases_this_turn = 0
                try:
                    summarizer = self._get_summarizer()
                    if hasattr(summarizer, "generate_conversational"):
                        history = ev.get("payload", {}).get("conversation_history", "")
                        _log(f"Calling generate_conversational for UserPromptSubmit with history length {len(history)}")
                        words = summarizer.generate_conversational(history, "UserPromptSubmit", session_id=session_id)
                        _log(f'OUTPUT: {words}')
                        if words:
                            self._tts_queue.put(words)
                except Exception as e:
                    _log(f"Failed to generate start words: {e}")
                continue
```

**After:**
```python
            if event_type == "UserPromptSubmit":
                if self._is_cron_tick(ev.get("payload", {}).get("transcriptPath")):
                    _log("Skipping UserPromptSubmit narration for background cron tick.")
                    continue

                # Immediately interrupt any currently playing audio so the user isn't talked over!
                if hasattr(self, "_sink") and self._sink is not None:
                    self._sink.interrupt()
                
                self._classifier.flush(session_id)
                self._phases_this_turn = 0
                try:
                    summarizer = self._get_summarizer()
                    if hasattr(summarizer, "generate_conversational"):
                        history = ev.get("payload", {}).get("conversation_history", "")
                        words = summarizer.generate_conversational(history, "UserPromptSubmit", session_id=session_id)
                        if words:
                            self._tts_queue.put(words)
                except Exception as e:
                    _log(f"Failed to generate start words: {e}")
                continue
```

---

### 8.6 Worker TTS Initialization & Playback
**Before (lines 462–597):**
Includes raw `_tts_worker()`, legacy `_speak()`, and `_wait_mpv_idle()`.

**After:**
```python
    def _ensure_tts_initialized(self) -> None:
        """Initialize TTSEngine and ResilientSynthesizer on the worker thread.

        Honors Apple MLX single-thread stream affinity: MLX compute streams
        are per-thread, so the model must be loaded and invoked on the exact
        same thread (_tts_worker).
        """
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

    def _tts_worker(self) -> None:
        _log("tts_worker thread started")
        self._ensure_tts_initialized()
        while True:
            phase = self._tts_queue.get()
            if phase is None:
                _log("tts_worker received sentinel; stopping")
                return
            try:
                if isinstance(phase, str):
                    self._speak(phase)
                elif isinstance(phase, dict):
                    _log(f"Dict received in worker: {phase}")
                    if phase.get("type") == "Stop":
                        summ = self._get_summarizer()
                        history = phase.get("history", "")
                        phases_count = phase.get("phases", 0)
                        session_id = phase.get("session_id", "")
                        
                        # SILENCE INVISIBLE CRON TURNS: If the turn was purely a cron tick with no user input, do not announce completion.
                        if "<!-- cron tick -->" in history and "User:" not in history.split("<!-- cron tick -->")[-1]:
                            _log("Silencing end-of-turn summary for invisible cron tick.")
                        else:
                            words = ""
                            if hasattr(summ, "generate_conversational"):
                                words = summ.generate_conversational(
                                    history, "Stop", phases_this_turn=phases_count, session_id=session_id
                                )
                            _log(f"OUTPUT: {words}")
                            if words:
                                self._speak(words)
                else:
                    _log(f"Phase received in worker: {phase}")
                    summ = self._get_summarizer()
                    line = summ.summarize(phase)
                    _log(f"Summarizer generated: {line}")
                    if line:
                        self._speak(line)
            except Exception as exc:
                _log(f"tts_worker error: {exc!r}")
            finally:
                self._tts_queue.task_done()
                self._update_depth(self._tts_queue.qsize())

    def _speak(self, line: str) -> None:
        """Synthesize and play speech in-process on the _tts_worker thread.

        Replaces legacy run_speak.sh process sprawl and mpv duration sleep hacks.
        Uses ResilientSynthesizer to handle any Kokoro generation faults,
        plays synchronously via NativeAudioSink, and cleans up temporary WAV files.
        """
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

---

## 9. Verification & Unit Testing Strategy

### 9.1 Fixing Existing Test Suite Failure
1. **Target:** `tests/test_narrator_service.py:306`.
2. **Issue:** `AttributeError: 'NarratorService' object has no attribute '_classifier'` at line 237 of `narrator_service.py`.
3. **Resolution:** Safe attribute check `classifier = getattr(self, "_classifier", None)` in `_tail_events()`.
4. **Verification Command:**
   ```bash
   .venv/bin/python tests/test_narrator_service.py
   ```
   All 15 existing tests will immediately pass.

### 9.2 New Unit Tests for In-Process TTS Integration
To thoroughly verify the new functionality without requiring real audio hardware or downloading 2 GB models during hermetic test runs:

1. **`test_speak_synthesizes_and_plays`:**
   - Instantiate `NarratorService` injecting mock `NativeAudioSink` and mock `ResilientSynthesizer`.
   - Call `_speak("Testing narration")`.
   - Assert `mock_synth.synthesize_one` was called with text, profile, and temp WAV path.
   - Assert `mock_sink.play` was called with that WAV path.
   - Assert temp WAV path is deleted after `_speak()` returns.

2. **`test_speak_skips_unspeakable_content`:**
   - Configure `mock_synth.synthesize_one` to return `False` (representing unpronounceable text / symbols).
   - Call `_speak("•••")`.
   - Assert `mock_sink.play` was never invoked.
   - Assert temp WAV path is deleted.

3. **`test_speak_cleans_up_on_synthesis_fault`:**
   - Configure `mock_synth.synthesize_one` to raise `TTSGenerationError("synthesis crashed")`.
   - Call `_speak("error text")`.
   - Assert exception does not bubble out.
   - Assert temp WAV and `.partial` files are deleted.

4. **`test_speak_cleans_up_on_playback_failure`:**
   - Configure `mock_sink.play` to raise `RuntimeError("mpv failure")`.
   - Call `_speak("test")`.
   - Assert exception does not crash the caller.
   - Assert temp WAV is deleted.

5. **`test_tts_worker_recovers_from_item_error`:**
   - Enqueue a faulty item and a valid item into `_tts_queue`, followed by `None`.
   - Run worker loop.
   - Assert both items complete (`task_done` invoked twice) and worker terminates cleanly upon receiving `None`.
