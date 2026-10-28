# ADR-026: one line, one missed-list entry, one play, across his stop

Status: accepted, 2026-10-27 (#m1027-1628-c253, passed on as #m1027-1628-7cf5)

## Context
His 2026-10-27 16:27: "Why am I hearing duplication when you say things?"

The record (the narrator's log, the history sidecars, desk-speaker's `said.jsonl`):

- **16:22 and 16:27, each line twice.** He stopped speech at 23:22:25Z while
  a Master Desk line played, and again at 23:23:22Z. desk-speaker handed each
  line it held to the missed list (`missed add`, no line id) and also kept it
  to resend. His play from the speech control ended the stop by playing the
  missed copy, which had no id, no sender and no file in its history. Then
  desk-speaker's resend, carrying the id, was admitted and played again
  (23:26:28Z and 23:27:15Z). The 16:22 resend was refused only because the
  ledger had counted the stop-cut line as done.
- **13:03 to 13:07, three lines three times each.** In a meeting (10:38,
  10:59, 11:01) the narrator set each Status update line aside. desk-speaker
  took the drop as a cut, handed the line over (`missed add`) and sent it
  once more, and the narrator set the retry aside again. Each line was on the
  missed list three times, none with an id, and the list's replay played all
  nine copies in a row. "You missed 7 lines" counted the copies too.
- **The orphaned player.** At 18:01:42Z the 11:01 narrator began a line that
  a pause from the speech control then held (a paused line is never a stall).
  The 11:51 restart's SIGTERM gave it less time than its shutdown takes (15 s
  for the line in flight, 3 s, up to 5 s for threads). launchd's SIGKILL
  followed (no "shutdown" in the log; the next narrator started 21 s later),
  and its mpv kept running with no parent.

## Decision
1. **A missed-list entry keeps its line's id** (`MissedLine.line_id`, with
   `origin_file`). The list holds one entry per id: a second set-aside, or a
   caller's `missed add` for an id the narrator already holds, is the same
   entry, under the id it first had.
2. **A replay carries the id, the sender and the file** (`missed --play`, the
   control's play-item and play-all, his resume). So the ledger sees one line
   whichever path delivers it, and the history names its sender.
3. **The narrator refuses an id it has played** for as long as it keeps the
   id: a day (the missed list's own longest age), not ten minutes. The ledger
   holds at most 2,000 ids; past that the oldest settled ones go first. It
   lives beside the missed list (`~/.local/state/auto-speech/lines.json`) so
   it outlives a reboot; the old `/tmp` file is read once.
4. **A line heard leaves the missed list by every copy**: when it plays, and
   when a replay of a copy is refused as already heard.
5. **`missed add --line-id ID --origin-file NAME`**: a line already heard is
   not added; one already on the list is the same entry.
6. **One holder across his stop: desk-speaker's pending file** (decided with
   desk-speaker, its metal note). desk-speaker no longer hands a line it
   still holds to the missed list. It hands a line over, with its id, only
   when it lets go of it (stale, skipped). A line the narrator set aside
   itself (its ledger record names a `missed_id`) is the narrator's, and
   desk-speaker never resends it. Why desk-speaker's side: it plays the line
   when the stop lifts however it lifts (the narrator replays set-aside lines
   only on a plain resume), it skips a line gone stale instead of playing it
   late, and the stop stays visible on the desk.
7. **A narrator starting up stops an orphaned player of its own**: the pid
   in `auto-speech-mpv.pid`, only when it is mpv with auto-speech's own flags
   and its parent is pid 1. A player another live process owns is never
   touched.

No new check was added. Refusing an id already played is the dedupe the Desk
ordered, and his 2026-10-15 rule puts hard checks last.

## Consequences
- The same line arriving by both paths, in either order, plays once (tests:
  `TestOnePlayAcrossStop` in `tests/test_line_id.py`).
- A line he stopped while it played counts as heard, as ADR-025 already had
  it. It is not replayed by any path. He can replay it from the history.
- While a stop holds, a desk line waits in desk-speaker's pending folder and
  is not listed in the control's missed list. It plays when he resumes.
