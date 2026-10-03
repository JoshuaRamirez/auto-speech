# Original User Request

## 2026-10-03T17:45:27Z

# Teamwork Project Prompt — Draft

> Status: Ready for launch — awaiting user approval.
> Goal: Craft prompt → get user approval → delegate to teamwork_preview
> Requested team: The full multi-agent teamwork system

Refactor the `auto-speech` architecture into a Unified Daemon Server. Eliminate the `run_speak.sh` process sprawl, integrate the MLX Kokoro TTS engine in-process for zero-latency streaming, and replace the detached `mpv` and brittle `time.sleep` hacks with a clean, synchronous `NativeAudioSink`.

Working directory: /Users/joshua/Developer/auto-speech
Integrity mode: development

## Requirements

### R1. In-Process TTSEngine and Blocking AudioSink
Refactor `narrator_service.py` to instantiate `TTSEngine` directly in-process. Create a `NativeAudioSink` that plays the generated WAV files synchronously using `subprocess.run(["mpv", "--really-quiet", ...])`, completely removing the `run_speak.sh` script, the detached `mpv` background session logic, the `time.sleep(duration)` calculations, and the `SIGKILL` hacks.

### R2. Thin Client IPC via UNIX Sockets
Refactor `speak.py` into a thin CLI client that reads `stdin` and forwards the text to the daemon. In `narrator_service.py`, run a background thread using Python's `socketserver` to listen on a UNIX domain socket (e.g., `/tmp/auto-speech-daemon.sock`), enqueueing incoming speech requests into the main `_tts_queue`.

### R3. Remove Dead Architectural Sprawl
Delete obsolete files including `run_speak.sh`, `PipelineOrchestrator`, `ShortPathStrategy`, `MpvController`, and the `SessionDir` PID management, as the daemon now acts as the single authoritative owner of the audio hardware.

## Acceptance Criteria

### Execution & Architecture
- [ ] `narrator_service.py` boots successfully, loads `TTSEngine` into memory once, and synthesizes tool narrations without spinning up secondary Python interpreters.
- [ ] Tool narrations play sequentially via `mpv` in blocking mode; the Python thread naturally waits for the audio to finish without `time.sleep()` hacks.
- [ ] Manual test: Running `echo "test" | python3 plugin/scripts/python/speak.py` successfully sends text to the daemon's UNIX socket, which synthesizes and plays it without overlapping existing audio.
- [ ] No detached `mpv` processes are left running in the background after playback finishes.
