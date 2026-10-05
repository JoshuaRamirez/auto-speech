---
name: auto-speech
description: Speak text aloud on macOS using native Apple neural voice synthesis, replay cached audio, control playback, and manage the auto-speech daemon.
---

# Auto-Speech Skill

Use this skill when you want to speak responses aloud, replay previous turns, manage audio playback, or diagnose the local text-to-speech service.

## Core Capabilities

### 1. Speak Aloud Verbatim
To speak text aloud through the Mac's speakers:
- **MCP Tool**: Call `auto-speech:speak(text="...")`.
- **CLI**: Pipe text to `speak.py`:
  ```bash
  echo "Task completed successfully." | python3 plugin/scripts/python/speak.py
  ```
- **Guidelines**:
  - Write text naturally for listening (no markdown asterisks, raw backticks, or code blocks).
  - Sentences are processed with Priority 2 (Explicit MCP) and queued cleanly.

### 2. Replay Previous Speech
To replay the most recent (or Nth most recent) cached audio:
```bash
python3 plugin/scripts/python/replay.py --ordinal 1
```

### 3. Playback Controls
To pause, resume, seek, or check playback status:
```bash
python3 plugin/scripts/python/control.py status
python3 plugin/scripts/python/control.py pause
python3 plugin/scripts/python/control.py resume
```

### 4. Diagnostics & Health
To check the audio daemon, dependencies, socket connection, and voice profile:
```bash
python3 plugin/scripts/python/doctor.py
```

### 5. Engine Architecture
- **macOS Default Engine**: `AppleSayEngine` using native neural voice (`Ava (Premium)`), offering 0.0ms cold-boot latency and 0MB RAM footprint.
- **Optional/Headless Engine**: `Kokoro MLX` via Apple Silicon MLX (activated with `AUTO_SPEECH_TTS_ENGINE=kokoro`).
