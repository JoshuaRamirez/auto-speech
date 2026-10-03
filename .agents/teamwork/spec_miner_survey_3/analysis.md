# Architectural Mining & Specification Report: Unified Daemon Server Refactor

**Date:** 2026-10-03  
**Miner:** `spec_miner_survey_3`  
**Working Directory:** `/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_survey_3`  
**Project Root:** `/Users/joshua/Developer/auto-speech`  
**Reference Document:** `/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md`

---

## 1. Executive Summary

This specification mining report covers three distinct areas necessary to refactor `auto-speech` from its legacy multi-process architecture into a **Unified Daemon Server**:
1. **Dead Architectural Sprawl to Delete:** An exhaustive census of all files, classes, methods, imports, call sites, and test cases for `run_speak.sh`, `PipelineOrchestrator`, `ShortPathStrategy`, `MpvController`, and `SessionDir` PID management.
2. **Thin Client IPC via UNIX Sockets:** The complete behavioral specification of `speak.py` (CLI flags, stdin handling, environment variables, exit codes) and the exact wire protocol/contract for `speak.py` $\to$ UNIX socket (`/tmp/auto-speech-daemon.sock`) $\to$ `narrator_service.py` background `socketserver`.
3. **Existing Test Suites & Test Harness:** A catalog of all 41 test files (35 Python, 6 Bash), test execution mechanisms (`tests/run_all.sh` vs. `pytest`), how `speak.py` and `narrator_service.py` are tested today, tests broken by sprawl removal, and baseline test failures.

---

## 2. Dead Architectural Sprawl to Delete

The original architecture used independent processes that coordinated playback by spawning detached `mpv` background processes, recording PIDs in `/tmp/auto-speech/mpv.pid`, acquiring flock locks, polling socket files, sleeping for WAV durations, and issuing `SIGKILL`. The new architecture designates `narrator_service.py` as the sole daemon process owning the audio hardware.

### 2.1 Component 1: `run_speak.sh`

* **Exact File Location:** `/Users/joshua/Developer/auto-speech/plugin/scripts/shell/run_speak.sh` (21 lines)
* **Description:** A bash script that resolves `$PROJECT_ROOT`, activates `$PROJECT_ROOT/.venv`, and invokes `exec python "$PLUGIN_SCRIPTS_DIR/python/speak.py" "$@"`.
* **Obsolete Rationale:** It was created as an entry wrapper for slash commands and background workers. With `speak.py` becoming a lightweight thin client communicating over a UNIX socket, a venv-activating shell wrapper is redundant; `speak.py` can be executed directly by Python (`python3` or the system/project python) without heavy virtualenv activation overhead.
* **All References and Call Sites:**
  1. `plugin/scripts/shell/run_speak.sh` (the file itself)
  2. `plugin/commands/auto-speech-speak.md` (lines 63–65):
     ```bash
     "$PROJECT_ROOT/plugin/scripts/shell/run_speak.sh" \
         --ordinal ORDINAL --source-hash "$SOURCE_HASH" < "$REWRITE_FILE" >>"$LOG" 2>&1
     ```
  3. `plugin/scripts/python/autoplay_worker.py`:
     - Line 18: Docstring references `run_speak.sh`
     - Line 52: `SPEAK = _PLUGIN_SCRIPTS_DIR / "shell" / "run_speak.sh"`
     - Line 392: `rc, _ = self._runner(["bash", str(SPEAK), "--source-hash", source_hash], stdin_text=stdin_text)`
  4. `plugin/scripts/python/say_worker.py`:
     - Line 5: Docstring references "speaks the file's text verbatim via the speak wrapper"
     - Line 27: `from autoplay_worker import SPEAK, ...`
     - Line 113: `rc = self._runner(["bash", str(SPEAK)], stdin_text=text)`
  5. `plugin/scripts/python/narrator_service.py`:
     - Lines 109–110:
       ```python
       def _speak_script() -> Path:
           return _project_root() / "plugin" / "scripts" / "shell" / "run_speak.sh"
       ```
     - Line 522: `speak = _speak_script()`
     - Line 531: `proc = subprocess.run([str(speak), "--keep-artifacts"], input=line.encode("utf-8"), ...)`
  6. `tests/test_autoplay_worker.py`:
     - Line 155: `if str(prog).endswith("run_speak.sh"):`
  7. Documentation:
     - `docs/micro-design/phase-8-plugin.md` (lines 11, 17, 36)
     - `docs/micro-design/phase-11-cache-replay.md` (lines 108, 116, 209)
     - `docs/micro-design/phase-15-stop-hook-autoplay.md` (lines 56, 62, 130, 134, 140)
     - `docs/decisions/ADR-008-source-hash-replay-cache.md` (line 124)
* **Required Adjustments Upon Deletion:**
  - `plugin/commands/auto-speech-speak.md`: Replace `$PROJECT_ROOT/plugin/scripts/shell/run_speak.sh` with `python3 "$PROJECT_ROOT/plugin/scripts/python/speak.py"`.
  - `autoplay_worker.py`: Update `SPEAK` to point to `_PLUGIN_SCRIPTS_DIR / "python" / "speak.py"` and invoke `["python3", str(SPEAK), ...]` (or invoke via sys.executable).
  - `say_worker.py`: Update execution to use `speak.py`.
  - `narrator_service.py`: Delete `_speak_script()` and the `subprocess.run([str(speak)...])` block entirely (speech is now synthesized in-process via `TTSEngine` and played via `NativeAudioSink`).
  - `tests/test_autoplay_worker.py`: Update assertion `str(prog).endswith("run_speak.sh")` to `str(prog).endswith("speak.py")`.

