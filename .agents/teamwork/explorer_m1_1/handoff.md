# Handoff Report: NativeAudioSink Investigation & Design (M1.1)

**From**: `explorer_m1_1` (NativeAudioSink Specialist)  
**To**: Orchestrator (`c05df6b8-cecd-49ba-9fb8-8fa47f977488`) / Implementer Agent  
**Artifacts**: `analysis.md`, `handoff.md`  

---

## 1. Observation

1. **Brittle Playback Hacks in `narrator_service.py`**:
   - `plugin/scripts/python/narrator_service.py` lines 317–318 and 333–334 execute indiscriminate system-wide kills:
     ```python
     # Line 318 & 334:
     subprocess.run(["pkill", "-9", "mpv"], capture_output=True)
     ```
   - `plugin/scripts/python/narrator_service.py` lines 545–557 compute audio length and sleep with SIGKILL termination:
     ```python
     wav_path = SessionDir.wav_path_path().read_text().strip()
     with wave.open(wav_path, "r") as f:
         duration = f.getnframes() / float(f.getframerate())
     _log(f"sleeping for wav duration: {duration:.2f}s")
     time.sleep(duration + 0.5)  # slight buffer
     pid = SessionDir.read_pid()
     if pid:
         try:
             os.kill(pid, _signal.SIGKILL)
         except ProcessLookupError:
             pass
     ```
   - Lines 520 and 560 rely on polling `_wait_mpv_idle(max_seconds=...)` against `/tmp` filesystem sockets.

2. **Detached Sprawl in `mpv_controller.py`**:
   - `plugin/scripts/python/mpv_controller.py` lines 104–119 spawn detached processes with `start_new_session=True` and IPC server `--input-ipc-server={socket_path}`.
   - It requires file locking via `fcntl.flock` on `/tmp/auto-speech-mpv-start.lock` (line 72) and polling loops with timeout caps (lines 180–190).

3. **Interface Specification in `PROJECT.md` & `DISPATCH.md`**:
   - `PROJECT.md` lines 77–84 specify:
     ```python
     class NativeAudioSink:
         def play(self, wav_path: Path) -> None:
             """Plays wav file synchronously via mpv, blocking until playback finishes."""
         def interrupt(self) -> None:
             """Terminates any currently active mpv playback process."""
     ```
   - Required CLI arguments: `mpv --really-quiet --no-video --keep-open=no --idle=no <wav_path>`.

4. **Environment Telemetry & Verification**:
   - Environment verification: Python 3.14.7, pytest 9.0.2, and Homebrew mpv at `/opt/homebrew/bin/mpv`.
   - Command prototype execution of `mpv --really-quiet --no-video --keep-open=no --idle=no <file>` on macOS:
     - Real silent WAV playback exited with return code `0`.
     - Mid-playback termination via `proc.terminate()` (SIGTERM) unblocked `proc.communicate()` in 56ms with return code `-15` without orphaned child processes.

---

## 2. Logic Chain

1. **Subprocess Architecture (Observation 1 & 3 → Choice of `Popen`)**:
   - While `PROJECT.md` mentions `subprocess.run(...)`, `subprocess.run()` encapsulates the process object inside its local function scope, making it inaccessible to other threads calling `interrupt()`.
   - Therefore, `NativeAudioSink` must instantiate `subprocess.Popen` and retain a reference on `self._proc`.
   - `play()` invokes `proc.communicate()` which provides synchronous, blocking execution while preventing pipe buffer deadlocks.

2. **Concurrency Safety & Deadlock Prevention (Observation 1 & 3 → Dual-Lock Design)**:
   - Playback requests must run sequentially (FIFO) to prevent audio overlapping and race conditions. A `_playback_lock` serializes calls to `play()`.
   - However, if `interrupt()` attempted to acquire `_playback_lock`, it would block until playback completed, defeating the purpose of interruption.
   - Therefore, a separate micro-duration `_state_lock` guards `self._proc` and `self._interrupted`. `interrupt()` acquires only `_state_lock` to obtain the process handle and set the flag.
   - Because `_playback_lock` is never acquired while holding `_state_lock`, deadlock between playback and interruption is mathematically impossible.

