# ADR-022: the speech control drives the UI Utility's control piece

Status: accepted, 2026-10-21 (#m1021-2027-04bb, card 73)

## Context
His 2026-10-21 20:07-20:09, after the player panel (ADR-021) showed a
line as a page: "not the actual page - i want the same thing that shows
up like in the screenshot - do u remember" / "in a mac native" / "no i
asked today" / "see - and i wanted a transcript". The UI Utility 0.3.0
(contract v1 rev 1.2) has a control piece: a floating control cloned
from macOS's own speech controller, with a list below it. The control
plays nothing; its caller plays, tells it the state and the list, and
acts on what the person asks for.

## Decision
`speech_control.ControlDriver` drives one control (panel `control`, id
`auto-speech`) behind the existing `[history] player` setting.

- **When it opens.** When a line starts playing and the setting is on
  (amended 2026-10-31, see below; it used to open at daemon start too):
  never while a hold stands (a meeting, or another app's sound), never
  when the UI Utility is not installed. A control he closes stays closed,
  across daemon restarts, until he reopens it himself. Turning the setting
  off closes it.
- **What it shows.** The source is the speaker of the line playing (the
  desk's name; auto-speech's icon). The list, newest first: the line
  playing now (a provisional `now-<n>` item until the history names it),
  the history's newest 50 lines (clip = the history WAV), and every line
  on the missed list as an unplayed item (clip null). The rate is the
  playback rate; the rates are 1, 1.25, 1.5, 2 and 0.75.
- **Keeping it current.** `item` and `state playing` when a line starts
  (after any hold); `state held` when a hold begins; `items` and
  `state idle` when the line ends, heard or set aside; `items` when a line
  is set aside outside a play; `source` when the speaker changes.
- **Its events.** play resumes a line he paused (a hold for another app's
  sound is not his to resume), or replays the newest line when idle;
  hold pauses the line playing (mpv's pause; a paused line is not a
  stall, ADR-019); stop is the barge-in (stop and clear the queue);
  skip-back restarts the line playing, or replays the line before the
  current one; skip-forward ends the line playing. rate sets mpv's speed
  for this and every later line (0.25 to 4). play-item replays a heard
  line's WAV over any other sound, without exporting it again
  (`QueueItem.history_id`), or plays a missed line through
  `missed_speech.play_ids`, which takes it off the list once heard.
  play-all plays each id in order; play-from (contract 1.5) plays every
  line from a chosen time, oldest first (ADR-027).
- **The per-line player panel** (ADR-021) no longer opens by default; it
  opens only when one is passed in. His "not the actual page" retires it
  from the daemon.
- **Threads.** The narrator's calls only change the driver's model and
  wake its worker; the worker owns the connection. The client's reader
  thread hands events to the worker, since a request sent from the reader
  would wait on a reply only the reader can read.

The contract stays generic: the domain words (desk names, the missed
list, the history) live here.

## Consequences
- A paused line holds the queue until he presses play or stop.
- A long pause shows as an overdue step in `doctor` (it reads the
  worker's expected end); nothing restarts the daemon over it.
- The rate is the daemon's for its lifetime; a restart starts at 1. (Closed by ADR-023: the rate is kept in history.toml.)
- Invariants: I-22.1 never opened during a hold; I-22.2 every item id is
  unique and the list is newest first; I-22.3 a replayed history line is
  never exported twice; I-22.4 an event never blocks the client's reader.

## Amendment, 2026-10-31: opens only when a line plays; his close is kept (#m1031-1313-9a54)
His 2026-10-31 12:53: "why is my speech app opening on its own over and
over", and 13:09: "yes fix the speech thing". The narrator idles out after
600 s and launchd starts it again; each start's `start()` woke the worker,
which opened the control, and any line cleared his "closed it" mark, which
lived only in memory and was lost on every restart.

- `start()` applies the kept rate to the sink and the model only; it wakes
  nothing and sends nothing. `playing()` is the one call that asks for an
  open. A sync with no line asked for opens nothing and launches nothing
  (`no line`); updates go only to a control already open. A connection
  lost while idle is opened again with the next line, not on its own.
- His close (`closed` with `by: "user"`) is kept in
  `$AUTO_SPEECH_TMP_ROOT/auto-speech-control-closed.json` (default /tmp),
  read at construction and at each sync, so a restart keeps it. While it
  stands, no line opens the control. A close that is not his leaves it as
  it was.
- Only his own reopening clears it: `auto-speech history player-on` (CLI,
  or the MCP tool `history(action="player-on")`), after which the next
  line opens the control; or the settings piece's Speech control switch
  turned on, which opens it at the next sync. A line playing never clears
  it. The contract has no "opened by him" event, so there is no third way.
- Invariant I-22.5: the control is opened only for a line playing or his
  own reopening, never by a daemon start.
