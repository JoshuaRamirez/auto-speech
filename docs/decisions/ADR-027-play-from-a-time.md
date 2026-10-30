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
