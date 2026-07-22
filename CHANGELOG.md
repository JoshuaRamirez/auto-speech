# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The Chrome extension under `chrome-extension/` is versioned independently
(its own changelog lives in `chrome-extension/README.md`).

## [Unreleased]

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

[Unreleased]: https://github.com/JoshuaRamirez/auto-speech/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/JoshuaRamirez/auto-speech/releases/tag/v0.1.0
