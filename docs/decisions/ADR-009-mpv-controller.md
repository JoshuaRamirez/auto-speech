# ADR-009 — mpv-based seekable playback controller

**Status:** Accepted
**Date:** 2026-04-23
**Depends on:** [ADR-007](ADR-007-mandatory-concat-and-cache-centric-artifact.md) (a single `full.wav` is the playback input).

## Context
`afplay` is shipped with macOS and trivially reliable, but it is a blocking
command with **no IPC** and **no seek**. `/speak` starts it, `afplay` plays
to the end, there is no way to ask it to pause, resume, fast-forward, or
jump to a specific time.

The user wants media-controller semantics: `/pause`, `/resume`, `/seek +15`,
`/seek -30`, `/seek 0` (start), `/seek end`. These cannot be layered on
top of `afplay` without unpleasant hacks (SIGSTOP/SIGCONT is risky around
CoreAudio; killing and restarting afplay at an offset loses time fidelity).

## Decision

**Swap the playback engine from `afplay` to `mpv`, and drive it through
mpv's built-in JSON-IPC over a Unix domain socket.**

- A single long-lived "playback session" represents one audio file being
  played. The session is started by `/speak` (or `/replay`) and owns an
  mpv subprocess plus a socket at `/tmp/auto-speech/control.sock`.
- The session is **singleton**: at most one playback is active at a time.
  A second `/speak` while one is playing kills the current mpv and starts
  a new session.
- Control slash commands (`/pause`, `/resume`, `/seek N`, `/restart`,
  `/end`) send one-line JSON commands to the socket and exit.

## Why mpv

- macOS-friendly: Homebrew `brew install mpv`, no build steps.
- Battle-tested media playback: handles WAV, precise seeking, pause/resume.
- First-class JSON-over-Unix-socket IPC via `--input-ipc-server=<path>`.
- Runs headless (`--no-video --really-quiet`), no GUI pop-up.
- Cooperates with macOS audio routing (respects default output device).

## Why a long-lived controller process

Slash commands are short-lived: each one starts a process, does its work,
exits. But `/pause` on a non-existent process is a no-op. So the **playback
session** (mpv + socket) must outlive the slash command that started it.
Two shapes:

- **Option A: mpv itself is the daemon.** `/speak` starts mpv detached
  (via `start_new_session`), returns immediately. `/pause` finds the
  socket and writes to it. mpv exits when playback finishes or when the
  socket receives `{"command":["quit"]}`.
- **Option B: A Python daemon wrapping mpv.** Adds a process + complexity
  for no added capability, since mpv's own IPC is already adequate.

**Chosen: Option A.** The long-lived process is just mpv itself. The
control commands are trivial — open socket, write one JSON line, read
one line of reply, close.

## Session model

Only one active session at a time. A session is identified by a
`/tmp/auto-speech/` directory containing:
- `control.sock` — the mpv IPC socket.
- `mpv.pid` — the mpv pid (as text).
- `wav.path` — absolute path to the WAV being played.
- `started_at` — ISO-8601 timestamp.

Starting a session:
1. If `/tmp/auto-speech/` exists and `mpv.pid` names a running mpv, kill
   it (SIGTERM; escalate to SIGKILL after 1 s). Remove the dir.
2. Create a fresh dir.
3. Launch mpv: `mpv --no-video --really-quiet
   --input-ipc-server=/tmp/auto-speech/control.sock <wav>` with
   `start_new_session=True`.
4. Write the PID and WAV path into the session dir.
5. Exit 0 immediately (do not wait for playback to finish).

Controls:
- `/pause` — `{"command":["set_property","pause",true]}`
- `/resume` — `{"command":["set_property","pause",false]}`
- `/seek +N` — `{"command":["seek",N,"relative"]}`
- `/seek -N` — `{"command":["seek",-N,"relative"]}`
- `/seek M` — `{"command":["seek",M,"absolute"]}` (M seconds from start)
- `/restart` — `{"command":["seek",0,"absolute"]}`
- `/end` — `{"command":["quit"]}`

The mpv process self-cleans: when it exits (natural end or `quit`), it
closes the socket. The session dir is cleaned up lazily — the next
`/speak` removes stale session dirs as part of its pre-flight (step 1
above).

## Rationale vs alternatives

- **sounddevice-based Python daemon.** More control but more moving parts
  (audio device lifecycle, sample-rate handling, seek buffering). No
  capability gain for this use case.
- **ffplay / mplayer.** mpv supersedes mplayer; ffplay has no IPC socket.
- **Keep afplay; simulate pause via SIGSTOP.** Fragile on macOS Core Audio.
  Seek cannot be implemented this way. Dead end.

## Consequences

- `brew install mpv` is a prerequisite. `setup/install.sh` gains an
  `mpv` check and installs via `brew` if missing.
- `PlaybackConsumer` (long path) and `ShortPathStrategy` both route
  through a new L3 service `MpvController` rather than calling
  `AfplayLauncher` directly.
- Replay (`replay.py`) also routes through `MpvController`, so all
  playback paths benefit from seek/pause uniformly.
- The blocking-wait model of v0.1 changes: `/speak` now returns as soon
  as playback **starts**, not when it finishes. This is actually better
  for the slash-command UX: the user regains control of Claude Code
  immediately and can issue `/pause` or `/seek`.
- A new invariant: at most one active mpv session globally.

## Invariants introduced

- **I-12.1 Singleton session.** At any moment there is zero or one
  active mpv instance managed by auto-speech. Starting a new session
  kills any prior.
- **I-12.2 Socket-is-ground-truth.** Control commands succeed iff the
  socket is connectable and mpv responds. No state is tracked outside
  the socket + session dir.
- **I-12.3 Detached lifecycle.** mpv runs in its own process group so
  that Ctrl-C on the spawning shell does not terminate it.
- **I-12.4 Graceful shutdown.** `/end` sends `{"command":["quit"]}`;
  mpv exits cleanly; no leftover audio process.

## Out of scope

- Multiple simultaneous playback sessions (e.g., queue a second clip
  after the first). Explicit non-goal for v0.1.
- Playback speed control (mpv supports it, but `/speed 0.8` isn't in
  the minimum user ask).
- A `/status` command showing current position, total duration, paused
  state. Trivial follow-on; defer.
