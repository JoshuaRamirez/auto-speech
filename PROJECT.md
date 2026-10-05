# Project: auto-speech Unified Daemon Server

## Architecture
The `auto-speech` system is refactored from a multi-process, detached shell-sprawl architecture into a single Unified Daemon Server.

```
                    CLI Clients (speak.py, hooks)
                                 │
                                 ▼ (UNIX Domain Socket: /tmp/auto-speech-daemon.sock)
               ┌──────────────────────────────────────────────┐
               │         narrator_service.py (Daemon)         │
               │                                              │
Events JSONL ─►│ ┌──────────────┐         ┌─────────────────┐ │
               │ │ _tail_events │         │ SocketServer    │ │
               │ └──────┬───────┘         └────────┬────────┘ │
               │        │                          │          │
               │        ▼                          ▼          │
               │       _tts_queue (FIFO, drop-oldest cap: 32)  │
               │                                   │          │
               │                                   ▼          │
               │                          _tts_worker thread  │
               │                         (MLX stream-affinity)│
               │                                   │          │
               │                   ┌───────────────┴────────┐ │
               │                   │                        │ │
               │                   ▼                        ▼ │
               │           ResilientSynthesizer      NativeAudioSink
               │           (in-process TTSEngine)   (synchronous mpv)
               │                   │                        │ │
               │                   └───────────────┬────────┘ │
               └───────────────────────────────────┼──────────┘
                                                   ▼
                                         Audio Output (Speakers)
```

### Key Principles:
1. **Single Authoritative Hardware Owner**: The daemon owns the audio output hardware exclusively.
2. **In-Process MLX Kokoro TTS**: `TTSEngine` runs in-process inside `_tts_worker`, honoring Apple MLX single-thread stream affinity and eliminating secondary Python interpreter spawns.
3. **Synchronous NativeAudioSink**: Playback runs sequentially and synchronously via `subprocess.run(["mpv", "--really-quiet", "--no-video", ...])`, completely removing duration guessing, `time.sleep()`, detached background sessions, and `SIGKILL`/`pkill` hacks.
4. **Thin Client IPC**: `speak.py` is a lightweight CLI forwarding text over `/tmp/auto-speech-daemon.sock` to the daemon's internal `_tts_queue`.
5. **Zero Architectural Sprawl**: Elimination of `run_speak.sh`, `PipelineOrchestrator`, `ShortPathStrategy`, `MpvController`, and `SessionDir`.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | NativeAudioSink | Synchronous, blocking audio sink using `mpv` without detached sessions or `time.sleep` | M1 | ORIGINAL_REQUEST §R1 |
| 2 | In-Process TTSEngine | Direct instantiation and invocation of `TTSEngine` in `narrator_service.py` on `_tts_worker` | M1 | ORIGINAL_REQUEST §R1 |
| 3 | NarratorService Cleanup | Remove `time.sleep`, `SIGKILL`/`pkill`, fix line 237 bug, remove duplicate code block | M1 | Survey Reports 1 & 3 |
| 4 | Daemon UNIX Socket Server | `socketserver.ThreadingUnixStreamServer` at `/tmp/auto-speech-daemon.sock` feeding `_tts_queue` | M2 | ORIGINAL_REQUEST §R2 |
| 5 | Thin Client speak.py | Thin CLI client reading stdin/args and transmitting to `/tmp/auto-speech-daemon.sock` | M2 | ORIGINAL_REQUEST §R2 |
| 6 | Delete Dead Architectural Sprawl | Delete `run_speak.sh`, `pipeline.py`, `short_path.py`, `mpv_controller.py`, `session_dir.py`, `test_mpv_wait.py` | M3 | ORIGINAL_REQUEST §R3 |
| 7 | Adapt Sprawl Callers | Adapt `autoplay_worker.py`, `say_worker.py`, `web_server.py`, `replay.py`, `control.py`, docs | M3 | Survey Report 3 |
| 8 | E2E Test Suite (Tiers 1-4) | Comprehensive opaque-box test suite for daemon lifecycle, socket IPC, sequential playback, no orphans | M4 (Phase 1) | ORIGINAL_REQUEST Acceptance Criteria |
| 9 | Adversarial Coverage Hardening | White-box stress testing and gap coverage for daemon concurrency, error recovery, socket disconnects | M4 (Phase 2) | Dual Track Requirement |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | In-Process TTSEngine & NativeAudioSink | Create `NativeAudioSink`, integrate `TTSEngine` into `narrator_service.py`, remove playback sleeps & kills | none | DONE |
| M2 | Thin Client IPC via UNIX Sockets | Add daemon socket server on `/tmp/auto-speech-daemon.sock`, refactor `speak.py` to thin client | M1 | DONE |
| M3 | Elimination of Dead Sprawl & Caller Realignment | Delete `run_speak.sh`, `pipeline.py`, `short_path.py`, `mpv_controller.py`, `session_dir.py`, adapt callers | M1, M2 | DONE |
| M4 | Final Milestone: E2E Verification & Adversarial Hardening | Phase 1: 100% pass of E2E tests (Tiers 1-4). Phase 2: Tier 5 adversarial hardening | M1, M2, M3, E2E Track | DONE |

## Interface Contracts
### UNIX Socket IPC: `speak.py` ↔ `narrator_service.py`
- **Socket Path**: `/tmp/auto-speech-daemon.sock`
- **Wire Protocol**: Stream-oriented UTF-8 text transmission.
  - Client connects to `/tmp/auto-speech-daemon.sock`.
  - Client sends UTF-8 encoded text payload and shuts down write (`socket.SHUT_WR`) or closes connection.
  - Server accepts connection, reads incoming payload until EOF, strips whitespace, and if non-empty, enqueues to `_tts_queue`.
  - Server returns immediate ACK or closes connection.
- **Error Handling**: If daemon socket does not exist or connection is refused, client outputs error to stderr and exits with non-zero exit code (or attempts launch if configured).

### Audio Sink: `NativeAudioSink`
- **Interface**:
  ```python
  class NativeAudioSink:
      def play(self, wav_path: Path) -> None:
          """Plays wav file synchronously via mpv, blocking until playback finishes."""
      def interrupt(self) -> None:
          """Terminates any currently active mpv playback process."""
  ```
- **CLI Invocations**:
  `mpv --really-quiet --no-video --keep-open=no --idle=no <wav_path>`

## Code Layout
- `plugin/scripts/python/native_audio_sink.py`: New module defining `NativeAudioSink`.
- `plugin/scripts/python/narrator_service.py`: Modified to host `TTSEngine`, `NativeAudioSink`, and UNIX socket server.
- `plugin/scripts/python/speak.py`: Refactored thin client.
- `plugin/scripts/shell/run_speak.sh`: DELETED.
- `plugin/scripts/python/pipeline.py`: DELETED.
- `plugin/scripts/python/short_path.py`: DELETED.
- `plugin/scripts/python/mpv_controller.py`: DELETED.
- `plugin/scripts/python/session_dir.py`: DELETED.
- `tests/test_mpv_wait.py`: DELETED.
- `tests/e2e/`: E2E test suite directory created by E2E Testing Track.
