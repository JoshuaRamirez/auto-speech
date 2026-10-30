# ADR-028: the speech control's 10-30 round (UI Utility contract 1.6)

Status: accepted, 2026-10-30

## Context
His asks between 09:39 and 09:55 on 2026-10-30, after Play from (ADR-027)
worked: a replayed line should select and scroll to its own row, not add a
new one; the empty From cell (a replay's now-row, the 01:22:47 line,
record `20261030-082247-114-b582d648`, sender "Master Desk") should name the
sender; the row playing should light its words as they are spoken; the
transport should go to the line's start, its end, and 1, 5 or 10 seconds
back or forward. And his 09:55, verbatim: "I also want to see somehow what
is queued to be played, and I want to be able to manipulate the queue,
clear it, modify it."

The UI Utility builds contract 1.6 beside this. This side owes what it
sends and what it does with the events.

## Decision
- **A replay is its row.** `ControlDriver.playing(text, source, item_id,
  words)`: a heard line played again (`history_id`) makes its own history
  row current, with no now-row; a missed line playing makes `missed:<id>`
  current. Only a fresh line gets a now-row, with its sender
  (`_sender_name`, the name the history records) and its estimated words.
- **One row per line.** `items()` keeps one row per id and one history row
  per spoken line (sidecar `line_id`, else `origin_file`, else its id),
  the newest. Amended 2026-10-30 (#m1030-1241-6e0a): until then the history
  never wrote `line_id`, so only `origin_file` held; now it does. Two records
  sharing either key are one line. A missed entry whose `line_id` or
  `origin_file` matches a heard row is not a second row. A line heard later
  than it came (set aside by his stop, then played from the missed list)
  keeps its row at the time it came (sidecar `said_at_epoch`, the missed
  entry's `queued_at`), so its missed row becomes its heard row in place
  instead of a new row on top at the time of the play.
- **Words and position.** The row playing carries `words` in the player
  panel's `text.words` shape: the sidecar's `word_timings.words` for a
  heard line, `estimate_word_timings(text, clip duration)` for a fresh or
  missed one. It is sent as an `item` when the line starts and in every
  `items` while it plays. `state` carries `position` (seconds): mpv's
  `time-pos` when the player answers, else the control's clock (the
  position when it started, a pause, a seek or a rate change, plus elapsed
  time × rate while playing). It is sent with the state on start, pause,
  resume, seek and rate change; the UI Utility runs its own clock between.
- **Transport.** `start`: the line playing from its start (mpv seek 0).
  `end`: the line playing ends, so the next plays. `skip-back` and
  `skip-forward` with `seconds` (a positive number): mpv seeks that far
  within the line (`NativeAudioSink.seek`). Without `seconds`, the old
  behaviour. All are handled on the event thread at once (ADR-024's
  amendment) and none touches the narrator process.
- **The queue tab.** `{"cmd":"queue","id":"auto-speech","items":[{"id",
  "text","time","source"}…]}` is the arbiter's waiting items in the order
  they will play (P1, P2, P3, P4, each first in, first out). Ids are the
  item's `queue_id`, stable while it waits. The arbiter tells the narrator
  of every change (queued, taken to play, removed, moved, shed, purged);
  the control marks the queue to be sent and its worker sends it at most
  every 0.25 s, after the open, and never on the event thread. A UI
  Utility before 1.6 refuses `queue`: it is not sent again on that
  connection and the control stays open.
- **Queue edits act in place**, on the event thread (no request, so they
  never wait): `clear` takes every waiting line out, `remove` the named
  ones, `move` one to an index of the play order. The line playing plays
  on. One log line each: `queue: cleared <n>`, `queue: removed <n> (<ids>)`,
  `queue: moved <id> to <index>`. A line's id in the line ledger is
  released, so the same line can be sent again.
- **A move stays in its band.** P3/P4 (narration) are purged by a barge-in
  and shed when full; an explicit line moved among them could be lost, and
  a narration phase moved among explicit lines would play as one. A move
  past its band's edge stops there and the log says so. In practice nearly
  every waiting line is explicit (desk lines, replays), so the tab's order
  is free within them.
- **Cleared and removed lines are not put on the missed list**, as asked:
  he chose to drop them. The Desk may want live lines (not replays) kept on
  the missed list instead, so a slip of the hand never loses a desk's
  message unheard; that is a one-line change in `_queue_taken`.
- **`auto-speech queue [--json]`** lists what waits, through the daemon's
  new `queue` action. A read: it never starts the narrator.

## Consequences
- The control's list no longer grows a row per replay, and Play from runs
  are visible and editable in the queue tab.
- Tests: `tests/test_control_round_1030.py` (the real arbiter, a live
  worker into a gated sink, the wire against the stand-in UI Utility, and
  the CLI).
