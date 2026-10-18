# ADR-015 — Speech yields to other apps' sound: hold, then drop after 180 s

**Status:** Accepted
**Date:** 2026-10-18
**Depends on:** ADR-006 (file-queue concurrency), ADR-009 (mpv controller), ADR-012 (Stop-hook autoplay).

## Context
auto-speech spoke whenever its turn in the queue came, even while music, a
video or a call was already coming out of the speakers. Joshua's ask
(2026-10-18 15:28): "upgrade auto speech to not play by default when
something is already playing sound. There can be an exception list
provided, so it has to be supported in the existing API, whether CLI, MCP,
or both."

Two things had to be decided beyond the ask: how to tell that another app
is playing without any new permission, and what to do with speech that is
held for a long time.

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
Tests that drive a real sink against a mocked mpv rebind the gate factory
to `None`; the gate itself is tested against a fake probe, a canned
process table and a fake clock.

### Longest wait: 180 seconds, then drop (configurable to play)
A held speech waits up to `max_wait_seconds` (default 180) for the other
sound to stop, polling every 0.5 s and re-reading the settings file on
every poll so `yield off` or `yield add <app>` releases a hold that is
already in progress. When the wait runs out the speech is **dropped** by
default (`after_max_wait = "drop"`); `"play"` makes it play over the other
sound instead.

Why 180 s: the spoken material is a turn summary, a narration line, or a
`speak` call, all of which lose their point within a few minutes. Three
minutes covers a song break, a short video or the end of a call, which
is when the user would still want to hear what was queued. Why drop, not
play: the ask was "not play when something is already playing"; playing
over the music after three minutes would be the behaviour the ask removes,
only delayed, and a long album would then surface every queued line late
and over the music. Why not wait forever: the daemon's TTS worker blocks
in the sink while holding, so an unbounded hold would stall the queue for
the length of the music and then replay stale lines in a burst. A dropped
item is not silently lost: the daemon log records the hold and the drop
with the blocking app's name, and autoplay audio is already in the replay
cache, so `/auto-speech-replay` can play it on demand.

### One shape in three places
The CLI (`auto-speech yield`, alias `sound-yield`), the MCP tool
(`sound_yield`) and the config file (`~/.config/auto-speech/sound-yield.toml`,
section `[sound_yield]`) use the same names: `enabled`, `exceptions`,
`max_wait_seconds`, `after_max_wait`, `poll_seconds`. The CLI and the MCP
tool share one `run_action` so their verbs (`status | on | off | add |
remove | clear | check`) and their output text are identical, and both
write the same file the daemon reads. Exceptions match case-insensitively
by bundle id (`com.apple.Music`), by its last component (`Music`) or by
process name (`Spotify`).

## Alternatives considered
- **Polling `ps` for known media apps.** Fragile: it cannot tell a paused
  player from a playing one, and misses browsers and calls.
- **An audio tap or input monitoring.** Needs a permission prompt and
  would capture audio; ruled out by the brief.
- **Holding in the daemon's queue rather than the sink.** Would leave the
  CLI's direct cached playback and the web app unguarded, and would need
  priority-aware logic in two places.
- **Play after the longest wait, by default.** Rejected above; available
  as `after_max_wait = "play"` for users who prefer late over never.
- **A separate expiry per priority tier.** The sink has no notion of
  priority; one policy is simpler to explain and configure. Revisit if a
  real case appears (for example, an explicit `speak` that should always
  play after the wait).

## Consequences
- New module `plugin/scripts/python/sound_yield.py` (settings store, Core
  Audio probe, process table, gate, shared action runner and text).
- `NativeAudioSink` takes a `sound_yield` gate (default from
  `DEFAULT_SOUND_YIELD_FACTORY`), raises `PlaybackYielded` on an expired
  drop, exposes `hold_status`, and `interrupt()` releases a hold.
- `narrator_service` builds its sink with a gate that logs to the daemon
  log; the `status` reply gains a `sound_yield` field.
- CLI subcommand `yield`, MCP tool `sound_yield`, config example
  `config/sound-yield.toml.example`, slash command `/auto-speech-yield`
  (extras), doctor validation of the file.

## Invariants (additions)
- **I-18.1 Own playback never holds.** No pid that is auto-speech's own
  (by the rule above) appears in `blockers`.
- **I-18.2 Order is kept.** A held playback keeps its turn; nothing behind
  it starts before it ends (played, dropped or aborted).
- **I-18.3 Bounded hold.** No playback is held longer than
  `max_wait_seconds` plus one poll interval.
- **I-18.4 Live settings.** A change to the file, by any of the three
  surfaces, takes effect at the next poll of a hold in progress and at the
  next playback otherwise; no daemon restart is needed.
- **I-18.5 Fail open.** When no probe can answer, speech plays.
