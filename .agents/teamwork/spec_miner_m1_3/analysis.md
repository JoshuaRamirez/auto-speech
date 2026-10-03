# Analysis: NarratorService Legacy Hacks, Bug Fix, and Unit Testing (M1)

## Objective & Scope
Investigate Milestone M1 specifications for `plugin/scripts/python/narrator_service.py` and `tests/test_narrator_service.py`:
1. Catalog every legacy hack line in `narrator_service.py` to be removed during M1 (`time.sleep`, `SIGKILL`/`pkill`, `SessionDir`, `_wait_mpv_idle`, duplicate dead code blocks).
2. Specify the exact fix for the unit test failure at `tests/test_narrator_service.py:306` (`AttributeError` on line 237).
3. Formulate a comprehensive unit test verification strategy for `tests/test_narrator_service.py` under the new in-process architecture.

## Authoritative Specification Sources
- `ORIGINAL_REQUEST.md`: Requirements §R1 (In-Process TTSEngine and Blocking AudioSink), §R3 (Remove Dead Architectural Sprawl), Acceptance Criteria.
- `PROJECT.md`: Feature Inventory (#1 NativeAudioSink, #2 In-Process TTSEngine, #3 NarratorService Cleanup), Milestone M1 definition, Interface Contracts.
- `plugin/scripts/python/narrator_service.py`: Target source file containing legacy hacks and bug.
- `tests/test_narrator_service.py`: Existing unit test suite exercising module helpers and reproducing line 237 failure.

---

## Features Discovered
| # | Category | Feature | Description | Inputs | Outputs | Error Behavior | Discovered Via |
|---|----------|---------|-------------|--------|---------|----------------|----------------|
| 1 | Architecture / Daemon | Elimination of `run_speak.sh` Subprocess | Replace external shell script invocation with in-process TTS and synchronous playback | Text string (`str`) | Audio played to hardware | Log failure, do not crash daemon | PROJECT.md § Architecture, ORIGINAL_REQUEST §R1 |
| 2 | Cleanup / Concurrency | Elimination of `time.sleep` Duration Hack | Remove `wave.open` duration calculation and `time.sleep(duration + 0.5)` polling | WAV file on disk | Natural execution block until completion | None; synchronous child process handles timing | `narrator_service.py:545-550`, PROJECT.md § Architecture |
| 3 | Cleanup / Process Control | Elimination of `SIGKILL` and `pkill` Hacks | Replace indiscriminate `pkill -9 mpv` and `os.kill(pid, SIGKILL)` with controlled `NativeAudioSink.interrupt()` | Interrupt event (e.g. `UserPromptSubmit`) | Terminated child `mpv` process | Safe no-op if no playback active | `narrator_service.py:318, 555, 594`, PROJECT.md § Audio Sink |
| 4 | Cleanup / Storage | Elimination of `SessionDir` Dependency | Remove imports and queries to detached session state (`read_pid`, `wav_path_path`, `is_mpv_running`) | None | Internal daemon state tracking | None; no external state file race conditions | `narrator_service.py:542, 545, 552, 563`, PROJECT.md § Code Layout |
| 5 | Cleanup / IPC | Elimination of `_wait_mpv_idle` Method | Delete 36-line polling method using `mpv_ipc.send(["get_property", "eof-reached"])` | `max_seconds`, `initial_sleep` | None | Stale timeout kill removed | `narrator_service.py:562-597`, DISPATCH.md |
| 6 | Cleanup / Dead Code | Removal of Duplicate Code Block | Delete unreachable identical block in `_process_chunk` following unconditional `continue` | Hook event JSON payload | Deduplicated processing path | Eliminates potential desync / confusion | `narrator_service.py:333-349` |
| 7 | Bug Fix / Resilience | Safe `_classifier` Attribute Access | Use `getattr(self, "_classifier", None)` in `_tail_events` wall-clock flush to avoid crash | `self` instance (with or without `_classifier`) | In-flight phase flush or safe pass | Silently bypasses phase check if classifier absent | `narrator_service.py:237`, `test_narrator_service.py:306` |
| 8 | Audio Output | `NativeAudioSink.play()` Integration | Synchronous audio playback via `mpv --really-quiet --no-video --keep-open=no --idle=no` | `Path` to WAV file | Blocks caller until audio finishes | Raises on abnormal process failure | PROJECT.md § Audio Sink |
| 9 | Audio Output | `NativeAudioSink.interrupt()` Integration | Safe termination of active playback process when new user prompt arrives | User prompt event | Immediate audio silence | Ignores missing/dead process | PROJECT.md § Audio Sink |
| 10 | Synthesis | In-Process `TTSEngine` Integration | Direct instantiation of Kokoro TTS in `narrator_service.py` worker thread | Text string, `VoiceProfile`, output `Path` | Generated WAV file | Logs error, advances queue without blocking | PROJECT.md § Key Principles #2, `tts_engine.py` |

---

## Edge Cases
| # | Feature | Input | Observed Behavior |
|---|---------|-------|-------------------|
| 1 | Safe Classifier Access | `NarratorService` constructed via `__new__` (no `_classifier`) | Line 237 raises `AttributeError: 'NarratorService' object has no attribute '_classifier'`. Fixed: evaluates `getattr()` to `None`, cleanly skips. |
| 2 | Safe Classifier Access | `self._classifier` exists but `_current` is empty dictionary `{}` | Safely evaluates `bool(classifier._current) == False`, no iteration. |
| 3 | Safe Classifier Access | `self._classifier._current[sid]` has empty `events` list (`[]`) | Accessing `current.events[-1]` would raise `IndexError`. Fixed: guard with `if current.events and ...`. |
| 4 | Wall-Clock Flush | Silence threshold not yet exceeded | Phase remains in `classifier._current[sid]`; no enqueue occurs until timeout. |
| 5 | `UserPromptSubmit` Interruption | Audio currently playing via `NativeAudioSink` | `interrupt()` sends `SIGTERM`/kill to active child process; playback halts instantly; turn reset proceeds. |
| 6 | `UserPromptSubmit` Interruption | No audio currently playing | `interrupt()` is a clean no-op; does not raise error; no stray `pkill` executed. |
| 7 | Duplicate Block Elimination | User prompt submit JSON chunk arrives | Single execution of turn reset and start words generation; no double-enqueue or unreachable code. |
| 8 | Empty Speech String | Empty or whitespace-only string passed to `_speak()` | Returns immediately without synthesizing or invoking `audio_sink.play()`. |
| 9 | Queue Full During Playback | Bursts of tool phases while audio is playing | Handled by `_enqueue_phase` drop-oldest backpressure; bounded at `max_queue_depth` (default 32). |
| 10 | Daemon Shutdown | Shutdown sentinel `None` put into `_tts_queue` | Worker terminates immediately; does not call `_speak()`; unlinks PID file. |

---

## Deep Dive 1: Complete Catalog of Legacy Hacks in `narrator_service.py`

### 1. External Script Resolution: `_project_root()` & `_speak_script()`
- **Location**: Lines 105–110
  ```python
  def _project_root() -> Path:
      return Path(__file__).resolve().parents[3]


  def _speak_script() -> Path:
      return _project_root() / "plugin" / "scripts" / "shell" / "run_speak.sh"
  ```
- **Callsite**: Line 522
  ```python
  speak = _speak_script()
  ```
- **Execution**: Lines 530–536
  ```python
  proc = subprocess.run(
      [str(speak), "--keep-artifacts"],
      input=line.encode("utf-8"),
      capture_output=True,
      timeout=120,
      env=env,
  )
  ```
- **Rationale for Removal**:
  Spawns `run_speak.sh` as an external subprocess, which spins up a secondary Python runtime running `speak.py`, which in turn invoked `PipelineOrchestrator`, `ShortPathStrategy`, and detached `mpv` processes.
- **M1 Replacement**:
  In-process `TTSEngine` (or `ResilientSynthesizer`) produces the WAV file directly in-process; `NativeAudioSink.play(wav_path)` plays it synchronously. `_speak_script()` and `_project_root()` have no other callers in `narrator_service.py` and are deleted.

### 2. Duplicate Dead Code Block & `pkill -9 mpv` in `_process_chunk`
- **Location**: Lines 316–350
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
- **Rationale for Removal**:
  1. **Duplicate Dead Code**: Lines 333–349 are completely unreachable because line 331 unconditionally issues `continue`.
  2. **Uncontrolled `pkill`**: Lines 318 and 334 execute `pkill -9 mpv`. This is an un-scoped global kill command that terminates any `mpv` process across the OS.
  3. **Redundant Import**: Line 317 `import subprocess` is redundant (`subprocess` is imported at top of file, line 21).
- **M1 Replacement**:
  Delete lines 333–349 completely. Replace line 318 with `self._audio_sink.interrupt()`.

### 3. Pre-Wait `_wait_mpv_idle()` Call
- **Location**: Lines 518–520
  ```python
  518:         # PRE: don't interrupt an in-progress play. Wait indefinitely (up to 10m) 
  519:         # for long end-of-turn chat responses to finish.
  520:         self._wait_mpv_idle(max_seconds=600.0, initial_sleep=0.0)
  ```
- **Rationale for Removal**:
  Attempted to prevent collision with separate detached background processes (e.g. autoplay worker) by polling for up to 10 minutes. In the unified daemon architecture, the daemon exclusively owns the audio hardware; `_tts_worker` processes audio sequentially and synchronously.
- **M1 Replacement**:
  Deleted entirely.

### 4. Post-Wait `SessionDir`, `wave.open`, `time.sleep`, and `SIGKILL`
- **Location**: Lines 540–561
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
- **Specific Legacy Elements**:
  - `SessionDir.wav_path_path()` (line 545) & `SessionDir.read_pid()` (line 552): Reads metadata files from `/tmp/auto-speech-mpv-*` left by external scripts.
  - `wave.open` & duration calculation (lines 546–547): Computes length to guess sleep duration.
  - `time.sleep(duration + 0.5)` (lines 549–550): Blocks execution blindly based on an estimate, causing cutoffs or lag.
  - `os.kill(pid, _signal.SIGKILL)` (line 555): Force kills the process via SIGKILL.
  - Fallback `self._wait_mpv_idle(max_seconds=15.0, initial_sleep=0.3)` (line 560): Redundant polling fallback.
- **M1 Replacement**:
  All lines 540–561 are replaced by synchronous playback:
  `self._audio_sink.play(wav_path)`
  Since `play()` blocks until the mpv child process terminates, no duration calculation, sleep, or PID killing is needed.

### 5. `_wait_mpv_idle` Method Implementation
- **Location**: Lines 562–597
  ```python
  562:     def _wait_mpv_idle(self, max_seconds: float, initial_sleep: float = 0.3) -> None:
  563:         from session_dir import SessionDir  # local import; project module
  564:         from mpv_ipc import MpvIpc, MpvIpcError
  565:         import signal
  566: 
  567:         deadline = time.monotonic() + max_seconds
  568:         # Optional initial sleep — useful AFTER we start mpv (let it spin
  569:         # up before checking), pointless BEFORE we'd start a new one.
  570:         if initial_sleep > 0:
  571:             time.sleep(initial_sleep)
  572:         while time.monotonic() < deadline:
  573:             if not SessionDir.is_mpv_running():
  574:                 return
  575:             
  576:             socket_path = SessionDir.socket_path()
  577:             if socket_path.exists():
  578:                 try:
  579:                     res = MpvIpc.send(["get_property", "eof-reached"], socket_path)
  580:                     if res.get("data") == True:
  581:                         _log("mpv reached eof; killing it manually")
  582:                         pid = SessionDir.read_pid()
  583:                         if pid:
  584:                             os.kill(pid, signal.SIGTERM)
  585:                         return
  586:                 except MpvIpcError:
  587:                     pass
  588: 
  589:             time.sleep(0.25)
  590:         _log(f"mpv still running after {max_seconds}s; moving on")
  591:         pid = SessionDir.read_pid()
  592:         if pid:
  593:             try:
  594:                 os.kill(pid, signal.SIGKILL)
  595:             except Exception:
  596:                 pass
  ```
- **Rationale for Removal**:
  Polls IPC sockets in a busy-wait loop (`time.sleep(0.25)`), sends JSON-IPC requests to mpv to check for EOF, and sends `SIGTERM` or `SIGKILL`.
- **M1 Replacement**:
  Deleted entirely.

---

## Deep Dive 2: Line 237 Bug Specification & Exact Fix

### Symptom & Reproduction
Running `pytest tests/test_narrator_service.py` or `python3 tests/test_narrator_service.py` fails with:
```
FAILED tests/test_narrator_service.py::test_tail_resumes_a_line_split_across_two_reads
...
  File "/Users/joshua/Developer/auto-speech/plugin/scripts/python/narrator_service.py", line 237, in _tail_events
    if hasattr(self._classifier, "_current") and self._classifier._current:
               ^^^^^^^^^^^^^^^^
AttributeError: 'NarratorService' object has no attribute '_classifier'
```

### Root Cause
In Python, evaluating `hasattr(self._classifier, "_current")` requires evaluating the argument expression `self._classifier` first. If `self` does not have the attribute `_classifier`, Python raises `AttributeError` during the attribute lookup of `self._classifier`, before `hasattr()` is ever called.
In unit tests such as `test_tail_resumes_a_line_split_across_two_reads`, `NarratorService` is constructed via `__new__`:
```python
svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
```
to bypass model loading and background threads. Consequently, `svc` does not have `_classifier` assigned.

### Exact Code Fix
In `plugin/scripts/python/narrator_service.py`, replace lines 236–243:

**Before**:
```python
            # Wall-clock phase flush
            if hasattr(self._classifier, "_current") and self._classifier._current:
                for sid, current in list(self._classifier._current.items()):
                    if time.time() - current.events[-1].ts > self._classifier._silence_seconds:
                        closed = self._classifier.flush(sid)
                        if closed:
                            self._maybe_enqueue(closed)
```

**After**:
```python
            # Wall-clock phase flush
            classifier = getattr(self, "_classifier", None)
            if classifier and hasattr(classifier, "_current") and classifier._current:
                for sid, current in list(classifier._current.items()):
                    silence_s = getattr(classifier, "_silence_seconds", 0.5)
                    if current.events and (time.time() - current.events[-1].ts > silence_s):
                        closed = classifier.flush(sid)
                        if closed:
                            self._maybe_enqueue(closed)
```

### Verification
When `getattr(self, "_classifier", None)` is used:
- If `_classifier` is absent on `self`, `getattr` returns `None`.
- `if classifier and ...` evaluates to `None` (falsy) and skips the block without error.
- If `_classifier` is present, it accesses `_current` and executes the silence threshold check safely.
- Guarding `if current.events and ...` prevents `IndexError` on empty event lists.

---

## Deep Dive 3: Unit Test Verification Strategy for `tests/test_narrator_service.py`

### 1. Verification of Existing Suite
Once the line 237 fix is in place, the entire existing unit test suite passes:
```bash
pytest -v tests/test_narrator_service.py
# or
python3 tests/test_narrator_service.py
```
Expected result: **15 passed, 0 failed**.

### 2. New Unit Tests for M1 In-Process Architecture
To thoroughly verify the new architecture in `NarratorService`, the following unit tests should be added to `tests/test_narrator_service.py`:

#### Test A: `test_speak_invokes_synthesizer_and_audio_sink`
- **Objective**: Ensure `_speak(line)` synthesizes WAV in-process and delegates to `NativeAudioSink.play()`, without external shell scripts, sleeps, or kills.
- **Fixture / Mock**:
  ```python
  class _MockSink:
      def __init__(self):
          self.played = []
          self.interrupted = False
      def play(self, wav_path):
          self.played.append(Path(wav_path))
      def interrupt(self):
          self.interrupted = True

  class _MockSynthesizer:
      def __init__(self):
          self.synthesized = []
      def synthesize_one(self, text, profile, out_path):
          self.synthesized.append((text, out_path))
          out_path.touch()
          return True
  ```
- **Assertions**:
  - `synth.synthesized[0][0] == "hello"`
  - `sink.played == [out_path]`
  - No calls to `subprocess.run(["...run_speak.sh"])`
  - No calls to `time.sleep`

#### Test B: `test_process_chunk_user_prompt_submit_interrupts_audio_sink`
- **Objective**: Ensure `UserPromptSubmit` hook events trigger `audio_sink.interrupt()` instead of `pkill -9 mpv`.
- **Assertions**:
  - `sink.interrupted is True`
  - With `patch("subprocess.run") as mock_subproc`, verify `mock_subproc.assert_not_called()` or `pkill` is never passed as argument.

#### Test C: `test_tail_events_with_missing_classifier_does_not_raise`
- **Objective**: Regression test explicitly pinning the line 237 fix.
- **Assertions**:
  - `svc._tail_events()` executes multiple poll loops and terminates on `_stop.set()` without `AttributeError`.

#### Test D: `test_tail_events_wall_clock_phase_flush_triggers`
- **Objective**: Verify that when `_classifier` is present, elapsed silence causes an in-flight phase to flush and enqueue.
- **Assertions**:
  - In-flight phase in `classifier._current` is flushed and passed to `_maybe_enqueue`.

### 3. Verification Pipeline Summary
- `pytest tests/test_narrator_service.py`: Unit tests for narrator service.
- `tests/run_all.sh`: Regression test across all components.
