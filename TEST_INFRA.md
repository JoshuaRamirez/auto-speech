# E2E Test Infrastructure: auto-speech Unified Daemon Server

## 1. Architectural Overview

The `auto-speech` system is refactored from a multi-process, detached shell-sprawl architecture into a single Unified Daemon Server. The end-to-end (E2E) test suite validates the system as an opaque box against user requirements and acceptance criteria defined in `ORIGINAL_REQUEST.md` and `PROJECT.md`.

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

The test infrastructure exercises the three core functional requirements:
- **R1: In-Process TTSEngine and Blocking AudioSink**: Direct in-process synthesis and synchronous playback via `NativeAudioSink` without detached background sessions, duration guessing, `time.sleep()`, or `SIGKILL`/`pkill` hacks.
- **R2: Thin Client IPC via UNIX Sockets**: Lightweight CLI client `speak.py` piping `stdin` to `/tmp/auto-speech-daemon.sock`, handled by a background `ThreadingUnixStreamServer` enqueueing into `_tts_queue`.
- **R3: Removal of Dead Architectural Sprawl**: Verification of complete deletion of obsolete components (`run_speak.sh`, `PipelineOrchestrator`, `ShortPathStrategy`, `MpvController`, `SessionDir`, `test_mpv_wait.py`) and confirmation that all surviving modules import cleanly without broken dependencies.

---

## 2. 4-Tier Test Methodology

The E2E test suite adheres to the 4-tier testing hierarchy:

| Tier | Focus | Test Count | Description |
|------|-------|------------|-------------|
| **Tier 1** | Feature Coverage | 18 tests | Baseline happy-path and contract verification (>=5 tests each for R1, R2, R3). |
| **Tier 2** | Boundary & Corner Cases | 15 tests | Stress and edge conditions (empty inputs, large payloads, disconnects, special chars, stale sockets). |
| **Tier 3** | Cross-Feature Interactions | 5 tests | Pairwise concurrent behaviors (socket IPC + JSONL event streaming, backpressure queue cap, bursts). |
| **Tier 4** | Real-World Workflows | 3 tests | Full session lifecycles (boot -> stream -> CLI speak -> interrupt -> shutdown -> cleanup). |
| **Total** | Full Suite | **41 tests** | 100% requirements and acceptance criteria coverage. |

---

## 3. Test Coverage Matrix

### Tier 1: Feature Coverage (18 Tests)

#### R1: In-Process TTSEngine & Blocking AudioSink
- `test_tier1_r1_native_audio_sink_plays_synchronously`: Verifies `NativeAudioSink.play(wav_path)` runs `mpv` synchronously, blocking until playback finishes with flags `--really-quiet --no-video --keep-open=no --idle=no`.
- `test_tier1_r1_native_audio_sink_interrupt`: Verifies `NativeAudioSink.interrupt()` immediately terminates the active playback process and unblocks the thread.
- `test_tier1_r1_tts_engine_instantiated_in_process`: Verifies `NarratorService` loads `TTSEngine` directly in-process without spinning up secondary Python processes or shell scripts.
- `test_tier1_r1_sequential_playback_fifo`: Verifies queued utterances are played strictly sequentially without concurrent overlapping `mpv` processes.
- `test_tier1_r1_no_orphan_mpv_processes`: Verifies no detached or orphaned `mpv` processes are left running after playback completes or on daemon shutdown.
- `test_tier1_r1_no_time_sleep_or_sigkill_hacks`: Verifies source code of `narrator_service.py` is free of `time.sleep` duration guessing and `SIGKILL`/`pkill` hacks.

