# ADR-013 — `/auto-speech-app` slash command starts the localhost web server

**Status:** Accepted
**Date:** 2026-04-29
**Depends on:** ADR-010 (web server design), ADR-012 (detach pattern via
python double-fork).

## Context
The web app is the natural surface for the Claude Desktop app and any
non–Claude-Code workflow, but to use it the user must remember to run
`plugin/scripts/shell/run_server.sh` in a terminal tab. That friction
makes the web UI feel second-class to the slash commands. Worse, when
the server isn't already running, the user has to switch contexts to
launch it.

A slash command that starts the server is the cleanest fix: typing
`/auto-speech-app` from any Claude Code session brings the web app up
detached and reports the URL. Subsequent invocations are no-ops with
a "already running" status.

## Decision

**Add a slash command `/auto-speech-app` that runs a small shell
script (`plugin/scripts/shell/start_webapp.sh`) which:**

1. Looks up `/tmp/auto-speech-webapp.pid`. If the PID is alive *and*
   `http://127.0.0.1:7860/` responds within 2 s → print
   `already-running` and the URL, exit 0.
2. Otherwise, double-fork the server into a fully detached process
   (same `os.setsid` + double-fork pattern Phase 15 uses for the
   autoplay worker), write the post-execvp PID to the pidfile, and
   poll the URL for up to 10 s.
3. On responsive → print `started` and the URL, exit 0.
4. On timeout → print `started-but-not-responsive` and the URL,
   exit 1. The server is still running; it just hasn't bound the port
   yet, which usually means the venv is missing or the model load
   failed mid-startup. The user can inspect the log at
   `/tmp/auto-speech-webapp.log`.

The slash command body parses the script's two-line output and
responds with a single human-readable status line.

## Why a slash command and not a SessionStart hook (yet)
- **Explicitness.** The user says "load it up" exactly when they need
  it, not on every session start. Sessions where audio isn't desired
  don't pay the resident-memory cost.
- **Simpler to reason about.** A SessionStart hook would have to
  handle "is it already running from a prior session" gracefully,
  which we already do in the start script — but the slash command is
  the natural place for the *first* such call.
- **Future-compatible.** If the SessionStart hook becomes desirable
  later, it just runs the same `start_webapp.sh` script. No code
  duplication.

## Why not a stop command (yet)
The web server holds open files (model weights), a Flask listener,
and the TTS executor thread. Stopping it cleanly requires sending
SIGTERM to the pidfile's PID. That's `kill $(cat
/tmp/auto-speech-webapp.pid)` — three keystrokes from any shell. A
slash command for it would be ~10 lines of work and zero new design
challenges. We defer it to **Phase 16.5** if the user actually wants
it; for now, restart-on-demand and let-it-run-otherwise is the
simpler default.

## Process & port singletons
- **Pidfile** at `/tmp/auto-speech-webapp.pid` is the source of
  truth for "is our server up?"
- **Port** is hardcoded at 7860 (matches ADR-010). Two different
  servers fighting for one port is a non-issue with the pidfile
  alive-check.
- **Stale pidfile** (process gone but file remains) is detected by
  `kill -0 <pid>`. The script removes the stale file before
  spawning a new server.

## Server runtime cost when idle
- ~50 MB resident for the Python interpreter + Flask + imported
  modules (no model loaded yet).
- The Kokoro model loads on the first `/api/speak` request (lazy),
  so an idle server doesn't pay the ~10 GB resident cost.
- Acceptable.

## Alternatives considered
- **Always-on launchd LaunchAgent.** Out of scope until the user
  validates daily web-app usage. Adds plist file + system-level
  install steps.
- **Start synchronously and wait for the model load.** Would push
  the slash command's wall time to ~3–5 s. Detached + lazy is
  cleaner; the URL is reachable immediately, and the first
  `/api/speak` pays the model load (already happens today).
- **Use the same `MpvController`-style detached pattern in pure
  bash (no python).** Considered, but `nohup` alone leaves the
  process in the same process group; on macOS some shells will
  reap on parent exit. Python's double-fork + setsid is the
  reliable answer (Phase 15 established this).

## Consequences
- New script `plugin/scripts/shell/start_webapp.sh`.
- New slash command `plugin/commands/auto-speech-app.md`.
- `setup/install-plugin.sh` symlinks the new command.
- A pidfile at `/tmp/auto-speech-webapp.pid` exists while the server
  runs. The server-side process itself does not write or read this
  file; the start script owns its lifecycle. (The web_server.py
  module is unchanged — fewer assumptions about its caller.)

## Invariants
- **I-16.1 At-most-one healthy server per machine.** Running
  `/auto-speech-app` twice yields exactly one running server.
- **I-16.2 Pidfile reflects truth or absence.** Either the pidfile
  doesn't exist OR its PID is alive OR the start script removes it
  on the next call before spawning fresh.
- **I-16.3 Stale-tolerant.** A pidfile left over from a crashed
  prior server (kernel killed it, oom-killer, etc.) does not block
  a fresh start.
- **I-16.4 No state mutation in the web_server module.** All
  pidfile work lives in `start_webapp.sh`. The Python remains
  unchanged so `run_server.sh` is still a viable manual-launch path.
