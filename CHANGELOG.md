# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The Chrome extension under `chrome-extension/` is versioned independently
(its own changelog lives in `chrome-extension/README.md`).

## [Unreleased]

## [0.4.0] - 2026-10-18

Nothing held is ever dropped: a missed list you can read, replay and clear
from the CLI and the MCP. Baseline tag `v0.3.0`; commits `2617431` (feature) plus this release-notes commit
landed on `main` by a `--no-ff` merge of `worktree/missed-list`;
`tests/run_all.sh`: 53 test files ran, 0 failed, under the isolated
runner with the caller's Claude Code session cleared. Release notes:
[docs/releases/v0.4.0.md](docs/releases/v0.4.0.md).

### Added
- **The missed list** (ADR-015, amended). A line held for another app's
  sound goes on the list as *held* the moment the hold starts and leaves
  it only once it has played. It stays as *missed* when the longest wait
  passes, when it turns out to be stale backlog once the sound stops, when
  a user interrupt releases its hold, or when the daemon restarted while
  it was held. `auto-speech missed` lists held and missed lines oldest
  first with when, why and the text; `missed --play` replays the missed
  ones in that order over any other sound, through the daemon, and each
  leaves the list once it has played; `missed --clear` forgets them. MCP
  tool `missed(action = list | play | clear)` prints the same text; slash
  command `/auto-speech-missed` (extras). One file:
  `~/.local/state/auto-speech/missed.json` (`AUTO_SPEECH_MISSED_FILE`
  overrides it; the test runner isolates it). The daemon `status` reply
  carries `missed` counts.
- `NativeAudioSink.play(hold=False)` plays one file over other sound (a
  replay); `already_waited` and `on_hold` carry the queue age and the hold
  callback to the gate; `last_hold` exposes the outcome.
  `DaemonClient.speak`/`play_cache` take `missed_id` and `hold`.

### Changed
- **Past the longest wait a held line is set aside, never dropped**
  (`EXPIRED` replaces `expired_drop`/`expired_play`). The longest wait
  (`max_wait_seconds`, 180 s) is now counted from when the line was asked
  for, so a line that waited behind an earlier hold is held only for the
  remainder; and once the sound stops, lines that sat behind a hold and
  are older than it go to the missed list instead of playing late, line
  by line. A line that is merely old because the queue was busy still
  plays.
- `yield status` and `yield check` say "then set aside as missed" where
  they said "then drop it".

### Removed
- `after_max_wait` (`drop` | `play`) from `[sound_yield]`: the loader
  names it in a warning and ignores it; `yield on/off/add` rewrite the
  file without it.

## [0.3.0] - 2026-10-18

Speech yields to other apps' sound, and the CLI and MCP surfaces reach
parity. Baseline tag `v0.2.0`; commits `e91a3c4..e91ea72 (plus this release-notes commit)` landed on `main`
by a `--no-ff` merge of `worktree/sound-yield`; `tests/run_all.sh`: 52 test files ran, 0 failed (630 counted unit tests plus the shell suites), under the isolated runner; live check 2026-10-18 16:06 (held 7.1 s behind an afplay tone, then spoke). Release notes:
[docs/releases/v0.3.0.md](docs/releases/v0.3.0.md).

### Added
- **Speech holds while another app plays sound** (ADR-015). By default a
  playback does not start while music, a video or a call is coming out of
  the speakers; it keeps its turn in the queue and plays when the sound
  stops. Past the longest hold (180 s) it is dropped and logged
  (`after_max_wait = "play"` plays it anyway). Detection reads Core
  Audio's process list and each process's running-output flag through
  ctypes, with the default output device's running-somewhere as fallback:
  no permission, no audio captured. auto-speech's own playback (its pid,
  its mpv, anything with `auto-speech` on its command line) never counts.
  `interrupt()` releases a hold like it stops a playback.
- **Exception list and off switch, the same in three places.**
  `auto-speech yield status | on | off | add <apps…> | remove <apps…> |
  clear | check` (alias `sound-yield`), the MCP tool `sound_yield(action,
  apps)` and `~/.config/auto-speech/sound-yield.toml` (`[sound_yield]`:
  `enabled`, `exceptions`, `max_wait_seconds`, `after_max_wait`,
  `poll_seconds`) share one action runner and one file. The daemon
  re-reads the file on every hold poll, so `yield off` or `yield add`
  releases a hold already in progress. `/auto-speech-yield` slash command
  (extras). `/auto-speech-doctor` validates the file. The daemon `status`
  reply reports `sound_yield.holding` and the last hold.
- `FEATURES.md` (feature catalog) and `docs/releases/` (per-version
  release notes with an index).
- **Unified `auto-speech` CLI dispatcher** (`bin/auto-speech`): speak,
  replay, pause, resume, restart, stop, seek, autoplay, narrate, yield,
  hook, doctor, update.
- **Expanded MCP server**: `speak`, `autoplay_turn`, `narrate_event`,
  `playback_control`, `session_configure`, `sound_yield`, `doctor`.
- **`speak` MCP server.** A stdio MCP server
  (`plugin/scripts/shell/run_mcp.sh`) with one tool, `speak(text)`, that
  says the words it gets aloud, verbatim. It returns as soon as the words
  are queued. A detached worker waits in the cross-session playback FIFO,
  honors the global mute, and never cuts off another playback. Register
  it with `bash setup/install-mcp.sh`.

### Fixed

- Leftover 0.1.0 opt-OUT copy on current surfaces now matches 0.2.0
  opt-IN: `/auto-speech-autoplay-status`, `/auto-speech-doctor`,
  `/auto-speech-scope`, and `docs/OPERATIONS.md`. Doctor reports `jq`.

### Fixed
- **Tests and development daemons can no longer reach the live speech
  service.** Every runtime path (`/tmp/auto-speech-daemon.sock`, the pid
  files, logs, markers, the mpv pid) now resolves through
  `AUTO_SPEECH_TMP_ROOT` (`state_paths.py` in Python, the same variable in
  the shell scripts; default `/tmp`, so the live daemon and hooks are
  unchanged). `tests/run_all.sh` exports a private per-run directory and
  socket, prints them, and stops any daemon a test started there.
  `tests/test_state_paths.py` proves it. Before this, a suite run could
  auto-start a worktree daemon on the live socket and send it an
  interrupt, cutting live speech.

### Changed
- `NativeAudioSink` takes a `sound_yield` gate (default on) and raises
  `PlaybackYielded` when a held playback is dropped; tests that drive a
  real sink against a mocked mpv rebind `DEFAULT_SOUND_YIELD_FACTORY`.
- Version strings: `auto-speech --version` and the MCP `serverInfo` report
  0.3.0.

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