---

### 2.2 Component 2: `PipelineOrchestrator` / `pipeline.py`

* **Exact File Location:** `/Users/joshua/Developer/auto-speech/plugin/scripts/python/pipeline.py` (322 lines, containing `class PipelineOrchestrator` lines 94–322, exit code constants lines 34–38, and helper functions lines 41–91).
* **Description:** Top-level L4 orchestrator for `/speak`. Loaded voice profiles, inspected the replay cache (`CacheStore`), branched into `ShortPathStrategy` or `_long_path` (Fibonacci chunk planner, `SegmentProducer`, `PlaybackConsumer`, worker threads), promoted generated WAVs to `CacheStore`, and launched detached `mpv` via `MpvController().start(wav_path)`.
* **Obsolete Rationale:** The daemon handles synthesis and audio playback in-process sequentially. `speak.py` no longer runs any pipeline orchestration locally.
* **All References and Call Sites:**
  1. `plugin/scripts/python/pipeline.py`: Defines `PipelineOrchestrator`, `EXIT_OK`, `EXIT_REWRITE_FAIL`, `EXIT_TTS_FAIL`, `EXIT_PLAYBACK_FAIL`, `EXIT_INTERRUPTED`.
  2. `plugin/scripts/python/speak.py`:
     - Line 14: `from pipeline import PipelineOrchestrator`
     - Line 44: `orchestrator = PipelineOrchestrator(keep_artifacts=args.keep_artifacts, source_hash=args.source_hash)`
     - Line 48: `return orchestrator.run(transcript_text=transcript_text, turn_ordinal=args.ordinal)`
  3. `plugin/scripts/python/web_server.py`:
     - Line 78: `from pipeline import EXIT_OK, PipelineOrchestrator`
     - Line 531: `orchestrator = PipelineOrchestrator(source_hash=source_hash, cache_root=_cache_root(), tts_engine=self._tts)`
     - Line 540: `rc = self._tts_executor.submit(orchestrator.run, audio_text).result()`
  4. `tests/test_synthesize_endpoint.py`:
     - Lines 235, 258:
       ```python
       with mock.patch.object(web_server, "PipelineOrchestrator") as po_cls:
           po_cls.return_value.run.return_value = web_server.EXIT_OK
           ...
           po_cls.return_value.run.assert_called_once()
       ```
  5. Documentation & diagrams:
     - `docs/specification/02-analysis.md`, `03-design.md`, `glossary.md`, `artifacts/class-inventory.md`, `artifacts/domain-model.md`, `artifacts/interface-contracts.md`, `diagrams/component-architecture.mmd`
     - `docs/decisions/ADR-007`, `ADR-010`
     - `docs/plan/phase-breakdown.md`, `implementation-plan.md`, `artifact-map.md`
     - `docs/micro-design/phase-7-orchestrator.md`, `phase-10-concat.md`, `phase-11-cache-replay.md`, `phase-13-web-server.md`, `phase-14-claude-cli-rewriter.md`, `phase-15-stop-hook-autoplay.md`, `phase-17-fire-and-forget-speak.md`
* **Blast Radius / Required Adjustments:**
  - Delete `pipeline.py` (or keep exit constants if needed by external callers, though `EXIT_OK = 0` is standard).
  - `speak.py`: Remove import and usage of `PipelineOrchestrator`. Forward text via UNIX socket instead.
  - `web_server.py`: Remove `PipelineOrchestrator` import. Refactor `_run_speak_job` to synthesize directly or send to the daemon socket.
  - `tests/test_synthesize_endpoint.py`: Update `test_synthesize_not_blocked_by_inflight_rewrite` which mocks `web_server.PipelineOrchestrator`.

---

### 2.3 Component 3: `ShortPathStrategy` / `short_path.py`

* **Exact File Location:** `/Users/joshua/Developer/auto-speech/plugin/scripts/python/short_path.py` (53 lines, `class ShortPathStrategy`).
* **Description:** Implemented duration estimation check (`should_use`) and one-shot audio synthesis via `ResilientSynthesizer`, then called `MpvController().start(wav_path)`.
* **Obsolete Rationale:** In the Unified Daemon, speech requests are synthesized and played sequentially using in-process `TTSEngine` and `NativeAudioSink`. Transcripts are generated directly without the legacy short/long path branching.
* **All References and Call Sites:**
  1. `plugin/scripts/python/short_path.py`: Defines `ShortPathStrategy`.
  2. `plugin/scripts/python/pipeline.py`:
     - Line 26: `from short_path import ShortPathStrategy`
     - Line 161: `short_path = ShortPathStrategy(SHORT_THRESHOLD_SECONDS)`
     - Line 165: `if short_path.should_use(transcript, profile):`
     - Line 168: `exit_code = short_path.execute(...)`
  3. `plugin/scripts/python/web_server.py`:
     - Line 82: `from short_path import ShortPathStrategy`
     - Line 715: `if ShortPathStrategy(SHORT_THRESHOLD_SECONDS).should_use(transcript, profile):`
  4. `tests/test_resilient_synthesizer.py`:
     - Line 6: Comment reference only.
  5. Documentation: `docs/specification/...`, `docs/plan/...`, `docs/micro-design/...`.