#### R2: Thin Client IPC via UNIX Sockets
- `test_tier1_r2_speak_cli_transmits_stdin_to_socket`: Verifies `echo "text" | python3 speak.py` sends UTF-8 text to `/tmp/auto-speech-daemon.sock` and exits 0.
- `test_tier1_r2_daemon_socket_enqueues_to_tts_queue`: Verifies the daemon's UNIX socket server accepts connections, reads incoming payloads until EOF, and enqueues to `_tts_queue`.
- `test_tier1_r2_socket_wire_protocol_stream_handling`: Verifies stream-oriented wire protocol (UTF-8 encoding, shutdown write, EOF framing).
- `test_tier1_r2_speak_cli_fails_gracefully_when_daemon_down`: Verifies `speak.py` exits with non-zero exit code and writes an error message to `stderr` when daemon socket is absent or connection is refused.
- `test_tier1_r2_daemon_cleans_up_socket_file_on_shutdown`: Verifies `/tmp/auto-speech-daemon.sock` is automatically unlinked and removed upon daemon shutdown.
- `test_tier1_r2_speak_cli_accepts_backward_compatible_args`: Verifies `speak.py` accepts `--ordinal`, `--keep-artifacts`, and `--source-hash` flags without argument errors.

#### R3: Removal of Dead Architectural Sprawl
- `test_tier1_r3_obsolete_files_deleted`: Verifies deletion of `run_speak.sh`, `pipeline.py`, `short_path.py`, `mpv_controller.py`, `session_dir.py`, and `test_mpv_wait.py`.
- `test_tier1_r3_no_pipeline_orchestrator_imports`: Verifies zero imports or usages of `PipelineOrchestrator` across the codebase.
- `test_tier1_r3_no_short_path_strategy_imports`: Verifies zero imports or usages of `ShortPathStrategy` across the codebase.
- `test_tier1_r3_no_mpv_controller_imports`: Verifies zero imports or usages of `MpvController` across the codebase.
- `test_tier1_r3_no_session_dir_imports`: Verifies zero imports or usages of `SessionDir` across the codebase.
- `test_tier1_r3_no_run_speak_sh_references`: Verifies zero references to `run_speak.sh` in active python and shell scripts.

---

### Tier 2: Boundary & Corner Cases (15 Tests)

#### R1 Boundaries
- `test_tier2_r1_empty_and_whitespace_text_handling`: Empty or whitespace-only strings passed to speech worker do not crash or create corrupt audio.
- `test_tier2_r1_extremely_large_text_synthesis`: Multi-paragraph / large text inputs (5,000+ characters) synthesize without buffer overflow or thread crashes.
- `test_tier2_r1_missing_wav_file_handling`: `NativeAudioSink.play()` handles non-existent WAV files cleanly without corrupting sink state.
- `test_tier2_r1_idempotent_interrupt`: Multiple rapid calls to `interrupt()` or interrupting when idle are safe and idempotent.
- `test_tier2_r1_temporary_wav_cleanup`: Temporary WAV files generated during speech synthesis are unlinked after playback, preventing disk leaks.

#### R2 Boundaries
- `test_tier2_r2_empty_stdin_ignored_by_daemon`: Empty stdin to `speak.py` does not enqueue empty items to `_tts_queue`.
- `test_tier2_r2_special_characters_and_multiline_payload`: Emojis, quotes, shell metacharacters, and multiline text are transmitted intact over the socket.
- `test_tier2_r2_large_socket_payload_chunking`: Large payloads (128 KB+) crossing socket buffer boundaries are fully received without truncation.
- `test_tier2_r2_abrupt_client_disconnect`: Abrupt client socket disconnects or premature closures are handled gracefully by the server without crashing.
- `test_tier2_r2_stale_socket_file_cleanup_on_startup`: Pre-existing stale socket files from prior ungraceful terminations are cleaned up and rebound on daemon start.

