# ADR-027: play every line from a chosen time, in order

Status: accepted, 2026-10-30 (#m1030-0850-a448)

## Context
His 2026-10-30 08:47: "my master desk speech app is some kind of modal, i
want it to be a proper window. and I want to play from a particular past
time all the messages up to the latest. so i want a filter bar. i want a
date col and a time col. i want to keep the individual play button. i want
to be able to select and copy the text of the spoken text"

The window, the filter bar, the columns and the copyable text are the UI
Utility's (its contract 1.5, built beside this). This side owes two things:
each listed line's date and time, and the plays a "play from" asks for.

The Desk's words: every spoken line still plays exactly once in normal use
(ADR-026 must not regress), while a replay from a chosen time is an explicit
act that may replay lines; voice and rate unchanged.

## Decision
- **Each item carries `time`.** `items()` adds `"time"`, RFC 3339 with the
  local offset to the second (`2026-10-30T08:47:12-07:00`): `spoken_at` for a
  heard line, `queued_at` for a missed one, the start of play for the line
  playing now. `at` (HH:MM) stays, the fallback for an older utility. The
  UI Utility never reads a field its contract does not name (`Wire.swift`), so
  `time` is sent to every copy, with no version check.
- **`play-from` plays the ids it names, in the order given.** The control
  sends `{"event":"play-from","id":…,"from":RFC3339,"items":[…]}`, every
  listed line at or after `from`, oldest first. Each id goes the way play-item
  goes: a heard line replayed from its WAV (`history_id`, not exported again),
  a missed line through `missed_speech.play_ids` (off the list once heard),
  unknown ids skipped. The line playing now (`now-…`) is not restarted: it
  plays on to its end and the rest follow, so no line cuts another off.
- **No new queue.** Every control play is a `Priority.EXPLICIT_MCP` item.
  That band is first in, first out, and an explicit item never preempts
  another (`_handle_p2_preemption` only appends; nothing sets `_preempted`),
  so a run of plays is already one ordered sequence, each to its end.
  play-all had no preemption problem; a live test now holds both to that.
- **One log line per play-from**: `control: play-from <from> (<n> lines)` in
  the narrator's log, `n` the lines queued.
- **A replay never lands on the missed list.** His stop in the middle of a
  play-from (or a play-all) left the queued replays to be set aside, and each
  went on the missed list as a new unplayed line although it was heard and in
  the history. `_set_aside` now drops a replay (`history_id`) with a log line
  instead, as `_set_aside_item` already did at a restart.

## Consequences
- A play-from may replay lines: that is his explicit ask. Normal narration,
  the missed list and the line ledger are untouched: a line spoken once is
  heard once, and a replay adds neither a history line nor a missed entry.
- A line arriving during a long play-from waits behind it if it is an
  explicit line (desk-say), or until the run ends if it is narration.
- Tests: `tests/test_play_from.py` (a live worker into a recording sink,
  silent) and `tests/test_speech_control.py` (the event and `time`).

## Amendment, 2026-10-30: a new run replaces the old (#m1030-0939-c4f9)
Seen live: he pressed Play from at 09:37:40 (82 lines) and again at 09:41:48
while the first run played; the second appended 82 replays behind the 57
still waiting (`queued: 139`).

- **A play-from or a play-all is a run.** `ControlDriver._as_run` calls
  `ControlActions.begin_run` before queueing and `end_run` after. The
  narrator numbers the run, tags each history replay queued in it
  (`QueueItem.control_run`), and takes every queued replay of an earlier run
  out of the queue (`PriorityArbiter.remove_where`, the item in hand
  untouched). One log line when any were taken: `control: replaced <n>
  queued replays`.
- **The line playing now plays to its end.** Explicit plays never cut one
  another off (above); a new run does not either.
- **Only a run's own replays go.** A fresh line from a desk, a single
  play-item, a skip-back replay and a missed line (it goes through the
  speak path, carries its line id and leaves the missed list only once
  heard) are never taken out, so ADR-025/026's one play per line holds.
- **play-item is not a run.** It is a click on one line: it queues behind
  whatever is waiting, as before, and replaces nothing. Replacing a run with
  a single click would drop the day he asked for; queueing it keeps both.
- Tests: `tests/test_play_from.py`, `TestANewRunReplacesTheOld`.