* **Blast Radius / Required Adjustments:**
  - Delete `short_path.py`.
  - In `web_server.py` line 715: Replace `ShortPathStrategy(SHORT_THRESHOLD_SECONDS).should_use(transcript, profile)` with `DurationEstimator.estimate_seconds(transcript.char_count, profile) <= SHORT_THRESHOLD_SECONDS`.

---

### 2.4 Component 4: `MpvController` / `mpv_controller.py`

* **Exact File Location:** `/Users/joshua/Developer/auto-speech/plugin/scripts/python/mpv_controller.py` (240 lines, `class MpvController`, `class MpvNotInstalledError`, `class MpvStartupError`).
* **Description:** Managed spawning detached `mpv` background processes (`subprocess.Popen(..., start_new_session=True)`), writing PIDs to `SessionDir`, acquiring cross-process start locks via `fcntl.flock` on `/tmp/auto-speech-mpv-start.lock`, polling `_wait_for_prior_session()`, and killing previous sessions with `_kill_prior_session()` (`SIGTERM`/`SIGKILL`).
* **Obsolete Rationale:** `NativeAudioSink` plays WAVs synchronously via `subprocess.run(["mpv", "--really-quiet", ...])` in the daemon worker thread. Detached process lifecycles, IPC control sockets, startup polling, and kill loops are completely eliminated.
* **All References and Call Sites:**
  1. `plugin/scripts/python/mpv_controller.py`: The file itself.
  2. `plugin/scripts/python/pipeline.py`:
     - Line 23: `from mpv_controller import MpvController, MpvNotInstalledError, MpvStartupError`
     - Line 81: `MpvController().start(wav_path)`
  3. `plugin/scripts/python/short_path.py`:
     - Line 9: `from mpv_controller import MpvController, ...`
     - Line 48: `MpvController().start(wav_path)`
  4. `plugin/scripts/python/replay.py`:
     - Line 9: `from mpv_controller import MpvController, ...`
     - Line 61: `MpvController().start(wav_path)`
  5. `plugin/scripts/python/web_server.py`:
     - Line 74: `from mpv_controller import MpvController, ...`
     - Line 258: `self._mpv = MpvController()`
     - Line 430: `self._mpv.start(wav_path)` (in `_handle_replay`)
     - Line 764: `self._mpv.start(wav_path)` (in `_synthesize_and_play`)
  6. `plugin/scripts/python/narrator_service.py`:
     - Line 507: Mentioned in comment explaining race conditions with `speak.py`'s `MpvController`.
  7. `tests/test_mpv_wait.py`:
     - The entire test file (178 lines) specifically tests `MpvController._wait_for_prior_session()`, `MpvController.start()`, and `PlaybackStateMachine`.
  8. `tests/test_synthesize_endpoint.py`:
     - Line 53: `mock.patch("web_server.MpvController"):`
* **Blast Radius / Required Adjustments:**
  - Delete `mpv_controller.py`.
  - Delete or supersede `tests/test_mpv_wait.py` (replace with unit tests for `NativeAudioSink`).
  - Update `replay.py`: Use `NativeAudioSink` or `subprocess.run(["mpv", ...])`.
  - Update `web_server.py`: Replace `self._mpv` with `NativeAudioSink`.
  - Update `tests/test_synthesize_endpoint.py`: Update mock of `web_server.MpvController`.

---

### 2.5 Component 5: `SessionDir` PID Management / `session_dir.py`

* **Exact File Location:** `/Users/joshua/Developer/auto-speech/plugin/scripts/python/session_dir.py` (80 lines, `class SessionDir`).
* **Description:** Managed filesystem layout in `/tmp/auto-speech/`:
  - `_ROOT = Path("/tmp/auto-speech")`
  - `control.sock` (mpv IPC socket)
  - `mpv.pid` (PID of background mpv)
  - `wav.path` (path of WAV currently playing)
  - `started_at` (timestamp)
  - Methods: `clear()`, `write()`, `read_pid()`, `is_mpv_running()` (`ps -p <pid> -o comm=`).
