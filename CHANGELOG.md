# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The Chrome extension under `chrome-extension/` is versioned independently
(its own changelog lives in `chrome-extension/README.md`).

## [Unreleased]

### Fixed

- Leftover 0.1.0 opt-OUT copy on current surfaces now matches 0.2.0
  opt-IN: `/auto-speech-autoplay-status`, `/auto-speech-doctor`,
  `/auto-speech-scope`, and `docs/OPERATIONS.md`. Doctor reports `jq`.

## [0.2.0] - 2026-08-12

Autoplay actually works now — and it no longer speaks unless asked.

### Changed

- **BREAKING — autoplay is opt-IN per session.** It previously played in
  every session by default, with a marker to silence one. Enable a session
  with `/auto-speech-autoplay-on`; every other session stays silent.
  Enrollment markers live in a NEW directory
  (`~/.claude/auto-speech-autoplay-enabled/<session_id>`); the old
  `auto-speech-autoplay-sessions/` held opt-OUT markers and is never read
  as enrollment, so sessions that had asked for silence are not flipped on.
  `/auto-speech-scope solo` now enrolls the soloed session.
- A session whose id cannot be resolved (e.g. `jq` missing) no longer
  plays. Under the opt-out default it was deliberately let through to
  avoid total silence; under opt-in the safe direction is the opposite.
  `/auto-speech-doctor` reports `jq`.

### Fixed

- **Autoplay never played at all.** The Stop hook captured the beacon
  mtime with `stat -f %m` (whole seconds) and the worker compared it
  against Python's float `st_mtime`, so every worker read its own beacon
  as newer, declared itself superseded, and bailed during the coalesce
  window — silently, with no error surfaced. Captured at full precision,
  with a tolerance for float round-trip.
- **Every web playback after the first returned HTTP 500.** The web server
  holds one `MpvController` for the process lifetime and never calls
  `stop()`, leaving its FSM at `READY`; the next `start()` raised
  `IllegalTransition`, uncaught in both the speak and replay handlers.
- **A single bad phrase silenced a whole message on the CLI path.** The
  retry-split recovery for mlx-audio's content-dependent generate fault
  existed only in the web server, so `/auto-speech-speak` exited 5 with no
  audio. Extracted into shared `SpanSplitter` + `ResilientSynthesizer`
  collaborators used by every path; a chunk with nothing speakable now
  degrades to a gap instead of failing the message.
- The narrator dropped events that straddled a poll boundary: the tail
  advanced its offset past a partially-written line, losing both the
  fragment and its remainder. It now consumes only complete lines.
- A malformed `AUTO_SPEECH_AUTOPLAY_COALESCE` /
  `AUTO_SPEECH_NARRATION_WAIT_MAX` killed the autoplay worker with a
  traceback and no log line, violating its always-exit-0 contract.
- Type-confused JSON (`{"text": 123}`, non-string `voice`/`hash`/`target`,
  non-numeric `rewrite_timeout_s`) returned 500 instead of 400 across
  `/api/speak`, `/api/synthesize`, `/api/replay` and `/api/seek`.

### Added

- `SessionEnrollment` — testable model + writer for autoplay opt-in,
  mirroring `SoloScope`'s shape.
- Regression tests for each fix above, including the hook→worker mtime
  handoff the prior suite bypassed entirely, and the three-state hook gate
  (unenrolled bails / enrolled proceeds / global mute overrides).

## [0.1.0] - 2026-07-22

First public release.

### Added

- Local Kokoro TTS on Apple Silicon (mlx-audio) with Fibonacci-scaled
  buffered playback via mpv — sub-second time-to-first-audio on long texts —
  and a source-hash replay cache.
- End-of-turn autoplay via a Claude Code Stop hook: `verbatim` / `small` /
  `medium` / `large` modes, on by default with per-session opt-out, all/solo
  session scope, cross-session FIFO queue with coalescing and dedup.
- Optional real-time narrator subsystem: hook event stream → phase
  classifier → local MLX LLM → spoken one-liners while Claude works.
- Localhost web app (`127.0.0.1:7860`): paste-to-speak with Claude-CLI
  rewrite, playback transport, replay-cache browser, `/api/synthesize` +
  `/api/voices` for the browser extension, multi-voice with per-voice
  language codes, resilient chunk-splitting around an mlx-audio
  long-input bug.
- Chrome extension "AutoSpeech" (v0.4.0, independently versioned):
  right-click speak-selection backed by the local Kokoro server, browser-
  voice fallback, live voice picker, minimal host permissions.
- 8 curated Claude Code slash commands (speak, replay, app, autoplay-on/
  off/mode, scope, doctor) + 13 extra commands installable with
  `setup/install-plugin.sh --with-extras`. Commands resolve the clone
  location at runtime — no hardcoded paths.
- `setup/` scripts: locked-dependency install (uv), audible verify,
  plugin/hook installers with uninstall counterparts, optional
  SessionStart self-update bootstrap (lockfile-driven `uv sync`, never a
  network pull).
- `/auto-speech-doctor` health check (binaries, disk, logs, daemon,
  queue, scope; `--json` mode).
- CI on macOS: ruff, shellcheck, lockfile drift, hermetic test subset on
  a bare Python 3.12, and a Flask+numpy web-test lane.

### Security

- Web server binds loopback only and reflects CORS solely for
  Chrome-extension origins — arbitrary web pages cannot drive the API.
- No telemetry; all synthesis and narration run locally.

[Unreleased]: https://github.com/JoshuaRamirez/auto-speech/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/JoshuaRamirez/auto-speech/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/JoshuaRamirez/auto-speech/releases/tag/v0.1.0
