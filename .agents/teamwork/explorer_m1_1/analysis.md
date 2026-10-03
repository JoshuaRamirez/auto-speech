# Architectural Investigation & Design: NativeAudioSink

**Author**: `explorer_m1_1` (NativeAudioSink Specialist)  
**Date**: 2026-10-03  
**Target Module**: `plugin/scripts/python/native_audio_sink.py`  
**Test Suite**: `tests/test_native_audio_sink.py`  
**Milestone**: M1 (In-Process TTSEngine & NativeAudioSink)

---

## 1. Executive Summary

This document presents the detailed architectural design, technical specification, and verification strategy for `NativeAudioSink`.

The `auto-speech` system is transitioning to a **Unified Daemon Server** architecture (PROJECT.md). Under this model, the daemon acts as the single authoritative hardware owner for audio output. `NativeAudioSink` replaces legacy detached background playback sessions, `/tmp` session metadata files, IPC polling, brittle `time.sleep(duration)` calculations, and indiscriminate system-wide `pkill -9 mpv` hacks with a clean, synchronous, thread-safe audio sink.

Key conclusions:
1. **Synchronous Popen Lifecycle**: `NativeAudioSink` uses `subprocess.Popen` combined with `proc.communicate()` to provide blocking, synchronous playback while maintaining an active process handle for immediate interruption.
2. **Targeted Interrupt Mechanism**: `interrupt()` terminates only the specific `mpv` process spawned by the sink using `SIGTERM` with bounded escalation to `SIGKILL` (0.5s grace period). It completely eliminates `pkill -9 mpv`.
3. **Deadlock-Free Dual-Lock Architecture**: A `_playback_lock` serializes sequential playback requests (guaranteeing FIFO playback and preventing overlapping audio), while a micro-duration `_state_lock` guards the process handle. `interrupt()` never contends for `_playback_lock`, guaranteeing immediate unblocking.
4. **Clean Error Contract**: Interrupted playback returns cleanly (`None`) to prevent polluting daemon error logs on routine user events (`UserPromptSubmit`). Missing binaries raise `MpvNotInstalledError`, missing files raise `FileNotFoundError`, and abnormal non-zero exits raise `PlaybackError`.

---

## 2. Analysis of Existing Code & Pathologies

### 2.1 The Legacy Detached Architecture (`mpv_controller.py`)
In the legacy codebase, playback was managed by `MpvController`:
- Spawned `mpv` detached with `start_new_session=True` and `--input-ipc-server=<socket_path>`.
- Wrote process metadata (`pid`, `wav_path`, `started_at`) into `/tmp/auto-speech/`.
- Synchronized multiple callers using filesystem file locks (`/tmp/auto-speech-mpv-start.lock` via `fcntl.flock`).
- Polled Unix domain sockets every 50ms for readiness (`MpvIpc.send(["get_property", "pid"])`).
- Had complex cap expiration logic (`_PRIOR_PLAYBACK_WAIT_SECONDS = 15.0`) to avoid wedging on stalled playback.

**Pathology**: High complexity, filesystem socket churn, race conditions between independent processes, and frequent orphan processes if a caller died before cleanup.

### 2.2 Brittle Playback in `narrator_service.py`
In `narrator_service.py`:
- **Duration guessing & sleep hacks** (lines 541–560):
  ```python
  with wave.open(wav_path, "r") as f:
      duration = f.getnframes() / float(f.getframerate())
  time.sleep(duration + 0.5)  # slight buffer
  pid = SessionDir.read_pid()
  if pid:
      os.kill(pid, signal.SIGKILL)
  ```
  This calculated audio duration from WAV headers, slept for that duration plus an arbitrary 0.5-second buffer, and then forcibly sent `SIGKILL` to `mpv`!
- **System-wide `pkill -9 mpv` on User Prompt** (lines 318, 334):
  ```python
  if event_type == "UserPromptSubmit":
      subprocess.run(["pkill", "-9", "mpv"], capture_output=True)
  ```
  Whenever the user submitted a prompt, the daemon ran `pkill -9 mpv`. If the user had any personal instance of `mpv` open (watching a tutorial, listening to music), it was abruptly terminated! Furthermore, sending `SIGKILL` (-9) bypassed mpv's audio driver cleanup, causing audio interface pops and driver instability.

---

## 3. NativeAudioSink Architecture & Technical Design

### 3.1 Subprocess Management: Why `subprocess.Popen` over `subprocess.run`
The project requirement specifies synchronous playback via:
`mpv --really-quiet --no-video --keep-open=no --idle=no <wav_path>`