* **Obsolete Rationale:** In the Unified Daemon, the daemon process is the single owner of audio hardware; playback is synchronous in the daemon's worker thread. Detached `mpv.pid` tracking and `SessionDir.is_mpv_running()` polling are obsolete hacks.
* **All References and Call Sites:**
  1. `plugin/scripts/python/session_dir.py`: Defines `SessionDir`.
  2. `plugin/scripts/python/mpv_controller.py`: Heavily uses `SessionDir` for socket, pid, running checks, clearing.
  3. `plugin/scripts/python/narrator_service.py`:
     - Lines 542–555:
       ```python
       from session_dir import SessionDir
       wav_path = SessionDir.wav_path_path().read_text().strip()
       ...
       pid = SessionDir.read_pid()
       os.kill(pid, _signal.SIGKILL)
       ```
     - Lines 563–595 in `_wait_mpv_idle()`: Reads `SessionDir.is_mpv_running()`, `SessionDir.socket_path()`, `SessionDir.read_pid()`, kills with `SIGTERM`/`SIGKILL`.
  4. `plugin/scripts/python/control.py`:
     - Lines 8, 18, 26, 63, 78: Uses `SessionDir.is_mpv_running()`, `SessionDir.socket_path()`, `SessionDir.clear()`.
  5. `plugin/scripts/python/web_server.py`:
     - Lines 81, 808, 811, 814, 824, 830, 856, 859, 867, 891: Uses `SessionDir.is_mpv_running()`, `SessionDir.socket_path()`, `SessionDir.clear()`.
  6. `plugin/scripts/python/autoplay_worker.py`:
     - Line 58: `MPV_PID_PATH = Path("/tmp/auto-speech/mpv.pid")`
     - Lines 93–105: `mpv_running()` checks if `MPV_PID_PATH` is alive.
  7. `plugin/scripts/python/dedup_guard.py`:
     - Line 28: `MPV_PID_PATH = Path("/tmp/auto-speech/mpv.pid")`
  8. `plugin/scripts/shell/autoplay_status.sh`:
     - Lines 53–56: `cat /tmp/auto-speech/mpv.pid`, `cat /tmp/auto-speech/wav.path`.
  9. `tests/test_dedup_guard.py`:
     - Lines 26, 31: Tests pass injected `mpv_pid_path`.
  10. `tests/test_mpv_wait.py`:
     - Defines `_FakeSessionDir` and monkeypatches `mpv_controller.SessionDir`.
* **Blast Radius / Required Adjustments:**
  - In `narrator_service.py`: Remove all `SessionDir` imports and usages. Remove `_wait_mpv_idle()`, WAV duration calculations (`time.sleep(duration + 0.5)`), and `SIGKILL` hacks.
  - Delete `session_dir.py`.
  - For `autoplay_worker.py` and `autoplay_status.sh`: Note that when the daemon owns audio, playback state can be probed from the daemon (or via daemon status) rather than reading `/tmp/auto-speech/mpv.pid`.

---

## 3. Thin Client IPC via UNIX Sockets

### 3.1 Current `speak.py` Analysis

* **Location:** `/Users/joshua/Developer/auto-speech/plugin/scripts/python/speak.py` (53 lines)
* **Command Line Interface:**
  - `--ordinal`: `int`, default `1`. 1-indexed N-from-end (logging/informational).
  - `--keep-artifacts`: `bool` flag (action="store_true"). Preserved tmpdir in legacy pipeline.
  - `--source-hash`: `str | None`, default `None`. Validated: must be 64 hexadecimal characters if supplied; exits with code `2` on validation error.
* **Input Handling:**
  - Reads entire transcript from stdin: `transcript_text = sys.stdin.read()`.
* **Execution Flow:**
  - Constructs `PipelineOrchestrator(keep_artifacts=args.keep_artifacts, source_hash=args.source_hash)`
  - Calls `orchestrator.run(transcript_text=transcript_text, turn_ordinal=args.ordinal)`
* **Exit Codes:**
  - `0`: Success (`EXIT_OK`)
  - `2`: Command line validation error (invalid `--source-hash` or argparse error)
  - `4`: `EXIT_REWRITE_FAIL` (empty/whitespace-only transcript)
  - `5`: `EXIT_TTS_FAIL` (Kokoro model generation failed)
  - `6`: `EXIT_PLAYBACK_FAIL` (mpv startup failed or mpv not installed)
  - `130`: `EXIT_INTERRUPTED` (SIGINT / KeyboardInterrupt)

---

### 3.2 Target Thin Client Specification (`speak.py`)

* **Role:** A lightweight CLI client that reads `stdin` and forwards text over a UNIX domain socket to the daemon.
* **Socket Path:** `/tmp/auto-speech-daemon.sock` (configurable via `AUTO_SPEECH_DAEMON_SOCKET` environment variable for test isolation).
* **CLI Arguments Contract (Backward Compatibility):**
  - Retain `--ordinal [N]` (optional, default 1) to avoid breaking callers (`auto-speech-speak.md`, scripts).
  - Retain `--keep-artifacts` (optional flag, no-op or forwarded).
  - Retain `--source-hash [HEX]` (optional, 64 hex chars, validated).
* **Stdin Processing:**
  - Read `sys.stdin.read()`.
  - Strip whitespace. If empty:
    - If `--source-hash` is provided (potential cache hit), allow empty string (or handle per protocol).
    - Otherwise, emit error to stderr: `speak: empty transcript text` and exit with code `4` (preserving existing contract).
* **Socket Connection & Wire Protocol:**
  1. Create `socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)`.
  2. Set connection timeout (e.g. 5.0 seconds).
  3. Connect to `/tmp/auto-speech-daemon.sock`.
  4. Frame message:
     - **Protocol Framing:** UTF-8 encoded text payload. To support metadata cleanly, use a single-line JSON envelope terminated by newline `\n`:
       ```json
       {"text": "<transcript>", "source_hash": "<hash_or_null>", "ordinal": 1}\n
       ```
       *(Alternatively, if raw text is sent: client sends text followed by `sock.shutdown(socket.SHUT_WR)`).*
       **Recommended Framing:** JSON line (`{"text": ...}\n`). This allows future extension (voice overrides, source hashes) while keeping the client thin and single-packet.
  5. Read response from daemon:
     - Daemon replies with `OK\n` or `{"status": "ok"}\n`.
  6. Close socket and exit `0`.
