# ADR-015 — Speech yields to other apps' sound: hold, then set aside as missed, never dropped

**Status:** Accepted (amended 2026-10-18 16:10: no line is ever dropped; a missed list)
**Date:** 2026-10-18
**Depends on:** ADR-006 (file-queue concurrency), ADR-009 (mpv controller), ADR-012 (Stop-hook autoplay).

## Context
auto-speech spoke whenever its turn in the queue came, even while music, a
video or a call was already coming out of the speakers. Joshua's ask
(2026-10-18 15:28): "upgrade auto speech to not play by default when
something is already playing sound. There can be an exception list
provided, so it has to be supported in the existing API, whether CLI, MCP,
or both."

The first cut (v0.3.0, the same afternoon) held speech for up to 180 s and
then **dropped** it. That broke the brief's own line, "held speech isn't
lost": Joshua listens to ChatGPT for long stretches, and on 2026-10-18 he
missed about twenty of the Desk's lines that way. The Desk's amendment
(16:09): never drop; after the longest wait the line stays; when it goes
quiet, do not play a stale backlog line by line; keep every held or expired
line in a list he can read or replay through the CLI and the MCP, with the
same names and shape, and clear each line once it has played.

Three things had to be decided beyond the ask: how to tell that another app
is playing without any new permission, what "stale" means and in what
order a backlog replays, and where the list lives.

## Decision

### Detection: Core Audio's process list, read through ctypes
The gate reads `kAudioHardwarePropertyProcessObjectList` on the system
object and, for each process object, `kAudioProcessPropertyIsRunningOutput`
(macOS 14.2+), `kAudioProcessPropertyPID` and
`kAudioProcessPropertyBundleID`. This is a property read on the HAL, not a
tap: no audio is captured, no entitlement or TCC prompt is involved, and
one read costs a few milliseconds. When the process list is not available
(older macOS, or a HAL error) the gate falls back to the default output
device's `kAudioDevicePropertyDeviceIsRunningSomewhere`, which can only
say that *something* is playing. In that mode the exception list cannot
apply, and the gate says so in `yield check`. If neither read works the
gate fails open: speech plays, and the daemon log carries one line saying
why.

auto-speech's own playback never counts. A playing pid is "ours" when it
is the calling process, the pid in `/tmp/auto-speech-mpv.pid`, a child of
the calling process, or when its command line or an ancestor's (up to
five hops, from one `ps` snapshot per assessment) contains `auto-speech`.
That covers the daemon's mpv, the web server's mpv and the detached say
workers without any registry of pids.

### Where the hold lives: inside `NativeAudioSink.play()`
Every playback path (daemon, CLI cached replay, web app, playback
consumer) goes through the sink, so the hold sits there, after the
playback lock is taken and before mpv is spawned. Queue order is kept:
the held item still owns its turn, and the items behind it wait as they
would behind a long playback. `interrupt()` releases a hold the same way
it stops a playback, so a P1 user interrupt is never blocked by music.
Past the longest wait the sink raises `PlaybackYielded` and never starts
mpv; what happens to the line is the caller's decision, and for the daemon
that decision is below. `play(hold=False)` skips the gate for one
playback: an explicit replay of a missed line plays over whatever else is
on. Tests that drive a real sink against a mocked mpv rebind the gate
factory to `None`; the gate itself is tested against a fake probe, a
canned process table and a fake clock.

### Nothing held is ever dropped: the missed list
The daemon keeps a **missed list**, one JSON file at
`~/.local/state/auto-speech/missed.json` (`AUTO_SPEECH_MISSED_FILE`
overrides it; the test runner points it into the isolated state
directory). A line goes on the list the moment the sink holds it, as
*held*, with the apps that were playing. It leaves the list only when it
has played. It stays on the list as *missed* when:

- the **longest wait** passes with the sound still going;
- it is **stale backlog**: once the sound stops, a line that waited behind
  the hold and is older than the longest wait is set aside instead of
  playing late (below);
- a user **interrupt** releases its hold (the line was never heard);
- the **daemon restarted** while it was held (settled on start).

`auto-speech missed` lists every line, oldest first, with when it was
asked for, why it is on the list and its text. `missed --play` re-speaks
the missed lines in that order, over any other sound, through the daemon
with the line's id attached, and the daemon removes each one once it has
played; held lines are left to play by themselves when the sound stops.
`missed --clear` forgets them all. The MCP tool `missed(action = list |
play | clear)` runs the same `run_action` and prints the same text, and
the slash command `/auto-speech-missed` runs the CLI.

### Stale: older than the longest wait (180 s), counted from the ask
There is one number, `max_wait_seconds` (default 180). It is the longest
a line waits for the other sound, and it is counted from the moment the
line was asked for, not from the moment its turn came: a line that spent
100 s in the queue behind an earlier hold is held at most 80 s more. The
same number decides what is stale when the sound stops: a line that sat
behind a hold and is now older than it goes to the missed list; a younger
one plays. A line that is merely old because the queue was busy, with no
hold since it was queued, still plays as before; the rule is scoped to
sound, not to congestion.

