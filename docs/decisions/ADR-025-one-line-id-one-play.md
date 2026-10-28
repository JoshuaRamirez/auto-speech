# ADR-025: one line id, one play, even under heavy load

Status: accepted, 2026-10-22 (#m1022-0045-542f, #m1022-0052-03b1; outcome 0043 T03). Amended by ADR-026
(2026-10-27): a done id is refused while kept, a day, not ten minutes; ids are kept a day, at
most 2,000, beside the missed list.

## Context
His 2026-10-22 00:42: "Check your auto speech log. It's something messed up
when the LLM was pegging the system."

The record: while a 24 GB local model pegged the Mac (00:35-00:40), the
narrator accepted the Master Desk's 00:35 line at 07:36:08Z and took 2.5
minutes to synthesize it (playing audio at 07:38:42Z). desk-speaker, polling
the socket, hit a TimeoutError, took it for a dropped connection and sent the
same line three more times (07:40:03, 07:40:33, 07:41:11Z). The narrator
queued and played every copy: it knew no line id (`source_hash=None`). One
line played four times, the 00:37 line twice, and the queue reached 5.

A second source of copies was in auto-speech itself: `DaemonClient` sends a
request again after a timeout (up to `max_retries`, 2). A speak the daemon had
accepted but answered late was queued again by the client's own retry.

## Decision

### 1. A line id
Every speak request may carry `line_id`: the MCP speak tool, `auto-speech
speak --line-id`, `DaemonClient.speak(line_id=…)` (desk-say's path), and
`narrate_event`. desk-speaker sends its pending file's stem, e.g.
`2026-10-22-003532.636-desk`.

A speak whose id is **queued, playing, or done within the last ten minutes**
is acknowledged and not queued again:
`{"status": "ok", "action": "speak", "duplicate": true, "line_id": …, "line": {…}}`.
A **dropped** id is admitted again: a replay follows only a real
interruption. A speak without an id gets one from `DaemonClient`
(`client-<16 hex>`), so the client's own retries carry one id. The id the
line was queued under is in the reply (`line_id`).

A `narrate_event` id is taken in once: an event whose id the narrator has
seen in the last ten minutes is ignored.

### 2. Status per id
`{"action": "status", "line_id": ID}` adds
`"line": {"id", "line_id", "state", "queued_at", "synth_started",
"playing_at", "done_at", "dropped_at", "reason", "sends", "plays",
"retries", "text"}` to the status reply. `state` is queued | playing | done |
dropped, or `unknown` for an id not seen in the last hour. `reason` says why
a line was dropped: `stopped` (his stop), `restart` (lost in a restart, or
set aside by the shutdown), `interrupted`, `failed`, or the missed list's own
reason (timeout, meeting, stale backlog). Every status reply also carries
`line_in_flight`: the record of the line the worker has in hand, when it has
an id. `auto-speech narrate status --line-id ID` prints one line for it.

The field stays `line_id`. The history player also has a `line_id` (its
own hex id), but only in its messages to the UI Utility (`speech_player.py`).
The two never meet in one request or reply.

### 3. A slow synthesis is reported as such
While a line synthesizes, its state stays `queued` with `synth_started` set,
so a caller's wait is informed, not blind. It becomes `playing` when the sink
starts it, and `done` when it played to its end.

### 4. The ledger
`line_ledger.py`: one record per id, kept an hour, in
`auto-speech-lines.json` in the state folder (written on each change). An id
survives a restart. A line still queued or playing when the daemon starts was
lost in the restart and reads `dropped`, reason `restart`. The worker marks
each pass: taken, synth started, playing, done. A line it had in hand that
neither played, nor was set aside, nor went back on the queue is dropped,
`interrupted` when a stop or barge-in cut it, else `failed`. A retry after a
timeout keeps the id and reads `queued` again. A line set aside on the
missed list remembers its missed id, so its replay (a resume, the control's
play, `missed --play`) is the same line to the ledger.

## Consequences
- desk-speaker asks instead of guessing: a slow line reads `queued` with
  `synth_started`, never a cut.
- A caller that sends the same id again gets the state back and nothing is
  queued. This holds across a restart for ten minutes after the line played.
- The ledger writes one small file per state change, a handful per line.
- Not changed: a line with no id from an older caller is queued as before.
  The client's own id only covers its own retries.