* **Error Handling & Exit Codes:**
  - Daemon socket not found (`FileNotFoundError` / `ENOENT`) or connection refused (`ConnectionRefusedError` / `ECONNREFUSED`):
    - Stderr: `speak: auto-speech daemon is not running (socket /tmp/auto-speech-daemon.sock unavailable)`
    - Exit code: `1` (or `6` if mapped to legacy playback failure)
  - Socket timeout / disconnect:
    - Stderr: `speak: daemon did not acknowledge request within timeout`
    - Exit code: `1`
  - Empty input without source-hash:
    - Exit code: `4`
  - Bad arguments:
    - Exit code: `2`

---

### 3.3 Target Daemon Socket Server Specification (`narrator_service.py`)

* **Implementation:** Use Python standard library `socketserver.ThreadingUnixStreamServer` (or `UnixStreamServer`) running on a dedicated daemon thread.
* **Socket Lifecycle:**
  - Path: `/tmp/auto-speech-daemon.sock` (or environment override).
  - **Pre-bind cleanup:** Before binding, remove stale socket file if it exists (`unlink(missing_ok=True)`), preventing `Address already in use` (`OSError: [Errno 48]`).
  - **Permissions:** Set permissions on socket file (`os.chmod(path, 0o600)`) so only the current user can communicate with the daemon.
  - **Shutdown cleanup:** When `NarratorService` shuts down (via signal, idle timeout, or exception), call `server.shutdown()`, `server.server_close()`, and unlink the socket file.
* **Request Handler (`socketserver.StreamRequestHandler`):**
  1. Reads request from `self.rfile`:
     - Parse line or read up to EOF.
     - Extract `text` (either from JSON envelope `data["text"]` or raw string).
  2. Validate non-empty text.
  3. Enqueue to `self._tts_queue`:
     - Put `text` (or `{type: "Speak", text: ...}`) into `self._tts_queue`.
     - Queue backpressure: use `_enqueue_phase()` or bounded put with drop-oldest policy if queue depth is exceeded (`self._max_queue`).
  4. Write acknowledgment to `self.wfile`: `b"OK\n"`.
* **In-Process `TTSEngine` and `NativeAudioSink` Integration:**
  - `TTSEngine` is initialized once in `NarratorService.__init__()`:
    ```python
    self._tts = TTSEngine()
    self._sink = NativeAudioSink()
    self._voice_profile = _load_voice_profile()
    ```
  - Eager prewarm at boot: Call `self._tts._ensure_loaded()` during startup so the first speech request has zero model-load latency.
  - In `_tts_worker()`:
    ```python
    while not self._stop.is_set():
        item = self._tts_queue.get()
        if item is None:
            break
        text_to_speak = item if isinstance(item, str) else ...
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            wav_path = Path(tf.name)
        try:
            self._tts.synthesize(text_to_speak, self._voice_profile, wav_path)
            self._sink.play(wav_path)
        finally:
            wav_path.unlink(missing_ok=True)
            self._tts_queue.task_done()
    ```
* **`NativeAudioSink` Class Specification:**
  ```python
  class NativeAudioSink:
      """Synchronous blocking audio playback via mpv."""
      def play(self, wav_path: Path) -> int:
          proc = subprocess.run(
              ["mpv", "--really-quiet", "--no-video", str(wav_path)],
              check=False,
              stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL,
          )
          return proc.returncode
  ```
  - **Blocking Guarantees:** `subprocess.run` blocks until `mpv` exits upon reaching EOF of the WAV file.
  - **Sequential Order:** Because items are processed one by one in `_tts_worker()`, requests never overlap or interrupt each other.
  - **No Detached Processes:** No background `Popen`, no detached PID files, and no lingering background processes after playback completes.

---

## 4. Existing Test Suites & Test Harness

### 4.1 Test Harness & Execution Methods

The codebase has two distinct test execution paradigms:

1. **`tests/run_all.sh` (Custom Bash Test Runner — Authoritative for Project):**
   - Does NOT depend on pytest.
   - Executes Python test files directly with `$PYTHON "$path"`.
   - Each Python test file defines standard test functions, runs them in a `main()` function, and exits with code 0 or 1.
   - Modes:
     - `bash tests/run_all.sh`: Runs entire suite (CHEAP $\to$ SHELL_TESTS $\to$ WEB $\to$ HEAVY).
     - `bash tests/run_all.sh --hermetic`: Runs only tests with zero runtime dependencies (skips MLX/Flask/audio).
     - `bash tests/run_all.sh --web`: Runs web-server tests requiring Flask.
   - Interpreter selection: Defaults to `.venv/bin/python`. Overridable via `AUTO_SPEECH_TEST_PYTHON`.

2. **`uv run pytest` (Pytest Runner):**
   - Pytest 9.0.2 is available via `uv run pytest`.
   - `pyproject.toml` contains `[project]` and `[tool.ruff]`, but no dedicated `[tool.pytest.ini_options]`.
   - Tests with standalone function names matching `test_*()` can be run by pytest.

---

### 4.2 Comprehensive Inventory of Test Files (41 Total)

