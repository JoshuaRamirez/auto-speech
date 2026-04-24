# Phase 12 Micro-Design — mpv-based Seekable Playback

Implements [ADR-009](../decisions/ADR-009-mpv-controller.md).

## Scope
Replace `afplay` with `mpv` for all playback. One long-lived mpv instance at
a time, driven by its JSON IPC socket. New slash commands: `/pause`,
`/resume`, `/seek`, `/restart`, `/end`. `/speak` and `/replay` route through
the same controller.

## M1 — Classes

- **`MpvController`** (L3 service) — owns the session lifecycle: kill any
  prior session, spawn mpv detached, write session metadata.
- **`MpvIpc`** (L1 adapter) — one-shot "connect to socket, send JSON line,
  read line reply, close".
- **`SessionDir`** (tiny value-object + helpers) — paths and parsing for
  `/tmp/auto-speech/`.
- **`control.py`** (L5 entry) — CLI for the control slash commands:
  `pause`, `resume`, `seek`, `restart`, `end`.

## M2 — Semantics

### `SessionDir`
Fixed location `/tmp/auto-speech/`. Holds:
- `control.sock` — mpv IPC socket.
- `mpv.pid` — decimal PID.
- `wav.path` — absolute path to current WAV.
- `started_at` — ISO-8601 UTC.

Operations (all `@staticmethod`, pure I/O):
- `root() -> Path`
- `clear() -> None` — remove dir tree ignore-errors.
- `write(pid, wav_path) -> None` — create fresh dir, write files.
- `read_pid() -> int | None` — parse `mpv.pid`, None on any failure.
- `socket_path() -> Path`
- `is_mpv_running() -> bool` — PID exists AND is an mpv process (best effort: `ps -p PID -o comm=` check).

### `MpvController`
- `start(wav_path: Path) -> None`: kill any prior session; spawn mpv
  detached on a fresh session dir. Returns once mpv has started **and**
  the socket is connectable (with a short timeout — typically ~300 ms).
- `stop() -> bool`: send `{"command":["quit"]}` to the socket; clear
  session dir. Returns True if the command was delivered, False if no
  session existed.

The "wait for socket" step is important: if the control command fires
before mpv has created the socket, the command fails and the user sees
a confusing error.

### `MpvIpc`
- `send(cmd: list | dict) -> dict`: connect to the session socket,
  write one newline-terminated JSON line, read one line, return the
  parsed JSON reply. Raises `MpvIpcError` on connect/I/O failure.

mpv's IPC accepts an envelope of the form `{"command": [...]}`; for
convenience `MpvIpc.send` accepts either a `list` (wrapped internally)
or a full `dict` (passed through).

### `control.py`
Subcommand-style CLI:
```
control.py pause
control.py resume
control.py seek +15
control.py seek -30
control.py seek 0
control.py seek end
control.py restart
control.py end
```
Each translates to one `MpvIpc.send` call with the mpv command
specified by ADR-009. Exit 0 on success; exit 2 on "no active session"
(socket missing); exit 3 on IPC failure.

## M3 — Relationships

```
/speak end-of-pipeline   ┐
/replay end-of-lookup    ├──► MpvController.start(full.wav)
ShortPathStrategy.execute┘

/pause /resume /seek /restart /end ──► control.py ──► MpvIpc.send ──► mpv socket
```

`MpvController` uses `MpvIpc` for the "wait-until-socket-responsive"
ready-check and also for the stop() call. `MpvIpc` talks only to the
socket; it does not know about sessions, PIDs, or the filesystem.

## M4 — Implementation details

### Starting mpv detached
```python
subprocess.Popen(
    [
        "mpv",
        "--no-video",
        "--really-quiet",
        f"--input-ipc-server={socket}",
        str(wav_path),
    ],
    start_new_session=True,     # detach from our process group
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    stdin=subprocess.DEVNULL,
    close_fds=True,
)
```

### Waiting for socket readiness
Poll-and-retry:
```python
deadline = time.monotonic() + 2.0
while time.monotonic() < deadline:
    if socket.exists():
        try:
            MpvIpc.send({"command": ["get_property", "pid"]}, socket)
            return
        except MpvIpcError:
            pass
    time.sleep(0.05)
raise MpvStartupError("mpv socket did not become ready within 2.0s")
```

