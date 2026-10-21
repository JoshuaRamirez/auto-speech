# Research: Player launch

No `NEEDS CLARIFICATION` was left in the Technical Context; these are the design choices and
the facts they rest on.

## R1. How the utility is reached
- **Decision**: Unix stream socket at `$UI_UTILITY_SOCK`, else `~/.local/run/ui-utility.sock`; one
  JSON object per line each way; every request carries `"v":1`, `"cmd"` and a `"req"` id.
- **Rationale**: the contract's only transport; `req` lets the reader match replies even with
  events interleaved.
- **Alternatives**: the `ui-utility` command per request: not on PATH here (only the app is
  installed), and a process per line is slower.

## R2. How it is launched
- **Decision**: `ui-utility launch [--silent]` when the command is on PATH; otherwise
  `open -g -a "UI Utility"`, with `--env UI_UTILITY_SILENT=1` when silent and
  `--env UI_UTILITY_SOCK=…` when a private socket is set. Silent when our own environment has
  `UI_UTILITY_SILENT=1`. Then poll the socket for up to 10 s.
- **Rationale**: the contract's two launch forms, both background (`-g`). Passing the socket and
  silent switch through means a launch from an isolated environment cannot attach to his copy.
- **Alternatives**: `open -a` without `-g` takes focus (constitution III).

## R3. Off the speech path
- **Decision**: `after_line` runs the cheap gates, then puts the line in a one-slot mailbox
  (replacing any older one) and returns `"queued"`. A daemon worker thread does the I/O.
- **Rationale**: `_heard` runs on the narrator's playback thread right after a line; a hung
  utility must cost nothing (SC-003). Newest-wins matches "the line just spoken" (spec US3).
- **Alternatives**: a thread per line (unbounded under a hang); synchronous with a short
  timeout (still delays speech by the timeout).

## R4. Connection life and events
- **Decision**: one connection held by the worker; a reader thread reads every line, routes
  replies (by `req`) to a queue, and keeps the last `closed`/`error` event for the log. On EOF or
  error the connection is dropped; the next line reconnects, says `hello` and opens again.
- **Rationale**: clarify Q3; the player sends `position` events every 250 ms while he plays it,
  which must be read or the utility's writes back up.
- **Alternatives**: reconnect per line: the contract does not say a panel survives its
  connection.

## R5. What `open` carries
- **Decision**: `{"v":1,"cmd":"open","panel":"player","id":"auto-speech","clip":{"path":<history
  WAV>},"text":{"words":<sidecar word_timings.words>},"title":"<sender> · HH:MM:SS","play":false}`.
- **Rationale**: one panel reused (contract: opening an open id replaces its clip and text);
  `play:false` (clarify Q1); words already in the contract's `{w,start,end}` shape (ADR-020).
- **Alternatives**: `play:true` doubles every line aloud.

## R6. The hold, twice
- **Decision**: check `holding()` in `after_line` and again in the worker just before `open`; a
  raising gate counts as holding.
- **Rationale**: a hold can start between the line ending and the hand-over (the launch can take
  seconds). Constitution II.

## R7. Status across processes
- **Decision**: the worker writes `{"outcome","detail","at","line_id"}` to
  `auto-speech-player.json` in the state dir (tmp + rename); `note()` reads it.
- **Rationale**: `history status` runs in the CLI or MCP process, not the daemon.

## R8. Tests
- **Decision**: a stand-in contract server (thread, Unix socket in the suite's dir) that records
  requests and answers from a script (ok, an error code, never, wrong contract); the launcher gets
  an injected `launch` function. `run_all.sh` sets `UI_UTILITY_SOCK` to its dir,
  `UI_UTILITY_SILENT=1`, and `AUTO_SPEECH_PLAYER_LAUNCH=never` (the launcher then never runs a
  launch command).
- **Rationale**: clarify Q4: a silent copy started by a test would stay as his copy.
