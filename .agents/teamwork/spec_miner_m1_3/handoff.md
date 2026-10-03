# Handoff Report: NarratorService Legacy Hacks, Bug Fix, and Unit Testing Specification (M1)

## 1. Observation
1. In `plugin/scripts/python/narrator_service.py`:
   - Lines 105–110:
     ```python
     def _project_root() -> Path:
         return Path(__file__).resolve().parents[3]


     def _speak_script() -> Path:
         return _project_root() / "plugin" / "scripts" / "shell" / "run_speak.sh"
     ```
   - Lines 316–350:
     ```python
     316:                 # Immediately interrupt any currently playing audio so the user isn't talked over!
     317:                 import subprocess
     318:                 subprocess.run(["pkill", "-9", "mpv"], capture_output=True)
     319:                 
     320:                 self._classifier.flush(session_id)
     321:                 self._phases_this_turn = 0
     322:                 try:
     323:                     summarizer = self._get_summarizer()
     324:                     if hasattr(summarizer, "generate_conversational"):
     325:                         history = ev.get("payload", {}).get("conversation_history", "")
     326:                         words = summarizer.generate_conversational(history, "UserPromptSubmit", session_id=session_id)
     327:                         if words:
     328:                             self._tts_queue.put(words)
     329:                 except Exception as e:
     330:                     _log(f"Failed to generate start words: {e}")
     331:                 continue
     332:                     
     333:                 # Immediately interrupt any currently playing audio so the user isn't talked over!
     334:                 subprocess.run(["pkill", "-9", "mpv"], capture_output=True)
     335:                 
     336:                 self._classifier.flush(session_id)
     337:                 self._phases_this_turn = 0
     338:                 try:
     339:                     summarizer = self._get_summarizer()
     340:                     if hasattr(summarizer, "generate_conversational"):
     341:                         history = ev.get("payload", {}).get("conversation_history", "")
     342:                         _log(f"Calling generate_conversational for UserPromptSubmit with history length {len(history)}")
     343:                         words = summarizer.generate_conversational(history, "UserPromptSubmit", session_id=session_id)
     344:                         _log(f'OUTPUT: {words}')
     345:                         if words:
     346:                             self._tts_queue.put(words)
     347:                 except Exception as e:
     348:                     _log(f"Failed to generate start words: {e}")
     349:                 continue
     ```
   - Lines 518–520:
     ```python
     518:         # PRE: don't interrupt an in-progress play. Wait indefinitely (up to 10m) 
     519:         # for long end-of-turn chat responses to finish.
     520:         self._wait_mpv_idle(max_seconds=600.0, initial_sleep=0.0)
     ```
   - Lines 522–536:
     ```python
     522:         speak = _speak_script()
     ...
     530:         proc = subprocess.run(
     531:             [str(speak), "--keep-artifacts"],
     532:             input=line.encode("utf-8"),
     533:             capture_output=True,
     534:             timeout=120,
     535:             env=env,
     536:         )
     ```
   - Lines 540–561:
     ```python
     540:         # POST: wait for our mpv to finish before pulling the next phase.
     541:         try:
     542:             from session_dir import SessionDir
     543:             import wave
     544:             import signal as _signal
     545:             wav_path = SessionDir.wav_path_path().read_text().strip()
     546:             with wave.open(wav_path, "r") as f:
     547:                 duration = f.getnframes() / float(f.getframerate())
     548:             
     549:             _log(f"sleeping for wav duration: {duration:.2f}s")
     550:             time.sleep(duration + 0.5)  # slight buffer
     551:             
     552:             pid = SessionDir.read_pid()
     553:             if pid:
     554:                 try:
     555:                     os.kill(pid, _signal.SIGKILL)
     556:                 except ProcessLookupError:
     557:                     pass
     558:         except Exception as e:
     559:             _log(f"failed to sleep for duration: {e}")
     560:             self._wait_mpv_idle(max_seconds=15.0, initial_sleep=0.3)
     ```
   - Lines 562–597: Complete `_wait_mpv_idle(self, max_seconds: float, initial_sleep: float = 0.3)` method definition using `mpv_ipc`, `SessionDir`, and `os.kill(pid, signal.SIGKILL)`.
   - Line 237:
     ```python
     237:             if hasattr(self._classifier, "_current") and self._classifier._current:
     ```

2. Test execution:
   Running `pytest tests/test_narrator_service.py` failed at line 306 with verbatim output:
   ```
   FAILED tests/test_narrator_service.py::test_tail_resumes_a_line_split_across_two_reads
   ...
   self = <narrator_service.NarratorService object at 0x109bae210>
   ...
   >           if hasattr(self._classifier, "_current") and self._classifier._current:
                          ^^^^^^^^^^^^^^^^
   E           AttributeError: 'NarratorService' object has no attribute '_classifier'
   plugin/scripts/python/narrator_service.py:237: AttributeError
   ========================= 1 failed, 14 passed in 0.05s =========================
   ```

