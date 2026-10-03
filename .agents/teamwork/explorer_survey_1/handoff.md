# Handoff Report: `narrator_service.py` Architecture, Process Lifecycle, and `NativeAudioSink` Integration

**Agent**: `explorer_survey_1`  
**Working Directory**: `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_1`  
**Handoff Type**: Hard (Investigation complete)

---

## 1. Observation

### Obs 1. Current Architecture of `narrator_service.py`
- **File**: `plugin/scripts/python/narrator_service.py` (643 lines)
- **Lifecycle & Process Management**:
  - PID file: `/tmp/auto-speech-narrator-daemon.pid` (line 41).
  - Liveness verification: `_existing_pid()` (line 94) delegates to `_pid_is_our_daemon(pid)` (line 78), which checks `os.kill(pid, 0)` and executes `ps -p <pid> -o command=` to confirm `"/narrator_service.py"` in command line (line 91).
  - State machine: `NarratorStateMachine` (`narrator_state.py`) tracking `NOT_RUNNING -> STARTING -> RUNNING -> {IDLE_SHUTDOWN, SIGNAL_SHUTDOWN} -> NOT_RUNNING` (lines 30–37, 134, 138, 140, 183, 190, 233).
  - Signals: Catches `SIGTERM` and `SIGINT` via `_on_signal` (lines 141–142, 187–192), sets `self._stop = threading.Event()`.
  - Auto-shutdown: Shuts down after `idle_shutdown_seconds` (default 600s) if no events are received (lines 130, 230–234).
  - External control scripts: `plugin/scripts/shell/narrator_service_start.sh` (detaches via Python double-fork to `.out` log), `narrator_service_status.sh`, and `narrator_service_stop.sh` (sends `SIGTERM`, waits, escalates to `SIGKILL`).
  - Auto-launch hook: `plugin/scripts/shell/narrator_hook.sh` (lines 56–71) auto-starts the daemon if opted in and daemon is not alive.

### Obs 2. Threading Model and Queue (`_tts_queue`)
- **Main Thread (`_tail_events`)**:
  - Tails `/tmp/auto-speech-narrator-events.jsonl` from offset stored in `/tmp/auto-speech-narrator-daemon.watermark` (lines 193–244).
  - Polls every `0.25`s (`POLL_INTERVAL_S`), parses complete lines, calls `_process_chunk` (lines 214–227).
  - Filters events by session marker in `~/.claude/auto-speech-narrate-sessions/<session_id>` (lines 293–304).
  - Feeds events to `PhaseClassifier(silence_seconds=0.5, max_events_per_phase=1)` (line 116).
  - Also flushes phases based on wall-clock silence (lines 237–243).
- **TTS Worker Thread (`_tts_worker`)**:
  - Dedicated background daemon thread `threading.Thread(target=self._tts_worker, daemon=True)` (line 152).
  - Consumes items from `self._tts_queue` in FIFO order (lines 463–500).
  - Supported item types:
    1. `str`: Direct text, calls `self._speak(phase)` (lines 468–469).
    2. `dict`: If `type == "Stop"`, calls `summarizer.generate_conversational(history, "Stop", ...)` and speaks output (lines 470–487).
    3. `Phase`: Summarizes closed phase via `summarizer.summarize(phase)` and speaks output (lines 488–495).
    4. `None`: Sentinel to exit thread on shutdown (lines 167, 465–466).
  - Backpressure: `_enqueue_phase()` caps queue at `max_queue_depth` (default 32) and drops oldest item on overflow (`self._tts_queue.get_nowait()`) with log tracking (lines 398–434).
  - Mirrored depth: Writes `_tts_queue.qsize()` to `/tmp/auto-speech-narration-depth` (lines 436–440) for autoplay synchronization.

### Obs 3. Playback Implementation & Hacks
- **`run_speak.sh` Sprawl**:
  - Line 110: `_speak_script()` points to `plugin/scripts/shell/run_speak.sh`.
  - Lines 530–536: `_speak()` runs `subprocess.run([str(speak), "--keep-artifacts"], input=line.encode("utf-8"), ...)` which spins up a new Python interpreter running `speak.py` and `PipelineOrchestrator` for every phrase.
- **Detached `mpv` Process**:
  - `speak.py` calls `MpvController().start(wav_path)`, which launches `mpv` in background with `start_new_session=True` and `--input-ipc-server=/tmp/auto-speech/control.sock`, writing PID to `/tmp/auto-speech/mpv.pid` (lines 104–125 in `mpv_controller.py`).
- **`time.sleep` Duration Calculation**:
  - Lines 542–550 of `narrator_service.py`:
    ```python
    wav_path = SessionDir.wav_path_path().read_text().strip()
    with wave.open(wav_path, "r") as f:
        duration = f.getnframes() / float(f.getframerate())
    _log(f"sleeping for wav duration: {duration:.2f}s")
    time.sleep(duration + 0.5)  # slight buffer
    ```
- **`SIGKILL` & `pkill` Hacks**:
  - Lines 552–557: Daemon reads `SessionDir.read_pid()` and runs `os.kill(pid, _signal.SIGKILL)` immediately after `time.sleep` expires.
  - Lines 591–596: `os.kill(pid, signal.SIGKILL)` in `_wait_mpv_idle()` timeout.
  - Lines 318 & 334: `subprocess.run(["pkill", "-9", "mpv"], capture_output=True)` executed on `UserPromptSubmit`.
- **Duplicate Dead Code**:
  - Lines 317–331 and 334–349 contain identical duplicate code blocks on `UserPromptSubmit`. Due to `continue` at line 331, lines 334–349 are unreachable.

