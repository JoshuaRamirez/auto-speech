# Handoff Report: Specification Mining Survey 3

**Agent:** `spec_miner_survey_3`  
**Working Directory:** `/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_survey_3`  
**Report Document:** `/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_survey_3/analysis.md`

---

## 1. Observation

1. **Dead Code Files & Locations:**
   - `plugin/scripts/shell/run_speak.sh` (21 lines): Executable bash wrapper activating `.venv` and execing `plugin/scripts/python/speak.py`. Referenced in `plugin/commands/auto-speech-speak.md:63`, `plugin/scripts/python/autoplay_worker.py:52`, `plugin/scripts/python/say_worker.py:27`, `plugin/scripts/python/narrator_service.py:110`, and `tests/test_autoplay_worker.py:155`.
   - `plugin/scripts/python/pipeline.py` (322 lines): Defines `PipelineOrchestrator` (lines 94–322) and constants `EXIT_OK` (34), `EXIT_REWRITE_FAIL` (35), `EXIT_TTS_FAIL` (36), `EXIT_PLAYBACK_FAIL` (37), `EXIT_INTERRUPTED` (38). Imported by `plugin/scripts/python/speak.py:14`, `plugin/scripts/python/web_server.py:78`, and mocked in `tests/test_synthesize_endpoint.py:258`.
   - `plugin/scripts/python/short_path.py` (53 lines): Defines `ShortPathStrategy`. Imported by `plugin/scripts/python/pipeline.py:26` and `plugin/scripts/python/web_server.py:82`.
   - `plugin/scripts/python/mpv_controller.py` (240 lines): Defines `MpvController`, `MpvNotInstalledError`, `MpvStartupError`, and `_MPV_START_LOCK_PATH` (`/tmp/auto-speech-mpv-start.lock`). Imported by `pipeline.py:23`, `short_path.py:9`, `replay.py:9`, `web_server.py:74`, and tested by `tests/test_mpv_wait.py:20`.
   - `plugin/scripts/python/session_dir.py` (80 lines): Defines `SessionDir` managing `/tmp/auto-speech/` (`control.sock`, `mpv.pid`, `wav.path`, `started_at`). Imported by `mpv_controller.py:16`, `control.py:8`, `web_server.py:81`, `narrator_service.py:542,563`, and read directly in `autoplay_status.sh:53`.

2. **Existing `speak.py` Implementation:**
   - `plugin/scripts/python/speak.py` is 53 lines.
   - Arguments: `--ordinal` (int, default 1), `--keep-artifacts` (action="store_true"), `--source-hash` (64 hex characters, validated at lines 34–41).
   - Input: Reads `sys.stdin.read()`.
   - Action: `PipelineOrchestrator(keep_artifacts=args.keep_artifacts, source_hash=args.source_hash).run(transcript_text=transcript_text, turn_ordinal=args.ordinal)`.
   - Returns orchestrator exit code.

3. **Current Playback in `narrator_service.py`:**
   - `narrator_service.py` lines 520–560 execute:
     ```python
     speak = _speak_script()  # run_speak.sh
     proc = subprocess.run([str(speak), "--keep-artifacts"], input=line.encode("utf-8"), ...)
     ...
     wav_path = SessionDir.wav_path_path().read_text().strip()
     with wave.open(wav_path, "r") as f:
         duration = f.getnframes() / float(f.getframerate())
     time.sleep(duration + 0.5)
     pid = SessionDir.read_pid()
     if pid:
         os.kill(pid, _signal.SIGKILL)
     ```
   - Uses `_wait_mpv_idle()` polling `SessionDir.is_mpv_running()` and `MpvIpc.send(["get_property", "eof-reached"], socket_path)` with fallback to `os.kill(pid, signal.SIGKILL)`.

4. **Test Suite Execution & Status:**
   - Test runner: `tests/run_all.sh` executes 41 test files (35 Python, 6 Bash).
   - Baseline execution: `bash tests/run_all.sh --hermetic` executed 31 test files, 28 passed, 3 failed:
     - `tests/test_narrator_service.py:306`: `AttributeError: 'NarratorService' object has no attribute '_classifier'` at `narrator_service.py:237`.
     - `tests/test_narrator_phase_classifier.py:44`: `AssertionError` in `test_category_change_closes_phase` due to `max_events_per_phase=1` default.
     - `tests/test_mlx_summarizer.py:121`: `AssertionError: assert "category=edit" in rendered`.
   - `tests/test_mpv_wait.py` passed (5/5) but is completely dedicated to `MpvController._wait_for_prior_session()`.
   - `tests/test_synthesize_endpoint.py` passed (12/12) but mocks `web_server.PipelineOrchestrator` and `web_server.MpvController`.
   - There are currently ZERO direct unit tests for `speak.py`.

---

## 2. Logic Chain