## 2. Logic Chain
1. **Legacy Subprocess Sprawl**:
   - `_speak()` currently invokes `_speak_script()` (`run_speak.sh`) via `subprocess.run` (Obs. 1, lines 522, 530).
   - This bypasses in-process TTS and spawns secondary shell/Python processes, violating Requirement R1 and PROJECT.md § Key Principles #2.
   - Removing `_speak_script()` and calling `TTSEngine` (or `ResilientSynthesizer`) directly in `_speak()` eliminates this process sprawl.
2. **Brittle Playback Timing & Detached Sessions**:
   - `_speak()` uses `wave.open` to compute duration and sleeps via `time.sleep(duration + 0.5)` (Obs. 1, lines 546–550).
   - It reads detached session files via `SessionDir.wav_path_path()` and `SessionDir.read_pid()` (Obs. 1, lines 545, 552).
   - It forcefully kills `mpv` via `os.kill(pid, _signal.SIGKILL)` (Obs. 1, lines 555, 594).
   - It busy-waits up to 10 minutes via `_wait_mpv_idle` (Obs. 1, lines 520, 562–597).
   - Replacing this entire block with synchronous `NativeAudioSink.play(wav_path)` eliminates duration guesswork, `time.sleep`, detached file reads, and `SIGKILL`.
3. **Dead Code & System-Wide Process Disruption**:
   - In `_process_chunk`, lines 316–331 handle `UserPromptSubmit` and terminate with `continue` at line 331 (Obs. 1).
   - Consequently, lines 333–349 are completely unreachable dead code.
   - Lines 318 and 334 execute `subprocess.run(["pkill", "-9", "mpv"])`, which indiscriminately kills all `mpv` processes on the user's operating system.
   - Removing lines 333–349 and replacing line 318 with `self._audio_sink.interrupt()` scopes process management strictly to the daemon's own playback child process.
4. **AttributeError Root Cause & Fix**:
   - In `test_tail_resumes_a_line_split_across_two_reads`, `NarratorService` is allocated via `__new__` without invoking `__init__`, so `_classifier` is absent on `self`.
   - `hasattr(self._classifier, "_current")` attempts to evaluate the argument `self._classifier` before calling `hasattr`, raising `AttributeError` (Obs. 2).
   - Replacing line 237 with:
     ```python
     classifier = getattr(self, "_classifier", None)
     if classifier and hasattr(classifier, "_current") and classifier._current:
     ```
     safely returns `None` when `_classifier` is absent, cleanly bypassing the check and fixing the test failure.

## 3. Caveats
- `NarratorService` also manages eager loading for `Summarizer` (`_get_summarizer()` / `_eager_load()`); this should remain intact during M1, as TTS synthesis and summarization operate on separate concerns in `_tts_worker`.
- The UNIX domain socket server integration (`socketserver.ThreadingUnixStreamServer`) belongs to Milestone M2 per PROJECT.md, not M1.
- In `resilient_synthesizer.py`, `ResilientSynthesizer.synthesize_one()` accepts a `VoiceProfile`; `NarratorService` should ensure a default or configured `VoiceProfile` is passed during in-process synthesis.

## 4. Conclusion
1. **Hacks to Remove in `narrator_service.py`**:
   - Lines 105–110: `_project_root()` and `_speak_script()`
   - Lines 317–318: inline `import subprocess` and `pkill -9 mpv`
   - Lines 333–349: duplicate dead code block
   - Line 520: `self._wait_mpv_idle(max_seconds=600.0, initial_sleep=0.0)`
   - Lines 522–536: `_speak_script()` call and `subprocess.run(run_speak.sh)`
   - Lines 540–561: `SessionDir`, `wave.open`, `time.sleep`, `SIGKILL`, and fallback call
   - Lines 562–597: `_wait_mpv_idle()` method definition
2. **Bug Fix in `narrator_service.py`**:
   - Replace lines 236–243 with safe `classifier = getattr(self, "_classifier", None)` check and `if current.events and ...` guard.
3. **Unit Test Strategy**:
   - Verify line 237 fix makes all 15 existing tests in `tests/test_narrator_service.py` pass.
   - Add unit tests for `_speak()` with mock `NativeAudioSink` and `TTSEngine`.
   - Add unit test for `UserPromptSubmit` verifying `audio_sink.interrupt()` is called without `pkill`.

## 5. Verification Method
1. **Reproduce Bug**:
   ```bash
   pytest tests/test_narrator_service.py
   ```
   Observes failure on `test_tail_resumes_a_line_split_across_two_reads` with `AttributeError: 'NarratorService' object has no attribute '_classifier'`.
2. **Verify Fix**:
   After applying the `getattr(self, "_classifier", None)` edit, run:
   ```bash
   pytest tests/test_narrator_service.py
   python3 tests/test_narrator_service.py
   ```
   Expected result: 15 passed, 0 failed.
3. **Verify Removal of Legacy Hacks**:
   Grep `narrator_service.py` to confirm zero occurrences of:
   ```bash
   grep -E "time\.sleep|SIGKILL|pkill|_wait_mpv_idle|SessionDir|run_speak" plugin/scripts/python/narrator_service.py
   ```
   Expected result: 0 matches.
