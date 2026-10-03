# BRIEFING — 2026-10-03T18:06:00Z

## Mission
Investigate and specify the exact in-process TTSEngine integration for narrator_service.py on the _tts_worker thread, replacing _speak_script() with ResilientSynthesizer + NativeAudioSink and temporary WAV cleanup.

## 🔒 My Identity
- Archetype: explorer
- Roles: investigation, synthesis
- Working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_2
- Original parent: c05df6b8-cecd-49ba-9fb8-8fa47f977488
- Milestone: M1

## 🔒 Key Constraints
- Read-only investigation — do NOT implement code in project source files
- Write findings only to /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_2/
- Specify exact changes: TTSEngine & ResilientSynthesizer initialization on _tts_worker, synthesis in _speak() to temp WAV, NativeAudioSink playback, temp WAV cleanup in finally block, thread error handling
- Produce analysis.md and handoff.md

## Current Parent
- Conversation ID: c05df6b8-cecd-49ba-9fb8-8fa47f977488
- Updated: not yet

## Investigation State
- **Explored paths**: ORIGINAL_REQUEST.md, PROJECT.md, DISPATCH.md, narrator_service.py, resilient_synthesizer.py, tts_engine.py, voice_profile.py, voice_profile_store.py, config_constants.py, web_server.py, test_narrator_service.py
- **Key findings**:
  - Apple MLX compute streams are per-thread; TTSEngine must be loaded and used strictly on the `_tts_worker` thread via `_ensure_tts_initialized()`.
  - `ResilientSynthesizer.synthesize_one()` writes directly to `temp_wav`, automatically splitting on Kokoro generation faults and returning True if audio was generated.
  - Synchronous `NativeAudioSink.play(temp_wav)` blocks naturally, completely eliminating duration estimation, `time.sleep()`, `SessionDir`, and `SIGKILL`.
  - Temporary WAV files, partial files (`.partial`), and split fragments (`f"{temp_wav.stem}-*.wav"`) must be unconditionally cleaned up in `finally:`.
  - Error handling in both `_speak()` and `_tts_worker()` catches exceptions, ensuring `task_done()` is called and the worker thread loop never crashes.
- **Unexplored areas**: None for M1 in-process TTSEngine integration.

## Key Decisions Made
- `NativeAudioSink` instantiated on `NarratorService.__init__` with optional dependency injection, enabling `_sink.interrupt()` from main thread on `UserPromptSubmit` and during shutdown.
- `TTSEngine` and `ResilientSynthesizer` instantiated and loaded lazily/prewarmed on `_tts_worker` thread to strictly enforce MLX single-thread stream affinity.
- Complete replacement of `_speak()` with clean `ResilientSynthesizer` + `NativeAudioSink` pipeline and `finally:` file cleanup.
- Removal of `_speak_script()`, `_wait_mpv_idle()`, duplicate `UserPromptSubmit` blocks, `SessionDir`, `wave.open`, `time.sleep`, and `SIGKILL`.

## Artifact Index
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_2/DISPATCH.md — Task assignment and dispatch log
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_2/BRIEFING.md — Persistent state and identity
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_2/progress.md — Heartbeat and step tracking
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_2/analysis.md — Comprehensive technical analysis
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_2/handoff.md — 5-component handoff report
