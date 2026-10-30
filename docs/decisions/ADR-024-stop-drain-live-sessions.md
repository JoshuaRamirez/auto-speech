# ADR-024: a stop stops, a restart drains first, only live sessions are heard

Status: accepted, 2026-10-21 (#m1021-2343-74ba, card 82)

## Context
His 2026-10-21 23:38: "you just interrupted yourself during the 10 m thing.
bug. fix. … i would gladly have these bugs stop recurring." / "i think im
hearing things from other conversations" / 23:39 "im still hearing things".

The record (the narrator log 06:36-06:40Z, desk-speaker's said.jsonl): the
Master Desk queued a 20-part briefing through desk-say at 23:36:23. A
restart at 06:36:56Z (signal 15) cut part 2 while it played. desk-speaker
fed parts 3-11 after the restart. Five stops (06:38:13-06:39:26Z, "P1 User
Barge-in completed: purged 0 lower items") each ended the line playing, and
after each the next part started: desk-speaker only waits for the daemon to
go idle, and a stop left it idle.

## Decision

### 1. A stop stops
His stop (playback_control stop, `auto-speech stop`, the web app's end: the
daemon's `interrupt` action; and the speech control's stop glyph) puts the
daemon in a **stopped** state: the line playing ends, the queue is cleared,
and nothing plays, from any caller, until a play or resume. A line that
arrives while stopped goes on the missed list (`missed`, reason `stopped`),
in order, so the control lists it unplayed. It is checked before synthesis
(`_speak`), before a cached clip (`_play_cached`) and at the play itself
(`_play_item`).

- **What ends it**: `auto-speech resume` and playback_control resume (the
  daemon's new `resume` action), and the control's play glyph. Each queues
  the lines the stop set aside, in order. A play-item or play-all on the
  control ends it too, and plays only what he picked.
- **What is not a stop**: the UserPromptSubmit barge-in when he types. It
  still interrupts and purges as before, without stopping.
- **Seen by callers**: the status reply carries `stopped`, `stopped_since`
  and `queued`; `auto-speech narrate status` prints it; `doctor` warns.
- **Kept across a restart**: `auto-speech-stopped.json` in the state folder.

### 2. A restart drains first
`auto-speech narrate restart [--when-quiet] [--timeout S]` waits until nothing plays and nothing is queued. When `~/.local/bin/desk-speaker status` reports a pending count (JSON `pending`, or "pending N" in text), it waits for that too. A status that cannot be read is not waited on. Then the command runs `launchctl kickstart -k gui/<uid>/dev.auto-speech.narrator`. `--when-quiet` also waits until no other app's sound or meeting holds a line. On timeout (300 s by default) it refuses, exits 1, and says what is still busy.

On SIGTERM the daemon lets the line in flight finish (up to 15 s, under
launchd's 20 s exit timeout for a submitted job) and sets everything still
queued aside as `daemon restarted`; a line cut past the budget, or by a
second signal, is set aside too, not counted as heard.

### 3. Only sessions meant to speak are heard
An enrolment marker (`~/.claude/auto-speech-autoplay-enabled/<id>`,
`~/.claude/auto-speech-narrate-sessions/<id>`) counts only while its session is alive.

**Alive** means either of these, as the daemon can tell:
- the session's transcript (`~/.claude/projects/<project>/<id>.jsonl`) was written in the last 24 hours;
- the marker itself is younger than 24 hours. A session that has just enrolled may not have a transcript yet.

The daemon and `SessionEnrollment` ignore a marker that is not alive. The daemon logs it once per session, and `auto-speech doctor` lists the stale ones. Nothing is deleted: the 30-day sweep stays. A hook that fires is itself a live session, so the shell hooks keep their plain marker check.

### 4. `missed add` (for T05)
`auto-speech missed add --text "…" --source NAME [--at HH:MM] [--id ID]`
sends `missed_add` to the daemon, the one writer. The line goes on the
missed list as `added`, with its source, which the control shows. `--at` is
today, or yesterday when that time is still ahead.

## Consequences
- After a stop, a desk that waits for idle sees `stopped: true` and can hold
  its own queue rather than feed the daemon (the lines would go on the
  missed list in order, not be lost).
- The cut briefing's parts can be put back with `missed add` and played
  from the control.

## Amendment, 2026-10-30: the control's stop is a playback act (#m1030-0948)
His 2026-10-30 09:48: "the stop button in the speech app kills the server.
its just supposed to be a playback controller, not the server controller".
At 09:47 a play-from run had about 130 replays waiting. The narrator's log
has neither "stopped by the speech control's stop" nor "P1 User Barge-in"
between 09:41:48 and the restart at 09:51:19: his press never reached the
stop. And a stop that did reach it cleared only the narration bands (P3/P4),
not the explicit band the replays wait in.

- **Transport presses are handled on the event thread, at once.** play,
  hold, stop and skip from the control (`ControlDriver._on_event`) no longer
  wait in line behind a play-from being queued or a sync to the UI Utility;
  none sends a request, so none can wait on its own reply. Settings, rate
  and play events still go to the control's worker.
- **A stop cancels plays.** Play events still waiting to be handled are
  dropped, and a play-from or play-all being queued queues none of the rest.
- **A stop clears every waiting replay.** `_stop_speech` takes every queued
  replay of a heard line out of the queue at once
  (`PriorityArbiter.remove_where`), logging `stopped: cleared <n> queued
  replays`. They are in the history already.
- **Live lines already waiting** are not dropped: the worker sets each aside
  on the missed list as `stopped` straight away (it does no synthesis while
  stopped), and they play on his play, in order, as above.
- **The process stays.** A stop ends the line playing (the player, mpv) and
  never signals, stops or restarts the narrator.
- Tests: `tests/test_stop_is_playback.py`.
