# Phase 16 Micro-Design — `/auto-speech-app` slash command

Implements [ADR-013](../decisions/ADR-013-slash-command-webapp-launcher.md).

## Scope
A single slash command `/auto-speech-app` that brings the localhost
web server up if it isn't already, reports the URL, and is idempotent
on repeat invocation. Detached process, pidfile-managed.

## M1 — Artifacts at this level

This phase is shell + a slash command file. **No new Python.** The
existing `web_server.py` runs unchanged.

| Artifact | Role |
|---|---|
| `plugin/scripts/shell/start_webapp.sh` | Idempotent start script: alive-check, stale-pidfile cleanup, double-fork detach, post-start health poll. |
| `plugin/commands/auto-speech-app.md` | Slash command body that runs the script and renders its two-line output as a single status line. |

Edit:
- `setup/install-plugin.sh` — symlink the new command.

## M2 — Semantics

### `start_webapp.sh`
Owns the entire lifecycle:
- **Alive check.** `kill -0 <pid>` against the pidfile.
- **Health check.** `curl -fs --max-time 2 http://127.0.0.1:7860/`.
- **Stale-pidfile cleanup.** If pidfile exists but the PID isn't
  alive, remove it.
- **Detached spawn.** Python double-fork + `os.setsid`; in the final
  child write the pidfile and `execvp` into `bash run_server.sh`.
- **Post-start poll.** Up to 20 × 0.5 s for the URL to respond.

Exit codes / output:

```
already-running\nhttp://127.0.0.1:7860/\n   exit 0
started\nhttp://127.0.0.1:7860/\n           exit 0
started-but-not-responsive\nhttp://127.0.0.1:7860/\n   exit 1
```

### `auto-speech-app.md`
Slash command body. Reads the two-line status from the script and
maps to one human line. Examples:
- "auto-speech web app started at http://127.0.0.1:7860/"
- "auto-speech web app already running at http://127.0.0.1:7860/"
- "auto-speech web app started but not yet responding (see /tmp/auto-speech-webapp.log)"

## M3 — Relationships

```
user types /auto-speech-app
    │
    ▼
slash command Bash:  start_webapp.sh
    │
    ├── pidfile alive + URL responsive? → already-running
    │
    └── otherwise:
          ├── remove stale pidfile if any
          ├── python3 double-fork + setsid
          │       └── execvp bash → run_server.sh → web_server.py
          ├── poll /api/status until responsive (≤10s)
          └── started | started-but-not-responsive
```

The detached web server outlives the slash command and the Claude
Code session that started it. Subsequent slash-command calls find it
via the pidfile.

## M4 — Implementation details

### Path constants
```
PROJECT_ROOT="<absolute>"
PIDFILE="/tmp/auto-speech-webapp.pid"
URL="http://127.0.0.1:7860/"
LOG="/tmp/auto-speech-webapp.log"
RUNSERVER="$PROJECT_ROOT/plugin/scripts/shell/run_server.sh"
```

### Why pidfile-then-execvp ordering
Python's double-fork ends in `execvp("bash", ["bash", run_server.sh])`.
The PID survives `execvp`. So writing the pidfile *before* `execvp`
is correct: the PID we write is the same PID that becomes the
running Flask process. No race window where the pidfile points at a
dead-after-fork process.

### Why we don't change `web_server.py`
Keeping pidfile lifecycle in the start script preserves
`run_server.sh` as a clean manual-launch path. Anyone running the
server in a terminal tab won't have a stale pidfile to clean up,
and nothing in the Python code path depends on a pidfile existing.

### Verbose logging
The detached process inherits `stdout`/`stderr` redirected to
`/tmp/auto-speech-webapp.log` (append). `tail -f` on the log shows
exactly what the server is doing if the post-start health poll
times out.

## Failure modes

| Mode | Handling |
|---|---|
| `python3` not on PATH | Script aborts with a clear error before spawning. |
| Port 7860 in use by something else | Server starts, but Flask fails to bind; URL never responds → `started-but-not-responsive` exit 1. Log explains. |
| Venv missing | `run_server.sh` aborts with its existing "venv missing" error. The pidfile is cleaned by the next start attempt (PID dead). |
| User Ctrl-Cs the slash command before health poll completes | The detached server keeps running. Next invocation finds it healthy. |

## Invariants (beyond ADR-013's)

- **I-16.5 Detached lifecycle.** The web server's process group is
  not the slash command's. Killing the parent shell does not stop
  the server.
- **I-16.6 Two-line stdout contract.** The script's stdout is
  always exactly two lines: a status word and the URL. The slash
  command's parser depends on this shape.

## Check gate

1. With the server already running, `/auto-speech-app` reports
   "already running" and exit 0; pidfile unchanged.
2. With the server NOT running and no pidfile, `/auto-speech-app`
   spawns a detached server; pidfile created; URL responsive;
   subsequent `curl /api/status` returns `{"active": false}`.
3. Stale pidfile (PID 99999): `/auto-speech-app` cleans it,
   spawns fresh, succeeds.
4. After Claude Code session exits, `pgrep -f web_server.py`
   still finds the process (detachment proven).
5. Running `/auto-speech-app` from a session whose project differs
   from the auto-speech checkout still works (the script knows its
   own location via `BASH_SOURCE[0]`).

## Out of scope
- A `/auto-speech-app-stop` command. (Future Phase 16.5.)
- SessionStart hook. (Future Phase 17 candidate.)
- LaunchAgent plist. (Future, only if the user requests
  "always running.")