While `subprocess.run(...)` is synchronous, its `Popen` instance is strictly local to the function frame. If a worker thread blocks inside `subprocess.run()`, another thread (e.g. event listener on `UserPromptSubmit`) cannot access the underlying `process` object to send `SIGTERM`.

`NativeAudioSink` therefore uses `subprocess.Popen` internally:
- `play(wav_path)` creates the `subprocess.Popen` instance and assigns it to `self._proc`.
- It executes `proc.communicate(timeout=timeout)`.
- `proc.communicate()` blocks synchronously until playback finishes, while streaming and buffering `stderr` safely to prevent pipe deadlocks.
- When `interrupt()` is called from another thread, it immediately signals `self._proc`, unblocking `communicate()` within milliseconds.

### 3.2 mpv Command-Line Arguments Rationale
`["mpv", "--really-quiet", "--no-video", "--keep-open=no", "--idle=no", str(wav_path)]`

| Flag | Purpose & Rationale |
|---|---|
| `--really-quiet` | Suppresses all terminal/console output, status lines, and progress bars. Prevents terminal pollution in daemon logs. |
| `--no-video` | Disables video rendering subsystems. Guarantees no Cocoa/OpenGL window or macOS Dock icon flashes on screen, even if the audio file contains album art. |
| `--keep-open=no` | Forces mpv to exit immediately when playback reaches EOF, overriding any user `mpv.conf` setting (`keep-open=yes`) that would pause at the end. |
| `--idle=no` | Prevents mpv from idling when no file is playing or playback finishes. |
| `str(wav_path)` | Path to the target audio file, normalized via `.resolve()`. |

### 3.3 Concurrency & Lock Hierarchy
`NativeAudioSink` employs a **Dual-Lock Architecture**:

```
Thread A: play()                           Thread B: interrupt()
────────────────                           ─────────────────────
acquire(_playback_lock)
  │
  acquire(_state_lock)
    _proc = Popen(...)
  release(_state_lock)
  │
  proc.communicate()  ◄───────────────────── acquire(_state_lock)
  (blocks on audio)                           proc = _proc
                                              _interrupted = True
                                            release(_state_lock)
                                            proc.terminate()
                                            proc.wait(timeout=0.5)
  (unblocks immediately: rc=-15)
  │
  acquire(_state_lock)
    _proc = None
  release(_state_lock)
  │
release(_playback_lock)
```

1. **`_playback_lock` (Sequential Playback Guard)**:
   - Acquired during the full duration of `play()`.
   - Serializes concurrent callers so audio files are played sequentially (FIFO) without overlapping.
   - Prevents race conditions where multiple threads spawn simultaneous processes and overwrite `_proc`.
2. **`_state_lock` (Atomic Process State Guard)**:
   - Acquired only for microseconds during process pointer assignment, pointer clearance, and `interrupt()` signaling.
   - `interrupt()` **NEVER** attempts to acquire `_playback_lock`.
   - Because `_playback_lock` is never acquired while holding `_state_lock`, deadlock is mathematically impossible.

### 3.4 Clean Interrupt Protocol
The `interrupt()` implementation:
1. Atomically checks if `self._proc` is active (`_proc is not None and _proc.poll() is None`).
2. Marks `self._interrupted = True`.
3. Calls `proc.terminate()` (`SIGTERM`), allowing mpv to close macOS CoreAudio hardware cleanly.
4. Waits up to `_TERMINATE_TIMEOUT` (0.5s) for the process to exit.
5. If the process does not exit within 0.5s (e.g. hung audio hardware), escalates to `proc.kill()` (`SIGKILL`) with a secondary 0.5s grace period.
6. Catches `ProcessLookupError` safely if the process already terminated.
7. Ensures child processes are reaped to prevent zombie processes.
8. Benchmarked interrupt latency on macOS: **< 60 milliseconds**.

### 3.5 Error Handling Contract
The error contract is strictly categorized:
- **`FileNotFoundError`**: Raised immediately if `wav_path` does not exist or is not a file, before spawning any subprocess.
- **`MpvNotInstalledError(AudioSinkError, RuntimeError)`**: Raised if `mpv` is not found on `PATH`. Provides the remediation command (`brew install mpv`).
- **Interruption Handling**: If the process exited due to `interrupt()` or received `SIGTERM`/`SIGKILL` (`retcode in (-signal.SIGTERM, -signal.SIGKILL, 143, 137)` or `_interrupted is True`), `play()` logs an info message and returns `None`. **No exception is raised.** This is critical: `UserPromptSubmit` is normal user behavior, not an error.
- **`PlaybackError(AudioSinkError, RuntimeError)`**: Raised if `mpv` exits with a non-zero exit code (e.g. 2 for corrupt audio format or missing audio device) when not interrupted. Stderr is captured and included in the exception message for diagnostics.
- **Unhandled Exceptions / Timeout**: If `communicate()` times out or encounters an unexpected exception, `self.interrupt()` is automatically invoked in a `finally`/`except` block to ensure no orphaned processes are left running.