1. **Dead Sprawl Deletion:**
   - Per Observation 1 and 3, `run_speak.sh`, `PipelineOrchestrator`, `ShortPathStrategy`, `MpvController`, and `SessionDir` exist solely to coordinate multi-process, detached playback and pass audio files across process boundaries via `/tmp/auto-speech`.
   - When `narrator_service.py` is refactored into the Unified Daemon holding `TTSEngine` and `NativeAudioSink` in-process (Observation 3), all multi-process detached session coordination is obsolete.
   - Therefore, deleting `run_speak.sh`, `pipeline.py`, `short_path.py`, `mpv_controller.py`, `session_dir.py`, and `test_mpv_wait.py` eliminates the architectural sprawl.
   - However, because surviving files (`web_server.py`, `replay.py`, `control.py`, `autoplay_worker.py`, `say_worker.py`, `auto-speech-speak.md`, `test_autoplay_worker.py`, `test_synthesize_endpoint.py`) import or invoke these components (Observation 1), they must be adapted during or immediately following deletion.

2. **Thin Client IPC:**
   - Per Observation 2, `speak.py` currently loads the full `PipelineOrchestrator` in-process.
   - Under R2, `speak.py` must become a thin client reading `stdin` and sending the text over a UNIX domain socket (`/tmp/auto-speech-daemon.sock`).
   - Retaining CLI arguments (`--ordinal`, `--source-hash`, `--keep-artifacts`) ensures existing callers (`auto-speech-speak.md`, `autoplay_worker.py`) do not fail with CLI argument parsing errors.
   - In `narrator_service.py`, running a `socketserver.ThreadingUnixStreamServer` background thread accepting connections, extracting text, and enqueueing to `self._tts_queue` allows both external requests and internal tool event narrations to share the single FIFO playback loop.

3. **Test Suite Realignment:**
   - Per Observation 4, `tests/run_all.sh` tracks tests in explicit arrays (`CHEAP`, `SHELL_TESTS`, `WEB`, `HEAVY`).
   - Removing `test_mpv_wait.py` requires removing it from `CHEAP` in `run_all.sh`.
   - Creating `tests/test_speak_client.py` and adding it to `run_all.sh` closes the zero-coverage gap for `speak.py`.
   - Updating `tests/test_autoplay_worker.py:155` prevents regression failures when `run_speak.sh` is removed.

---

## 3. Caveats

1. **`web_server.py` Architectural Boundary:**
   `web_server.py` currently has its own in-process `TTSEngine` instance and historically used `PipelineOrchestrator` and `MpvController`. The orchestrator must decide whether `web_server.py` communicates with the unified daemon via `/tmp/auto-speech-daemon.sock` or directly uses `NativeAudioSink`.
2. **`control.py` Slash Commands:**
   `control.py` (which powers `/auto-speech-pause`, `/auto-speech-resume`, `/auto-speech-seek`, `/auto-speech-end`) relies on `SessionDir.socket_path()` and `MpvIpc`. When `mpv` is executed synchronously per-utterance via `subprocess.run`, individual 2–5 second tool narrations do not have a persistent IPC socket server.
3. **Pre-existing Failures:**
   The 3 failing tests identified in Observation 4 are independent bugs in the current tree and should not be confused with regressions caused by the daemon refactor.

---

## 4. Conclusion

1. **Delete Dead Sprawl:** Remove 5 target components (`run_speak.sh`, `pipeline.py`, `short_path.py`, `mpv_controller.py`, `session_dir.py`) and 1 test file (`tests/test_mpv_wait.py`).
2. **Implement Thin Client & Socket Server:**
   - Refactor `speak.py` into a thin socket client targeting `/tmp/auto-speech-daemon.sock`.
   - Add `socketserver.ThreadingUnixStreamServer` to `narrator_service.py`, enqueueing to `self._tts_queue`.
   - Add `NativeAudioSink` (blocking `subprocess.run(["mpv", "--really-quiet", ...])`) and in-process `TTSEngine` in `narrator_service.py`.
3. **Update Callers:** Adapt `auto-speech-speak.md`, `autoplay_worker.py`, `say_worker.py`, `web_server.py`, and `test_autoplay_worker.py`.

---

## 5. Verification Method

1. **Verify Sprawl Removal:**
   ```bash
   # Confirm files are deleted:
   test ! -f plugin/scripts/shell/run_speak.sh
   test ! -f plugin/scripts/python/pipeline.py
   test ! -f plugin/scripts/python/short_path.py
   test ! -f plugin/scripts/python/mpv_controller.py
   test ! -f plugin/scripts/python/session_dir.py
   test ! -f tests/test_mpv_wait.py
   ```
2. **Verify No Dangling Imports:**
   ```bash
   grep -rn "PipelineOrchestrator" plugin/ tests/ || echo "clean"
   grep -rn "ShortPathStrategy" plugin/ tests/ || echo "clean"
   grep -rn "MpvController" plugin/ tests/ || echo "clean"
   grep -rn "SessionDir" plugin/ tests/ || echo "clean"
   grep -rn "run_speak.sh" plugin/ tests/ || echo "clean"
   ```
3. **Verify Thin Client Socket IPC:**
   ```bash
   # Start daemon
   bash plugin/scripts/shell/narrator_service_start.sh
   # Verify socket creation
   test -S /tmp/auto-speech-daemon.sock
   # Send test utterance
   echo "test phrase" | python3 plugin/scripts/python/speak.py
   # Stop daemon
   bash plugin/scripts/shell/narrator_service_stop.sh
   test ! -S /tmp/auto-speech-daemon.sock
   ```
4. **Run Test Suites:**
   ```bash
   bash tests/run_all.sh --hermetic
   bash tests/run_all.sh
   ```