| Category in `run_all.sh` | File Name | Type | Description |
|---|---|---|---|
| **CHEAP** | `tests/test_state_machine.py` | Python | Generic finite state machine transitions |
| **CHEAP** | `tests/test_auto_speech_log.py` | Python | Structured log rotation and writing |
| **CHEAP** | `tests/test_doctor.py` | Python | Diagnostic checks for system dependencies |
| **CHEAP** | `tests/test_config_validation.py` | Python | Configuration file schemas |
| **CHEAP** | `tests/test_self_update.py` | Python | Dependency lock hash checks |
| **CHEAP** | `tests/test_playback_state.py` | Python | Playback state transitions |
| **CHEAP** | `tests/test_narrator_state.py` | Python | Daemon lifecycle state transitions |
| **CHEAP** | `tests/test_worker_lifecycle.py` | Python | Worker lifecycle state machine |
| **CHEAP** | `tests/test_staleness_beacon.py` | Python | Mtime staleness beacon checking |
| **CHEAP** | `tests/test_playback_fifo.py` | Python | File-based FIFO ticketing |
| **CHEAP** | `tests/test_dedup_guard.py` | Python | Playback deduplication guard |
| **CHEAP** | `tests/test_autoplay_gate.py` | Python | Global mute gating |
| **CHEAP** | `tests/test_autoplay_scope.py` | Python | Spotlight / session filtering |
| **CHEAP** | `tests/test_autoplay_enrollment.py` | Python | Session opt-in marker management |
| **CHEAP** | `tests/test_autoplay_worker.py` | Python | Autoplay worker coordination & runner |
| **CHEAP** | `tests/test_say_worker.py` | Python | Say worker detached execution |
| **CHEAP** | `tests/test_mcp_server.py` | Python | MCP speak tool protocol |
| **CHEAP** | `tests/test_job_tracker.py` | Python | Web app background job state |
| **CHEAP** | `tests/test_narrator_phase_classifier.py` | Python | Tool event categorization |
| **CHEAP** | `tests/test_narrator_summarizer.py` | Python | Mock and factory summarizers |
| **CHEAP** | `tests/test_narrator_config.py` | Python | Narrator TOML config parsing |
| **CHEAP** | `tests/test_autoplay_config.py` | Python | Autoplay TOML config parsing |
| **CHEAP** | `tests/test_message_selector.py` | Python | JSONL transcript message filtering |
| **CHEAP** | `tests/test_transcript_locator.py` | Python | Slug directory transcript resolution |
| **CHEAP** | `tests/test_fibonacci.py` | Python | Fibonacci interval arithmetic |
| **CHEAP** | `tests/test_cli_rewrite.py` | Python | Claude CLI rewriter argument construction |
| **CHEAP** | `tests/test_mpv_wait.py` | Python | **Tests `MpvController` & `SessionDir` (Target for deletion)** |
| **CHEAP** | `tests/test_mlx_summarizer.py` | Python | MLX summarizer prompt template |
| **CHEAP** | `tests/test_narrator_service.py` | Python | Narrator daemon PID & event tailing |
| **SHELL** | `tests/test_autoplay_shim.sh` | Bash | Bash worker shim arg contracts |
| **SHELL** | `tests/test_narrator_hook.sh` | Bash | Hook event generation & gating |
| **SHELL** | `tests/test_install_idempotent.sh` | Bash | Setup script idempotency |
| **SHELL** | `tests/test_install_plugin.sh` | Bash | Plugin registration in Claude config |
| **SHELL** | `tests/test_bootstrap_hook_idempotent.sh` | Bash | Hook installer idempotency |
| **SHELL** | `tests/test_deps_locked.sh` | Bash | Verifies `uv.lock` matches `pyproject.toml` |
| **WEB** | `tests/test_synthesize_endpoint.py` | Python | `/api/synthesize` and `/api/speak` endpoints |
| **HEAVY** | `tests/test_chunk_planner.py` | Python | Text chunking on sentence boundaries |
| **HEAVY** | `tests/test_wav_concat.py` | Python | WAV frame concatenation |
| **HEAVY** | `tests/test_resilient_synthesizer.py` | Python | Resilient Kokoro chunk splitting |
| **HEAVY** | `tests/test_segment_producer.py` | Python | Background TTS generation thread |
| **HEAVY** | `tests/test_playback_consumer.py` | Python | Playback consumer loop |

---

### 4.3 How Tests Currently Exercise `speak.py` and `narrator_service.py`

* **`speak.py` Coverage:**
  - **Zero Direct Unit Tests:** There is no `test_speak.py` in the test suite.
  - **Indirect/Mocked Usage Only:**
    - `test_autoplay_worker.py`: Uses a mock runner that intercepts command line arguments ending with `"run_speak.sh"`.
    - `test_say_worker.py`: Mocks `self._runner` to capture commands executed by `SayWorker`.
    - `test_synthesize_endpoint.py`: Tests `/api/speak` on `WebServer` by mocking `web_server.PipelineOrchestrator`.
  - **Finding:** A dedicated unit test suite for `speak.py` (e.g. `tests/test_speak_client.py`) is urgently needed to test thin client argument handling, socket connection, daemon communication, error handling, and exit codes.