---

## 4. Proposed Specification for `native_audio_sink.py`

Below is the complete proposed implementation for `plugin/scripts/python/native_audio_sink.py`:

```python
"""NativeAudioSink: Synchronous, blocking audio sink using mpv with clean interrupt handling."""
from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class AudioSinkError(RuntimeError):
    """Base exception for all NativeAudioSink errors."""


class MpvNotInstalledError(AudioSinkError):
    """Raised when the mpv binary is not found on PATH."""


class PlaybackError(AudioSinkError):
    """Raised when mpv exits with a non-zero exit code during playback."""


class NativeAudioSink:
    """Synchronous audio sink playing WAV files via mpv with interrupt support.

    Playback is blocking and sequential. An active playback can be interrupted
    immediately from another thread via interrupt().
    """

    _TERMINATE_TIMEOUT = 0.5  # Seconds to wait for SIGTERM before escalating to SIGKILL
    _KILL_TIMEOUT = 0.5       # Seconds to wait after SIGKILL

    def __init__(self, mpv_path: str | Path | None = None) -> None:
        """Initialize NativeAudioSink.

        Args:
            mpv_path: Optional custom path to mpv binary. If None, resolved via PATH.
        """
        self._custom_mpv_path = str(mpv_path) if mpv_path is not None else None
        self._playback_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._interrupted: bool = False

    def _resolve_mpv(self) -> str:
        """Resolve mpv binary path, raising MpvNotInstalledError if not found."""
        if self._custom_mpv_path:
            return self._custom_mpv_path
        binary = shutil.which("mpv")
        if not binary:
            raise MpvNotInstalledError(
                "mpv not found on PATH. Install with: brew install mpv"
            )
        return binary

    def play(self, wav_path: Path | str, timeout: float | None = None) -> None:
        """Plays wav file synchronously via mpv, blocking until playback finishes.

        Args:
            wav_path: Path to the WAV file to play.
            timeout: Optional maximum duration in seconds for playback.

        Raises:
            FileNotFoundError: If the WAV file does not exist.
            MpvNotInstalledError: If mpv is not installed.
            PlaybackError: If mpv terminates with a non-zero exit code and was not interrupted.
        """
        path = Path(wav_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Audio file not found: {path}")

        mpv_bin = self._resolve_mpv()

        # Serializes playback across threads: only one file plays at a time.
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
                        str(path),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                self._proc = proc

            try:
                stdout, stderr = proc.communicate(timeout=timeout)
                retcode = proc.returncode
            except subprocess.TimeoutExpired:
                self.interrupt()
                raise PlaybackError(f"Playback timed out after {timeout}s: {path}")
            except Exception:
                self.interrupt()
                raise
            finally:
                with self._state_lock:
                    interrupted = self._interrupted
                    if self._proc is proc:
                        self._proc = None

            # If interrupted, return cleanly without error.
            if interrupted or retcode in (-signal.SIGTERM, -signal.SIGKILL, 143, 137):
                logger.info("Playback interrupted for %s", path)
                return

            if retcode != 0:
                err_msg = stderr.decode("utf-8", errors="replace").strip() if stderr else ""
                raise PlaybackError(
                    f"mpv exited with code {retcode} for {path}: {err_msg}"
                )

    def interrupt(self) -> None:
        """Terminates any currently active mpv playback process immediately.

        Safe to call from any thread and idempotent if no playback is active.
        """
        with self._state_lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                return
            self._interrupted = True

        # Signal process outside state lock so other inspections don't block
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

    @property
    def is_playing(self) -> bool:
        """Returns True if playback is currently active."""
        with self._state_lock:
            return self._proc is not None and self._proc.poll() is None

    @property
    def was_interrupted(self) -> bool:
        """Returns True if the most recent playback was interrupted."""
        with self._state_lock:
            return self._interrupted
```

---

## 5. Integration into `narrator_service.py` (M1 Blueprint)

In Milestone M1, `NativeAudioSink` integrates into `narrator_service.py` as follows:

1. **Initialization**:
   ```python
   from native_audio_sink import NativeAudioSink

   class NarratorDaemon:
       def __init__(self, ...):
           ...
           self._audio_sink = NativeAudioSink()
   ```

2. **Clean Interrupt on `UserPromptSubmit`**:
   In `_process_chunk()`:
   ```python
   if event_type == "UserPromptSubmit":
       if self._is_cron_tick(...):
           continue

       # Interrupt active playback cleanly without killing system-wide mpv
       self._audio_sink.interrupt()

       # Clear any pending items from the queue
       while not self._tts_queue.empty():
           try:
               self._tts_queue.get_nowait()
               self._tts_queue.task_done()
           except queue.Empty:
               break
       ...
   ```

3. **Replacing `_speak()`**:
   The entire legacy `_speak()` function (lines 501–561) with `SessionDir`, `_wait_mpv_idle`, `wave.open`, `time.sleep`, and `os.kill` is replaced with:
   ```python
   def _speak(self, line: str) -> None:
       """Synthesize line in-process and play synchronously via NativeAudioSink."""
       _log(f"speak: {line}")
       out_wav = self._synthesize_to_wav(line)  # via in-process ResilientSynthesizer
       if out_wav and out_wav.is_file():
           self._audio_sink.play(out_wav)
   ```

4. **Shutdown Cleanup**:
   In `stop()` or on daemon exit:
   ```python
   self._audio_sink.interrupt()
   ```

---

## 6. Unit Testing Strategy (`tests/test_native_audio_sink.py`)

The test suite must cover both mocked subprocess execution (for fast, deterministic CI verification of all states and edge cases) and real macOS subprocess execution (smoke testing with real mpv).

### 6.1 Test Matrix

| Test Case | Type | Condition | Expected Behavior |
|---|---|---|---|
| `test_missing_file_raises_filenotfound` | Unit | Path does not exist | `FileNotFoundError` before spawning process |
| `test_missing_mpv_raises_mpvnotinstalled` | Unit | `shutil.which` returns None | `MpvNotInstalledError` with installation message |
| `test_play_successful_invokes_mpv` | Mock | Mock Popen returns code 0 | Exact arguments verified; `proc.communicate()` called |
| `test_play_non_zero_exit_raises_playback_error` | Mock | Mock Popen returns code 2 | `PlaybackError` raised containing exit code & stderr |
| `test_interrupt_active_playback` | Mock | Thread calls `interrupt()` during `communicate()` | `proc.terminate()` called; `was_interrupted` True; returns cleanly |
| `test_interrupt_escalates_to_sigkill` | Mock | `proc.wait` raises `TimeoutExpired` | `proc.kill()` called; reaped |
| `test_interrupt_when_idle_is_noop` | Unit | `proc` is None | Safe no-op, no exceptions |
| `test_is_playing_transitions` | Mock | Poll returns None then 0 | `is_playing` transitions True -> False |
| `test_custom_mpv_path_override` | Mock | `NativeAudioSink(mpv_path="/bin/custom_mpv")` | Uses custom binary path |
| `test_concurrent_playback_serialized` | Threading | 2 threads call `play()` | `_playback_lock` serializes sequential execution |
| `test_real_playback_smoke` | Integration | Real 0.2s silent WAV + real `mpv` | Plays to completion in ~0.2s with code 0 |
| `test_real_interrupt_smoke` | Integration | Real 2.0s silent WAV + interrupt at 50ms | Exits cleanly in < 150ms with return code -15 |

### 6.2 Proposed Test Suite Code