Why 180 s: the spoken material is a turn summary, a narration line, a
desk notice or a `speak` call, all of which lose their point within a
few minutes. Three minutes covers a song break, a short video or the end
of a call, which is when the user would still want to hear what was
queued without asking. Why set aside rather than wait forever: the TTS
worker blocks in the sink while holding, so an unbounded hold would stall
the queue for the length of the music and then play every stale line in a
burst, late and out of context; the Desk's own words were "don't play a
stale backlog line by line". Why not drop: the user did not hear the
line, and nothing about that is his choice; the list gives him the text
and the replay. Why the queue age and not the hold time: with per-hold
timing a backlog of twenty lines would take an hour to drain, each line
held for the full 180 s in turn; with the age rule each line is set aside
about three minutes after it was asked for, so the list fills in step
with what he is missing and the daemon stays responsive.

### Replay order: oldest first, over other sound
A replay reads like a transcript of what was missed, so it goes oldest
first. It is an explicit request, so it plays now, over other sound
(`hold=False`): he asked for it while listening, and a replay that hid
behind the same music would be the problem again. Replayed lines are
spoken from their text (or their cached audio when there is no text), at
the voice and speed they were asked for, as P2 items, so they queue
behind nothing but each other.

### One shape in three places
The CLI (`auto-speech yield`, alias `sound-yield`; `auto-speech missed`),
the MCP tools (`sound_yield`, `missed`) and the config file
(`~/.config/auto-speech/sound-yield.toml`, section `[sound_yield]`) use
the same names: `enabled`, `exceptions`, `max_wait_seconds`,
`poll_seconds`; `list | play | clear`. The CLI and the MCP tool share one
`run_action` per surface so their verbs and their output text are
identical, and both write the same files the daemon reads. Exceptions
match case-insensitively by bundle id (`com.apple.Music`), by its last
component (`Music`) or by process name (`Spotify`). `after_max_wait`
(v0.3.0's `drop | play`) is gone: the loader names it in a warning and
ignores it.

## Alternatives considered
- **Polling `ps` for known media apps.** Fragile: it cannot tell a paused
  player from a playing one, and misses browsers and calls.
- **An audio tap or input monitoring.** Needs a permission prompt and
  would capture audio; ruled out by the brief.
- **Holding in the daemon's queue rather than the sink.** Would leave the
  CLI's direct cached playback and the web app unguarded, and would need
  priority-aware logic in two places.
- **Drop after the longest wait (v0.3.0).** Rejected by the amendment:
  held speech was lost.
- **Play after the longest wait.** The behaviour the ask removes, only
  delayed; and a long album would surface every queued line late and over
  the music.
- **Wait without limit.** Stalls the queue for the length of the music,
  then plays a stale burst. Rejected above.
- **Stale by hold time rather than queue age.** Drains a backlog one
  full wait per line; rejected above.
- **Keeping the missed list in the daemon's memory.** Lost on the 600 s
  idle shutdown, which is exactly when he would come back to read it.
- **A separate expiry per priority tier.** The sink has no notion of
  priority; one policy is simpler to explain and configure. Revisit if a
  real case appears.

## Consequences
- New module `plugin/scripts/python/missed_speech.py` (the list, its
  file, its text, the shared action runner). `sound_yield.py`: `EXPIRED`
  outcome, `already_waited` and `on_hold` on `wait_until_clear`.
- `NativeAudioSink.play(hold=, already_waited=, on_hold=)`, `last_hold`;
  `PlaybackYielded` means set aside, never dropped.
- `narrator_service` records held, missed and played lines, applies the
  stale-backlog rule before synthesis, honours `missed_id` and `hold` on
  `speak` and `play_cache`, reports `missed` counts in `status`, and
  settles held lines as missed on start. `QueueItem` carries both flags;
  `DaemonClient.speak/play_cache` send them.
- CLI subcommand `missed [--play | --clear]`, MCP tool `missed`, slash
  command `/auto-speech-missed` (extras), config example without
  `after_max_wait`, doctor validation updated.

## Invariants (additions)
- **I-18.1 Own playback never holds.** No pid that is auto-speech's own
  (by the rule above) appears in `blockers`.
- **I-18.2 Order is kept.** A held playback keeps its turn; nothing behind
  it starts before it ends (played, set aside or aborted).
- **I-18.3 Bounded hold.** No playback is held past `max_wait_seconds` of
  age plus one poll interval.
- **I-18.4 Live settings.** A change to the file, by any of the three
  surfaces, takes effect at the next poll of a hold in progress and at the
  next playback otherwise; no daemon restart is needed.
- **I-18.5 Fail open.** When no probe can answer, speech plays.
- **I-18.6 Nothing held is lost.** Every line the daemon holds is on the
  missed list from the moment of the hold until it has played; a line set
  aside for any reason stays there until it plays or is cleared.
- **I-18.7 No stale burst.** After a hold ends, a line older than
  `max_wait_seconds` that was queued before the hold ended does not play
  by itself.
