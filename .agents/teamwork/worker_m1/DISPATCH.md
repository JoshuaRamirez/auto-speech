# Task Assignment: Worker M1 (In-Process TTSEngine & NativeAudioSink)

You are worker_m1 (teamwork_preview_worker).
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m1
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md

## Reference Analysis Reports
Read these reports carefully before writing code:
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_1/analysis.md (NativeAudioSink specification and unit tests)
- /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_2/analysis.md (In-process TTSEngine integration in narrator_service.py)
- /Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m1_3/analysis.md (Hacks removal, line 237 bug fix, unit test verification)

## File Ownership
You exclusively own and may edit:
- `plugin/scripts/python/native_audio_sink.py` (NEW)
- `plugin/scripts/python/narrator_service.py` (MODIFIED)
- `tests/test_native_audio_sink.py` (NEW)
- `tests/test_narrator_service.py` (MODIFIED)
Do NOT modify files in `tests/e2e/` or any other modules.

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Tasks
1. Implement `NativeAudioSink` in `plugin/scripts/python/native_audio_sink.py`:
   - Synchronous, blocking playback via `subprocess.Popen(["mpv", "--really-quiet", "--no-video", "--keep-open=no", "--idle=no", str(wav_path)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)` with `proc.communicate()`.
   - Dual-lock design: `_playback_lock` for FIFO playback serialization and `_state_lock` for non-blocking process inspection.
   - Clean `interrupt()` method that sends `proc.terminate()` (SIGTERM) with bounded 0.5s escalation to `proc.kill()` (SIGKILL).
   - If interrupted, return cleanly (`None`); if `retcode != 0`, raise `PlaybackError(stderr)`; pre-flight checks for `FileNotFoundError` and `MpvNotInstalledError`.
2. Refactor `plugin/scripts/python/narrator_service.py`:
   - Instantiate `NativeAudioSink` as `self._sink = sink or NativeAudioSink()`.
   - In `_tail_events`: fix line 237 `_classifier` bug using `classifier = getattr(self, "_classifier", None)`.
   - On `UserPromptSubmit`: replace `subprocess.run(["pkill", "-9", "mpv"])` with `self._sink.interrupt()`; delete duplicate dead code block (lines 333–350).
   - In `_tts_worker`: lazy-initialize `TTSEngine` (or injected engine) and `ResilientSynthesizer` on the `_tts_worker` thread (preserving Apple MLX per-thread compute stream affinity).
   - In `_speak(self, line: str)`: synthesize in-process to temporary WAV using `self._synth.synthesize_one(line, self._profile, temp_wav_path)`, then play synchronously via `self._sink.play(temp_wav_path)`. In `finally:` block, ensure `temp_wav_path.unlink(missing_ok=True)` and clean any `.partial` files.
   - Remove legacy hacks: remove `_speak_script()`, `run_speak.sh` subprocess execution, `SessionDir.wav_path_path()`, `wave.open` duration calculation + `time.sleep()`, `SessionDir.read_pid()`, `SIGKILL`, and `_wait_mpv_idle()`.
3. Add and run tests:
   - Create `tests/test_native_audio_sink.py` covering normal playback, interrupt, error handling, sequential playback.
   - Run `.venv/bin/python tests/test_native_audio_sink.py`.
   - Run `.venv/bin/python tests/test_narrator_service.py` (verify 15/15 tests pass + new in-process tests).
   - Run `.venv/bin/python tests/e2e/run_e2e.py --tier 1` to observe M1 E2E tests passing.
4. Document all changes, test outputs, and commands in `handoff.md`. Send completion message when done.


## 2026-10-03T18:12:08Z
[Message] timestamp=2026-10-03T18:12:08Z sender=c05df6b8-cecd-49ba-9fb8-8fa47f977488 priority=MESSAGE_PRIORITY_HIGH content=You are worker_m1.
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m1
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md

Read ORIGINAL_REQUEST.md and PROJECT.md first. Read your task assignment in /Users/joshua/Developer/auto-speech/.agents/teamwork/worker_m1/DISPATCH.md.
Review reference reports in explorer_m1_1/analysis.md, explorer_m1_2/analysis.md, and spec_miner_m1_3/analysis.md.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_preview_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Implement:
1. NativeAudioSink in plugin/scripts/python/native_audio_sink.py.
2. Refactor narrator_service.py for in-process TTSEngine, NativeAudioSink, line 237 bug fix, and complete removal of time.sleep/SIGKILL hacks.
3. Tests in tests/test_native_audio_sink.py and tests/test_narrator_service.py.
4. Execute tests and report all passing results in handoff.md. Send a message when done.