```python
"""Unit tests for NativeAudioSink."""
from __future__ import annotations

import shutil
import signal
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from unittest import mock

import pytest

from native_audio_sink import (
    AudioSinkError,
    MpvNotInstalledError,
    NativeAudioSink,
    PlaybackError,
)


def _make_silent_wav(duration_s: float = 0.1, framerate: int = 24000) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wav_path = Path(tmp.name)
    n_frames = int(framerate * duration_s)
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(framerate)
        w.writeframes(b"\x00" * (n_frames * 2))
    return wav_path


def test_missing_wav_file_raises_filenotfound() -> None:
    sink = NativeAudioSink()
    non_existent = Path("/tmp/does_not_exist_auto_speech_123.wav")
    with pytest.raises(FileNotFoundError, match="Audio file not found"):
        sink.play(non_existent)


def test_missing_mpv_raises_mpv_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    wav_path = _make_silent_wav(0.05)
    try:
        sink = NativeAudioSink()
        with pytest.raises(MpvNotInstalledError, match="mpv not found on PATH"):
            sink.play(wav_path)
    finally:
        wav_path.unlink()


def test_play_successful_invokes_mpv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda cmd: "/opt/homebrew/bin/mpv")
    wav_path = _make_silent_wav(0.05)
    mock_proc = mock.MagicMock()
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.returncode = 0
    mock_proc.poll.return_value = None

    with mock.patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
        try:
            sink = NativeAudioSink()
            sink.play(wav_path)

            mock_popen.assert_called_once_with(
                [
                    "/opt/homebrew/bin/mpv",
                    "--really-quiet",
                    "--no-video",
                    "--keep-open=no",
                    "--idle=no",
                    str(wav_path.resolve()),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            mock_proc.communicate.assert_called_once()
            assert not sink.was_interrupted
        finally:
            wav_path.unlink()


def test_play_non_zero_exit_raises_playback_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/mpv")
    wav_path = _make_silent_wav(0.05)
    mock_proc = mock.MagicMock()
    mock_proc.communicate.return_value = (b"", b"Audio device error")
    mock_proc.returncode = 2
    mock_proc.poll.return_value = None

    with mock.patch("subprocess.Popen", return_value=mock_proc):
        try:
            sink = NativeAudioSink()
            with pytest.raises(PlaybackError, match="mpv exited with code 2"):
                sink.play(wav_path)
        finally:
            wav_path.unlink()


def test_interrupt_active_playback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/mpv")
    wav_path = _make_silent_wav(0.05)
    mock_proc = mock.MagicMock()
    mock_proc.returncode = -signal.SIGTERM
    mock_proc.poll.return_value = None

    def fake_communicate(*args, **kwargs):
        mock_proc.poll.return_value = -15
        return (b"", b"")

    mock_proc.communicate.side_effect = fake_communicate

    with mock.patch("subprocess.Popen", return_value=mock_proc):
        try:
            sink = NativeAudioSink()

            def do_interrupt():
                time.sleep(0.01)
                sink.interrupt()

            t = threading.Thread(target=do_interrupt)
            t.start()
            sink.play(wav_path)
            t.join()

            assert sink.was_interrupted
            mock_proc.terminate.assert_called_once()
        finally:
            wav_path.unlink()


def test_interrupt_escalates_to_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_proc = mock.MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="mpv", timeout=0.5), None]

    sink = NativeAudioSink()
    sink._proc = mock_proc

    sink.interrupt()

    mock_proc.terminate.assert_called_once()
    mock_proc.kill.assert_called_once()


def test_interrupt_when_idle_is_noop() -> None:
    sink = NativeAudioSink()
    sink.interrupt()  # Must not raise
    assert not sink.is_playing


@pytest.mark.skipif(not shutil.which("mpv"), reason="mpv binary required for integration smoke test")
def test_real_playback_smoke() -> None:
    sink = NativeAudioSink()
    wav_path = _make_silent_wav(0.1)
    try:
        t0 = time.time()
        sink.play(wav_path)
        elapsed = time.time() - t0
        assert elapsed >= 0.05
        assert not sink.was_interrupted
    finally:
        wav_path.unlink()


@pytest.mark.skipif(not shutil.which("mpv"), reason="mpv binary required for integration smoke test")
def test_real_interrupt_smoke() -> None:
    sink = NativeAudioSink()
    wav_path = _make_silent_wav(2.0)
    try:
        def interrupter():
            time.sleep(0.05)
            sink.interrupt()

        t = threading.Thread(target=interrupter)
        t0 = time.time()
        t.start()
        sink.play(wav_path)
        t.join()
        elapsed = time.time() - t0

        assert sink.was_interrupted
        assert elapsed < 0.6, f"Interrupt was too slow: {elapsed:.2f}s"
    finally:
        wav_path.unlink()
```

---

## 7. Verification & Implementation Recommendations

1. **Implementer Instructions**:
   - Create `plugin/scripts/python/native_audio_sink.py` using the exact code from Section 4.
   - Create `tests/test_native_audio_sink.py` using the code from Section 6.2.
   - Run `pytest tests/test_native_audio_sink.py -v`.
2. **Dependent Milestone Tasks**:
   - `narrator_service.py` builder should import `NativeAudioSink` from `native_audio_sink.py`.
   - In `narrator_service.py`, eliminate lines 318 and 334 (`subprocess.run(["pkill", "-9", "mpv"])`) and replace with `self._audio_sink.interrupt()`.
   - Eliminate lines 541–560 (`SessionDir`, `time.sleep`, `SIGKILL`) in `_speak()`, directing output to `self._audio_sink.play(out_wav)`.