* **`narrator_service.py` Coverage:**
  - `tests/test_narrator_service.py` contains 15 tests:
    1. `test_existing_pid_returns_none_when_pid_file_absent`
    2. `test_existing_pid_returns_none_for_dead_pid`
    3. `test_existing_pid_reclaims_live_non_daemon_pid`
    4. `test_pid_is_our_daemon_true_for_matching_cmdline`
    5. `test_pid_is_our_daemon_false_for_recycled_pid`
    6. `test_pid_is_our_daemon_false_for_dead_pid`
    7. `test_sweep_removes_old_session_markers_but_not_fresh_ones`
    8. `test_sweep_tolerates_missing_dirs`
    9. `test_process_chunk_filters_events_without_session_marker`
    10. `test_process_chunk_suppresses_stop_event_flush`
    11. `test_enqueue_under_cap_keeps_all`
    12. `test_enqueue_over_cap_drops_oldest`
    13. `test_enqueue_into_zero_free_slots_is_bounded`
    14. `test_tail_resumes_a_line_split_across_two_reads`
    15. `test_config_max_queue_depth_default_and_override`
  - **Critical Coverage Gaps:**
    - `_speak()` is completely untested.
    - `_tts_worker()` queue draining is completely untested.
    - Audio playback and hardware synchronization are completely untested.
    - UNIX domain socket server is completely untested.

---

### 4.4 Tests Broken by Sprawl Removal & Baseline Failures Observed

#### A. Tests Directly Impacted by Sprawl Removal
1. `tests/test_mpv_wait.py`:
   - Contains 5 tests verifying `MpvController._wait_for_prior_session()`, startup lock behavior, and `_FakeSessionDir`.
   - **Action:** Delete this test file when `MpvController` and `SessionDir` are removed; remove from `CHEAP` list in `tests/run_all.sh`.
2. `tests/test_autoplay_worker.py`:
   - Line 155 checks `if str(prog).endswith("run_speak.sh"):`.
   - **Action:** Update to `str(prog).endswith("speak.py")`.
3. `tests/test_synthesize_endpoint.py`:
   - Lines 53 and 258 patch `web_server.MpvController` and `web_server.PipelineOrchestrator`.
   - **Action:** Update mocks to reflect the refactored `web_server.py`.

#### B. Pre-Existing Baseline Failures Observed
During baseline execution of `bash tests/run_all.sh --hermetic`, 3 test failures were observed:
1. `tests/test_narrator_service.py:306`:
   - Error: `AttributeError: 'NarratorService' object has no attribute '_classifier'` in `narrator_service.py:237`.
   - Root Cause: `test_tail_resumes_a_line_split_across_two_reads` constructs `NarratorService.__new__(NarratorService)` without calling `__init__()`. When line 237 executes `if hasattr(self._classifier, "_current"):`, accessing `self._classifier` raises `AttributeError`.
2. `tests/test_narrator_phase_classifier.py:44`:
   - Error: `AssertionError` in `test_category_change_closes_phase`.
   - Root Cause: `PhaseClassifier.__init__` has `max_events_per_phase=1` as default, closing phases after 1 event rather than accumulating across tool calls.
3. `tests/test_mlx_summarizer.py:121`:
   - Error: `AssertionError: assert "category=edit" in rendered`.
   - Root Cause: Mismatch in prompt template expectations between test and implementation.

---

## 5. Features Discovered

| # | Category | Feature | Description | Inputs | Outputs | Error Behavior | Discovered Via |
|---|---|---|---|---|---|---|---|
| F-01 | Audio Sink | `NativeAudioSink.play` | Synchronous audio playback using `subprocess.run` calling `mpv` | `wav_path: Path` | `exit_code: int` (0 on success) | Non-zero exit code propagated; raises `FileNotFoundError` if WAV missing | ORIGINAL_REQUEST.md R1 |
| F-02 | TTS Engine | In-process `TTSEngine` | MLX Kokoro TTS engine loaded once at daemon boot, generating WAV audio in memory/disk | `text: str`, `profile: VoiceProfile`, `out_path: Path` | WAV audio file | Raises `TTSGenerationError` / `TTSNoSpeakableContentError` | ORIGINAL_REQUEST.md R1, `tts_engine.py` |
| F-03 | Thin Client CLI | `speak.py` CLI Client | Thin CLI that reads text from stdin and forwards it to the daemon socket | `sys.stdin`, optional `--ordinal`, `--source-hash` | Exit code 0 on success | Code 2 on bad args, Code 4 on empty text, Code 1 on daemon offline | ORIGINAL_REQUEST.md R2, `speak.py` |
| F-04 | Daemon IPC | UNIX Socket Server | `ThreadingUnixStreamServer` listening on `/tmp/auto-speech-daemon.sock` | Incoming UNIX socket connection with text payload | Acknowledgment `OK\n` | Drops oldest if queue depth exceeded; logs error | ORIGINAL_REQUEST.md R2, `narrator_service.py` |
| F-05 | Queue Ingestion | Unified `_tts_queue` | Enqueues external speech requests alongside internal narrator tool phase summaries | Text string or phase object | Enqueued in FIFO order | Backpressure drops oldest when queue length exceeds `max_queue_depth` | `narrator_service.py:123,428` |
| F-06 | Slash Command | `/auto-speech-speak` | Slash command reading prior Claude response and invoking `speak.py` | `$ARGUMENTS` (ordinal integer) | Speaks message aloud | Returns error line if ordinal invalid or extraction/speak fails | `plugin/commands/auto-speech-speak.md` |
| F-07 | Worker Client | `autoplay_worker._speak` | End-of-turn autoplay worker calling speech client | `source_hash: str`, `stdin_text: str` | Audio playback via daemon | Logs exit code on failure, updates FSM to DONE | `autoplay_worker.py:386` |
| F-08 | Worker Client | `say_worker.run` | MCP `speak` tool worker executing speech client verbatim | Path to temporary text file | Audio playback via daemon | Removes text file; returns 0 | `say_worker.py:92` |
| F-09 | Web Server | `web_server._synthesize_and_play` | Localhost web app `/api/speak` endpoint running TTS | JSON payload `{text, ...}` | Audio playback | Returns 400 on empty text, 202 on accepted job | `web_server.py:700` |
| F-10 | Test Harness | `run_all.sh` Test Runner | Standalone shell test runner supporting hermetic, web, and full modes | CLI flags (`--hermetic`, `--web`) | Exit code 0 if all pass, 1 if any fail | Emits list of failed tests | `tests/run_all.sh` |

