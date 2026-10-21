# ADR-021 — After each line, the UI Utility's player shows it, by the utility's contract

**Status:** Accepted
**Date:** 2026-10-21
**Depends on:** ADR-020 (the speech history and its player launcher), ADR-018 (the meeting hold).
**Spec:** `specs/001-player-launch/` (built through one Spec Kit trial; its TRIAL-LOG.md records how).

## Context
His 13:38, 2026-10-20 (the Master Desk #m1020-1339-68ee): "Each time the
Desk speaks, a small player pops up and plays the line's WAV … Below the
controls, the transcript, highlighting the current position." ADR-020
built the history and a launcher that stopped short of the UI Utility,
waiting for its published contract. The Development Desk handed the
contract over on 2026-10-21 (#m1021-0025-f8be):
`~/Developer/UI-Utility/docs/external/CONTRACT.md`, v1 rev 1.1. JSON lines
over a Unix socket (`~/.local/run/ui-utility.sock`, `UI_UTILITY_SOCK`
overrides it); `hello`, then `open` with one clip, its words and a title;
events interleaved with replies; `ui-utility launch [--silent]` or
`open -g -a "UI Utility"` to start it; "a copy already running keeps its
mode".

## Decision
- **The client** (`ui_utility_client.py`) speaks the contract: every
  request has `"v":1`, `"cmd"` and a `"req"` id `as-<n>`; a reader thread
  reads every line, hands a reply to the request with its `req` and drops
  events (an `error` event is logged once). Failures are a `ContractError`
  of one kind: `no socket`, `refused` (`<code>: <detail>`), `timeout` (5 s),
  `wrong contract` (not contract 1 with the `player` piece), `closed`.
- **Launch** in the background only: `ui-utility launch` when on PATH,
  else `open -g -a "UI Utility"`, passing `UI_UTILITY_SILENT=1` and
  `UI_UTILITY_SOCK` through when set; then connect, retrying every 0.25 s
  for 10 s. With `AUTO_SPEECH_PLAYER_LAUNCH=never` nothing is run.
- **Off the speech path.** `PlayerLauncher.after_line()` runs on the
  playback thread: the setting, the hold and the install check, then the
  line goes into a one-slot mailbox (an older line not yet shown is
  dropped: the player shows the line just heard) and it returns `queued`.
  A worker thread (`player-launcher`) does the rest.
- **The hand-over** on the worker: a WAV pruned meanwhile is `skipped`; the
  hold is checked again (one may have started while the line waited); a
  connection is made, the utility launched if its socket is missing, and
  `hello` said once per connection; then `open` on the one panel
  `auto-speech` with the history WAV, the sidecar's words, the title
  `<sender> · HH:MM:SS` and `play:false`. Opening the same id replaces it.
- **One held connection.** A refusal keeps it; a timeout, an EOF or a send
  error drops it, and the next line reconnects and says `hello` again. At
  daemon shutdown the connection is closed and the panel is left to him.
- **Status across processes.** The worker writes the last outcome to
  `auto-speech-player.json` in the state dir (tmp + rename):
  `{"outcome", "detail", "at", "line_id"}`. `history status` shows
  `opens after each line; last: <outcome> HH:MM:SS[: detail]`. The CLI and
  MCP build their launcher with `start_worker=False`.

Clarify answers (spec.md): the player opens ready to play and never plays
on open, since the line was just heard; a closed player is opened again by
the next line; one held connection; tests use a stand-in, never the real
app; no history list in the player in this step.

## Consequences
- With `player` on, a window appears (without focus) after each line he
  hears, outside a hold. Nothing changes while it is off, the default.
- A hung or absent utility costs the speech path nothing; the outcome file
  and the daemon log (each failure kind once) say what happened.
- `tests/run_all.sh` sets `UI_UTILITY_SOCK` to its private dir,
  `UI_UTILITY_SILENT=1` and `AUTO_SPEECH_PLAYER_LAUNCH=never`; the tests
  drive a stand-in server (`tests/ui_utility_standin.py`).

## Invariants
- I-21.1 Nothing is sent or launched while the setting is off, a hold
  stands, or the utility is not installed.
- I-21.2 `after_line` does no socket or process I/O.
- I-21.3 No test launches the real UI Utility.
- I-21.4 Every `open` carries `play:false`.