### Killing prior session
```python
pid = SessionDir.read_pid()
if pid and SessionDir.is_mpv_running():
    os.kill(pid, signal.SIGTERM)
    # wait up to 1 s then SIGKILL
    ...
SessionDir.clear()
```

### Routing existing playback paths
- `PlaybackConsumer` (long path) currently calls
  `AfplayLauncher.play(segment.wav_path, stop_event)` per segment. With
  Phase 12, playback happens on the *concatenated* `full.wav`, not
  per-chunk. The long path changes shape:
  1. Producer produces all chunks (unchanged).
  2. **Wait for all chunks.** Consumer no longer plays per-chunk.
  3. Concat runs (Phase 10).
  4. `MpvController.start(full.wav)` hands off and returns.
- `ShortPathStrategy.execute` similarly replaces its `AfplayLauncher`
  call with `MpvController.start(...)`.
- Replay (`replay.py`) same substitution.

**Trade-off flagged by this design change:** the long path previously
started audio as soon as chunk 1 was ready (~3 s). With mpv-on-concat,
audio starts after concat which is after all chunks are generated.

Measured upper bound on this regression: generator time for the longest
chunk. In the April 23 run, 5-chunk plan had chunk #5 gen=0.49 s (warm).
Total generation finished in ~3.4 s. Concat is ~50 ms. So on long path
the new first-audio latency ≈ total generation time + concat + mpv
startup ≈ 3.5 s + 0.05 + 0.3 ≈ 3.9 s. Within the 6 s NFR, but a
regression from the prior 2.4 s first-audio on warm runs.

**We accept the regression**, because the alternative — piping chunks
to mpv as a playlist and seeking across playlist boundaries — is
fragile. Seeking with mpv on a single concatenated file is trivial;
seeking across a playlist requires per-entry offset math that's not
worth the complexity.

### Slash-command design
Each control is a separate tiny slash command (`pause.md`,
`resume.md`, `seek.md`, `restart.md`, `end.md`). They are so trivial
they do not need a rewrite step — they just invoke `control.py` via
`run_control.sh`.

`seek.md` has an argument for the target (relative or absolute or
"end"):

```
/seek +15     → control.py seek +15
/seek -30     → control.py seek -30
/seek 0       → control.py seek 0      (absolute, start)
/seek 120     → control.py seek 120    (absolute, 120 s from start)
/seek end     → control.py seek end
```

`control.py seek` parses its argument:
- `+N` / `-N` → relative seek of N seconds.
- integer or float with no sign → absolute seek in seconds.
- literal `end` → seek to near-end (we pick end-of-file minus 0.5 s,
  implemented via `get_property duration` then absolute seek).

## Failure modes

| Mode | Handling |
|---|---|
| mpv not installed | `setup/install.sh` installs via brew. `MpvController.start` raises `MpvNotInstalledError` with the install command if brew is somehow unavailable. |
| Socket missing when control command runs | Control `control.py` exits 2 with "no active session". |
| IPC reply reports error | Exit 3, print the error payload on stderr. |
| Prior mpv refuses to die on SIGTERM | Escalate to SIGKILL after 1 s; proceed with new session. |
| Session dir stale from a prior crash | `is_mpv_running()` returns False; cleared silently. |

## Invariants introduced (beyond ADR-009's)

- **I-12.5 Start-idempotent-by-replacement.** Calling
  `MpvController.start(...)` while a prior session is active is equivalent
  to killing the prior and starting fresh — never errors.
- **I-12.6 Socket-ready on return.** When `MpvController.start` returns
  successfully, a subsequent `MpvIpc.send` to the session socket is
  guaranteed to reach mpv.

## Check gate

1. `brew install mpv` completes on the test machine.
2. `/speak` on a long message: audio starts a few seconds after
   invocation; slash command returns immediately (not blocked on
   playback).
3. While audio plays, `/pause` stops audio; `/resume` resumes at the
   same spot (audibly continuous).
4. `/seek +15` fast-forwards by 15 seconds.
5. `/seek 0` jumps back to start.
6. `/seek end` jumps to the last half-second; audio ends shortly after.
7. `/end` terminates playback cleanly; `ps aux | grep mpv` shows no
   lingering process.
8. A second `/speak` while one is playing kills the first and starts
   the second.
9. `/replay` also routes through mpv and supports the same controls.

## Out of scope
- `/status` command.
- Playback speed control.
- Multi-session queues.