3. **Clean Interrupt vs System-Wide `pkill` (Observation 1 & 4 → Targeted Escalation)**:
   - Replacing `pkill -9 mpv` requires targeting exclusively the sink's owned `_proc`.
   - `interrupt()` calls `proc.terminate()` (`SIGTERM`), allowing mpv and CoreAudio to cleanly close hardware buffers without audible audio pops.
   - If the process fails to terminate within 0.5s (`_TERMINATE_TIMEOUT`), it escalates to `proc.kill()` (`SIGKILL`).
   - The child process is awaited to guarantee complete reaping, preventing zombie processes.

4. **Clean Error Contract (Observation 1 & 3 → Exception Handling)**:
   - When a user submits a prompt (`UserPromptSubmit`), `interrupt()` terminates active playback. This is normal user behavior. If `play()` raised an exception upon interruption, the daemon worker loop would emit spurious error logs.
   - Therefore, if `self._interrupted` is True or `retcode in (-signal.SIGTERM, -signal.SIGKILL, 143, 137)`, `play()` returns `None` cleanly.
   - Conversely, if `retcode != 0` without interruption, `play()` raises `PlaybackError` containing the captured stderr.
   - Missing files raise `FileNotFoundError` before spawning; missing binary raises `MpvNotInstalledError`.

---

## 3. Caveats

1. **User Custom Configuration**: mpv reads user configuration files (`~/.config/mpv/mpv.conf`). Explicit CLI flags (`--no-video`, `--keep-open=no`, `--idle=no`) override conflicting config options, but user volume and audio output device selections (`ao=coreaudio`, device names) will be respected.
2. **Audio Hardware Hangs**: On rare macOS CoreAudio daemon crashes (`coreaudiod`), mpv can enter an uninterruptible kernel sleep. The 0.5s timeout followed by `SIGKILL` ensures that even in an audio hardware fault, the Python daemon worker is unblocked within 1.0 second.
3. **Out-of-Scope Milestone Elements**: In-process `TTSEngine` synthesis wiring in `narrator_service.py` is part of Milestone M1's service refactoring; this report focuses strictly on `NativeAudioSink` design and its contract with `narrator_service.py`.

---

## 4. Conclusion

`NativeAudioSink` is fully designed and specified for implementation at `plugin/scripts/python/native_audio_sink.py`:
- Complete production code specification is provided in `analysis.md` §4.
- Complete unit test suite covering mocked and real execution is provided in `analysis.md` §6.2.
- The design successfully eliminates `mpv_controller.py`, `session_dir.py`, `time.sleep` duration guessing, and `pkill -9 mpv` while providing deterministic, synchronous playback and sub-100ms interrupt latency.

---

## 5. Verification Method

To independently verify the implementation once written by the builder agent:

1. **Run Unit & Integration Test Suite**:
   ```bash
   pytest tests/test_native_audio_sink.py -v
   ```
   *Expected*: All tests pass (100% green), including mock tests and real audio playback smoke tests.

2. **Verify Sequential Serialization & Absence of Detached Sessions**:
   ```bash
   python3 -c "
   from native_audio_sink import NativeAudioSink
   import tempfile, wave, threading, time, shutil
   from pathlib import Path

   def make_wav(path, duration_s):
       with wave.open(str(path), 'wb') as w:
           w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
           w.writeframes(b'\x00' * int(24000 * 2 * duration_s))

   sink = NativeAudioSink()
   with tempfile.NamedTemporaryFile(suffix='.wav') as f1, tempfile.NamedTemporaryFile(suffix='.wav') as f2:
       make_wav(Path(f1.name), 0.2)
       make_wav(Path(f2.name), 0.2)
       order = []
       def run_play(p, name):
           sink.play(p)
           order.append(name)
       t1 = threading.Thread(target=run_play, args=(Path(f1.name), 'first'))
       t2 = threading.Thread(target=run_play, args=(Path(f2.name), 'second'))
       t1.start(); time.sleep(0.02); t2.start()
       t1.join(); t2.join()
       assert order == ['first', 'second']
       print('Verification SUCCESS: Sequential playback verified.')
   "
   ```

3. **Invalidation Conditions**:
   - Playback leaves orphan `mpv` processes in `ps aux | grep mpv`.
   - `interrupt()` blocks on an active playback process for > 1.0 second.
   - Calling `interrupt()` during `UserPromptSubmit` kills unrelated user `mpv` processes.
   - Calling `play()` with an invalid/corrupt audio file hangs or swallows errors silently.