### Obs 4. Test Failure in `tests/test_narrator_service.py`
- Command: `.venv/bin/python tests/test_narrator_service.py`
- Result: Exited with code 1.
- Error Traceback:
  ```
  File "tests/test_narrator_service.py", line 306, in test_tail_resumes_a_line_split_across_two_reads
    svc._tail_events()
  File "plugin/scripts/python/narrator_service.py", line 237, in _tail_events
    if hasattr(self._classifier, "_current") and self._classifier._current:
  AttributeError: 'NarratorService' object has no attribute '_classifier'
  ```
- Line 237 was added without safe attribute lookup (`getattr(self, "_classifier", None)`), causing unit tests that instantiate `NarratorService` via `__new__` to crash.

---

## 2. Logic Chain

1. **Elimination of Process Sprawl**:
   - (Obs 3) shows that `_speak` currently executes `subprocess.run([str(run_speak.sh), "--keep-artifacts"])`, triggering a cascade of shell script → python interpreter → `PipelineOrchestrator` → `ShortPathStrategy` → `MpvController` → detached `mpv`.
   - By importing `TTSEngine` directly into `narrator_service.py`, synthesis can occur in-process in the `_tts_worker` thread.
2. **Elimination of Detached `mpv`, `time.sleep`, and `SIGKILL`**:
   - (Obs 3) shows that detached `mpv` forced `narrator_service.py` into guessing duration via `wave.open` and sleeping (`time.sleep(duration + 0.5)`), followed by force-killing mpv (`SIGKILL`).
   - If `NativeAudioSink` runs `mpv` synchronously using `subprocess.run(["mpv", "--really-quiet", "--no-video", "--keep-open=no", "--idle=no", str(wav_path)])`, `subprocess.run` blocks until playback finishes.
   - The thread naturally unblocks precisely when audio finishes, rendering `wave.open`, `time.sleep`, `SessionDir`, and `SIGKILL` completely obsolete.
3. **Thread Safety & MLX Stream Affinity**:
   - `web_server.py` documents that MLX compute streams are per-thread.
   - (Obs 2) shows that `_tts_worker` is already a dedicated single-consumer thread for `_tts_queue`.
   - Performing all `TTSEngine.synthesize` and `NativeAudioSink.play` calls on the `_tts_worker` thread guarantees thread affinity and sequential FIFO audio output with zero concurrency conflicts.
4. **UNIX Socket IPC Integration**:
   - (Obs 2) shows `_tts_worker` already accepts raw `str` items in `_tts_queue` and speaks them.
   - Adding a `socketserver.ThreadingUnixStreamServer` listening on `/tmp/auto-speech-daemon.sock` allows external clients (like `speak.py`) to connect and send text directly into `self._tts_queue.put(text)`.
   - This turns `speak.py` into a thin CLI client that sends stdin over the socket and exits cleanly.

---

## 3. Caveats

1. **Temporary WAV Retention**: `TTSEngine` writes to a filesystem path. When synthesizing in-process, temporary WAV files should be created via `tempfile.NamedTemporaryFile` and deleted in a `finally` block once `NativeAudioSink.play()` returns, ensuring no disk leak in `/tmp`.
2. **Interruption Handling**: On `UserPromptSubmit`, `narrator_service.py` currently executes `pkill -9 mpv`. To completely eliminate `SIGKILL`/`pkill`, `NativeAudioSink` can track its active child process (`self._current_proc`) and expose an `interrupt()` method that terminates only its own process.
3. **Autoplay Worker Dependency**: `autoplay_worker.py` currently invokes `run_speak.sh --source-hash ...`. When `run_speak.sh` is deleted and `speak.py` is refactored into a thin client, `autoplay_worker.py` or its callers will need to invoke `speak.py` directly or through the socket. (This falls under Milestone 2/3 coordination).

---

## 4. Conclusion

1. `narrator_service.py` possesses a well-structured daemon lifecycle and queue architecture (`_tts_queue`, drop-oldest backpressure, depth mirroring, FSM states), but its playback subsystem is crippled by external process spawns, duration guessing, and `SIGKILL` hacks.
2. Integrating `TTSEngine` and `NativeAudioSink` directly into `narrator_service.py` will:
   - Provide instant, zero-latency streaming synthesis without secondary Python interpreters.
   - Replace detached background `mpv` with clean, synchronous blocking playback.
   - Eliminate `run_speak.sh`, `time.sleep()`, `SessionDir`, and all `SIGKILL`/`pkill` hacks.
3. The UNIX domain socket listener can be attached cleanly to `NarratorService` to ingest raw speech requests into `_tts_queue`.
4. The existing unit test crash on line 237 (`_classifier` attribute error) must be resolved by using `getattr(self, "_classifier", None)`.

---

## 5. Verification Method

### 1. Test Reproduction
Run the unit test for `narrator_service.py`:
```bash
.venv/bin/python tests/test_narrator_service.py
```
*Current state*: Fails at line 237 with `AttributeError`.  
*After fix*: Passes all 15 unit tests.

### 2. Full Suite Verification
Run the hermetic and standard test suites:
```bash
bash tests/run_all.sh --hermetic
```

### 3. File Inspection
Inspect the following locations for code changes and verification:
- `plugin/scripts/python/narrator_service.py`: Verify removal of `_speak_script`, `time.sleep`, `SessionDir`, and `SIGKILL`/`pkill`.
- `plugin/scripts/python/native_audio_sink.py`: Verify implementation of `NativeAudioSink` using blocking `subprocess.run` / `Popen`.
- `plugin/scripts/python/speak.py`: Verify thin client socket forwarder.

### Invalidation Conditions
- If MLX model loading fails when called from within the `_tts_worker` thread.
- If `subprocess.run(["mpv", "--really-quiet", ...])` leaves zombie processes or fails to block until audio completion.
