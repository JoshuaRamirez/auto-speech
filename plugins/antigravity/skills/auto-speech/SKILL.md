---
name: auto-speech
description: Speak text aloud on macOS using native Apple neural voice synthesis, replay recent lines from the speech history, control playback, manage autoplay/narration, and run diagnostics via the unified auto-speech CLI or MCP server.
---

# Auto-Speech Skill

Use this skill when you want to speak responses aloud, trigger turn autoplay, stream tool narration events, control playback transport, manage session enrollment, or diagnose the local text-to-speech service.

## Dual Control Surfaces

Auto-speech provides two equivalent interfaces for humans and AI agents:
1. **Unified CLI**: Executable from anywhere via `auto-speech <subcommand>`.
2. **MCP Server**: 6-tool JSON-RPC suite via stdio for programmatic agent control.

---

## 1. Direct Speech & Replay

### CLI
- **Speak verbatim**:
  ```bash
  auto-speech speak "Task completed successfully."
  echo "Piped text to read" | auto-speech speak
  ```
- **Replay recent speech**:
  ```bash
  auto-speech replay              # Replay the newest line of the speech history
  auto-speech replay --ordinal 2  # Replay the line before it
  ```

### MCP Tools
- `speak(text="...")`: Queues arbitrary text aloud verbatim (Priority 2).
- `playback_control(action="replay", ordinal=1)`: Replays a line of the speech history by ordinal (1 = newest).

---

## 2. Autoplay & Turn Narration

### CLI
- **Manage session enrollment**:
  ```bash
  auto-speech autoplay on       # Enroll current session
  auto-speech autoplay off      # Withdraw enrollment
  auto-speech autoplay status   # Check enrollment & daemon status
  auto-speech autoplay mode [verbatim|small|medium|large]
  auto-speech autoplay scope [all|solo]
  ```
- **Run hook simulation**:
  ```bash
  auto-speech hook stop < payload.json
  ```

### MCP Tools
- `autoplay_turn(session_id="...", response_text="...", mode="small")`: Processes and reads end-of-turn responses, running the appropriate summarization/rewriting pipeline identically to the `Stop` hook.
- `session_configure(subsystem="autoplay", action="enroll", session_id="...", mode="small")`: Configures session enrollment, modes, and solo scoping.

---

## 3. Tool Narration Subsystem

### CLI
- **Manage narration**:
  ```bash
  auto-speech narrate on      # Enable real-time tool narration
  auto-speech narrate off     # Disable narration
  auto-speech narrate status  # Check queue depth and logs
  auto-speech narrate stop    # Stop narrator daemon
  ```

### MCP Tools
- `narrate_event(event="PreToolUse", payload={...}, session_id="...")`: Emits lifecycle activity events into the narration pipeline to announce tool calls in real-time.
- `session_configure(subsystem="narrator", action="enroll", session_id="...")`: Toggles per-session narration opt-in.

---

## 4. Playback Transport Controls

### CLI
```bash
auto-speech pause
auto-speech resume
auto-speech restart
auto-speech seek +15       # Relative seek forward 15s
auto-speech seek -10       # Relative seek backward 10s
auto-speech seek 45        # Absolute seek to 45s
auto-speech stop           # Immediate stop & queue purge
```

### MCP Tools
- `playback_control(action="pause"|"resume"|"restart"|"stop"|"seek", target="+15")`

---

## 5. System Health & Diagnostics

### CLI
```bash
auto-speech doctor         # Formatted diagnostic report
auto-speech doctor --json  # Machine-readable JSON output
auto-speech update         # Reconcile venv dependencies with uv.lock
```

### MCP Tools
- `doctor(json_output=True)`: Returns structured health inspection of mpv, venv, disk headroom, daemon status, and queue depth.

---

## 6. Output & Synthesis Guidelines
- **Verbatim Voice**: The text is spoken exactly as passed. Avoid raw markdown fences, ASCII tables, backticks, or raw URLs.
- **Global Panic Mute**: All speech paths respect `~/.claude/auto-speech.disabled`. If muted, commands return safely without speaking.