#### R3 Boundaries
- `test_tier2_r3_surviving_callers_importable`: Surviving modules (`autoplay_worker.py`, `say_worker.py`, `web_server.py`, `replay.py`, `control.py`) import cleanly.
- `test_tier2_r3_absence_of_session_dir_tmp_directory`: System functions completely without `/tmp/auto-speech/` session directory.
- `test_tier2_r3_test_runner_clean_of_deleted_tests`: `tests/run_all.sh` does not reference deleted test files.
- `test_tier2_r3_markdown_and_commands_clean`: Slash command definitions (`auto-speech-speak.md`) do not invoke deleted scripts.
- `test_tier2_r3_all_surviving_scripts_pass_syntax_check`: All surviving Python files compile cleanly with `py_compile`.

---

### Tier 3: Cross-Feature Combinations (5 Tests)

- `test_tier3_concurrent_socket_and_jsonl_events`: Concurrent arrivals of socket IPC requests and JSONL tool events are ingested and queued without collision.
- `test_tier3_backpressure_queue_cap_with_socket_burst`: Sending requests exceeding `max_queue_depth` (32) exercises drop-oldest eviction without deadlocking clients.
- `test_tier3_multi_client_concurrent_burst`: Parallel `speak.py` client processes simultaneously connect and enqueue without dropped requests or server crashes.
- `test_tier3_playback_busy_queue_accumulation`: While playback is active, incoming socket messages queue cleanly and wait for sequential execution.
- `test_tier3_user_prompt_interrupt_flushes_socket_and_event_audio`: A `UserPromptSubmit` event during active playback triggers `interrupt()` and flushes the queue.

---

### Tier 4: Real-World Scenarios (3 Tests)

- `test_tier4_scenario_full_user_session_lifecycle`: Complete end-to-end user session: daemon boots -> streams tool event narration -> executes `speak.py` CLI -> intercepts prompt submit -> shuts down cleanly -> unlinks socket and PID files.
- `test_tier4_scenario_high_load_mixed_traffic`: High-intensity mixed traffic scenario verifying queue drain, zero memory growth, and zero process leakage.
- `test_tier4_scenario_daemon_reboot_and_client_reconnection`: Daemon restart resilience: client fails when daemon is stopped, and immediately succeeds once daemon restarts.

---

## 4. Test Infrastructure Architecture & Harness

The test suite is located in `tests/e2e/`:

```
tests/e2e/
├── __init__.py
├── harness.py                     # Test environment sandbox, spy mpv, socket client helper
├── run_e2e.py                     # CLI test runner with tier filtering and formatted reporting
├── test_unified_daemon_e2e.py     # Unified entry point running Tiers 1-4
├── test_tier1_features.py         # Tier 1: Feature Coverage (R1, R2, R3)
├── test_tier2_boundaries.py       # Tier 2: Boundary & Corner Cases
├── test_tier3_combinations.py     # Tier 3: Cross-Feature Interactions
└── test_tier4_scenarios.py        # Tier 4: Real-World Workflows
```

### Test Harness Components (`harness.py`):
1. **`IsolatedEnvironment`**: Creates temporary sandboxes with dedicated socket, PID, events, and log paths to prevent interference with running user daemons or system audio.
2. **`SpyMpv`**: Intercepts `mpv` invocations to record command-line arguments, verify `--really-quiet --no-video --keep-open=no --idle=no`, measure synchronous blocking behavior, and track active process IDs without emitting sound.
3. **`SocketClientHelper`**: Programmatic UNIX domain socket client to verify wire protocol, test concurrency, and simulate malformed payloads.

---

## 5. How to Run the Tests

The test suite runs with standard Python in the virtual environment without requiring third-party testing dependencies:

```bash
# Run the entire E2E test suite (Tiers 1-4)
.venv/bin/python tests/e2e/run_e2e.py

# Run a specific tier
.venv/bin/python tests/e2e/run_e2e.py --tier 1
.venv/bin/python tests/e2e/run_e2e.py --tier 2
.venv/bin/python tests/e2e/run_e2e.py --tier 3
.venv/bin/python tests/e2e/run_e2e.py --tier 4

# Run via standard unittest discovery
.venv/bin/python -m unittest discover tests/e2e

# Run via pytest (if installed in dev environment)
pytest tests/e2e/
```