---

## 6. Edge Cases & Observed Behaviors

| # | Feature | Input / Condition | Observed / Required Behavior |
|---|---|---|---|
| E-01 | Thin Client | Stdin is completely empty or whitespace | Client prints `speak: empty transcript text` to stderr and exits with code `4`. Socket connection is not initiated. |
| E-02 | Thin Client | Daemon socket missing or connection refused | Client catches `FileNotFoundError` / `ConnectionRefusedError`, prints informative error (`speak: auto-speech daemon is not running...`), and exits with code `1`. |
| E-03 | Thin Client | Stdin contains large text (e.g. 50 KB transcript) | Client streams / writes full text across socket. Server reads until EOF or newline framing without truncation. |
| E-04 | Socket Server | Stale socket file exists at daemon startup | Server unlinks `/tmp/auto-speech-daemon.sock` prior to `server_bind()`, avoiding `Address already in use` error. |
| E-05 | Socket Server | Daemon receives SIGTERM / SIGINT | Server cleanly closes socket, unlinks socket file from filesystem, and drains worker thread within grace timeout. |
| E-06 | Socket Server | Concurrent `speak.py` clients connect simultaneously | `ThreadingUnixStreamServer` handles client connections concurrently; requests are queued sequentially in `_tts_queue` in arrival order. |
| E-07 | `_tts_queue` | Burst of requests exceeds `max_queue_depth` (32) | Backpressure mechanism sheds oldest item, increments `_dropped_phases`, logs warning, and enqueues new request. |
| E-08 | Audio Sink | `mpv` binary missing from PATH | `subprocess.run` raises `FileNotFoundError`; caught, logged, returns exit code 6 (`EXIT_PLAYBACK_FAIL`). |
| E-09 | Audio Sink | WAV playback finishes naturally | `subprocess.run` unblocks cleanly; temporary WAV file is unlinked; no orphan `mpv` process remains. |
| E-10 | Audio Sink | Playback requested while previous audio is playing | Worker thread is blocked on `subprocess.run` of previous WAV; new item waits in `_tts_queue` until previous audio completes (FIFO, no cut-off, no overlapping audio). |

---

## 7. Migration & Implementation Recommendations for Orchestrator

1. **Delete Dead Sprawl First:**
   - Remove `plugin/scripts/shell/run_speak.sh`.
   - Remove `plugin/scripts/python/pipeline.py`.
   - Remove `plugin/scripts/python/short_path.py`.
   - Remove `plugin/scripts/python/mpv_controller.py`.
   - Remove `plugin/scripts/python/session_dir.py`.
   - Remove `tests/test_mpv_wait.py`.
   - Remove `test_mpv_wait.py` from `CHEAP` array in `tests/run_all.sh`.

2. **Implement `NativeAudioSink` & In-Process `TTSEngine` in `narrator_service.py`:**
   - Add `NativeAudioSink` to `narrator_service.py` (or a dedicated helper module `audio_sink.py`).
   - Instantiate `TTSEngine` in `NarratorService.__init__()` and eagerly load during boot.
   - Replace legacy `_speak()` method (which called `run_speak.sh`, `wave.open`, `time.sleep`, and `os.kill`) with in-process `self._tts.synthesize()` + `self._sink.play()`.

3. **Implement Socket Server in `narrator_service.py`:**
   - Add `socketserver.ThreadingUnixStreamServer` on `/tmp/auto-speech-daemon.sock`.
   - Launch in a background thread in `NarratorService.run()`.
   - Handler reads text and puts it into `self._tts_queue`.
   - Ensure clean socket teardown on daemon stop.

4. **Rewrite `speak.py` as Thin Client:**
   - Remove legacy `PipelineOrchestrator` imports and calls.
   - Read stdin, connect to `/tmp/auto-speech-daemon.sock`, send text, receive ACK, and exit.

5. **Update Surviving Callers:**
   - `auto-speech-speak.md`: Call `speak.py` directly with Python.
   - `autoplay_worker.py`: Update `SPEAK` constant to point to `speak.py`.
   - `say_worker.py`: Update runner invocation to use `speak.py`.
   - `web_server.py`: Update references to `PipelineOrchestrator`, `ShortPathStrategy`, and `MpvController`.

6. **Add New Test Suites:**
   - Add `tests/test_speak_client.py` to test `speak.py` thin client.
   - Add `tests/test_audio_sink.py` to test `NativeAudioSink`.
   - Add socket integration tests in `tests/test_narrator_service.py`.
