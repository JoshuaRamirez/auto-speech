# Task Assignment: Explorer M1.2 (In-Process TTSEngine Integration)

You are explorer_m1_2 (teamwork_preview_explorer).
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_2
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md

## Objective
Read ORIGINAL_REQUEST.md and PROJECT.md first.
You are investigating Milestone M1: In-process `TTSEngine` integration into `narrator_service.py`.
Specify the exact code changes for `narrator_service.py`:
1. In-process `TTSEngine` and `ResilientSynthesizer` initialization:
   - Ensure initialization occurs in `_tts_worker` (or is prewarmed cleanly respecting Apple MLX stream affinity).
   - Use default `VoiceProfile` (or configured voice profile).
2. Synthesis in `_speak(self, line: str)`:
   - Synthesize to a temporary WAV file using `self._synth.synthesize_one(line, profile, temp_wav_path)`.
   - Call `self._sink.play(temp_wav_path)`.
   - Ensure temporary WAV file is deleted in a `finally:` block.
   - Replace the legacy `_speak_script()` execution completely.
3. Thread and queue safety:
   - Ensure `_tts_worker` handles errors in synthesis or playback without crashing the thread loop.

Write your findings to `/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_2/analysis.md` and `handoff.md`. Send a message when done.


## 2026-10-03T18:00:30Z
You are explorer_m1_2.
Your working directory: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_2
Project root: /Users/joshua/Developer/auto-speech
Original request: /Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md
Project specification: /Users/joshua/Developer/auto-speech/PROJECT.md

Read ORIGINAL_REQUEST.md and PROJECT.md first. Read your task assignment in /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m1_2/DISPATCH.md.
Specify the exact in-process TTSEngine integration for narrator_service.py on the _tts_worker thread, replacing _speak_script() with ResilientSynthesizer + NativeAudioSink and temporary WAV cleanup.
Write findings to analysis.md and handoff.md. Send a message when done.
