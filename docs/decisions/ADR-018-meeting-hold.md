# ADR-018 — A meeting is a second blocker: lines are set aside at once, one summary line follows

**Status:** Accepted
**Date:** 2026-10-20
**Depends on:** ADR-015 (sound yield, the missed list), ADR-017 (players list).

## Context
Joshua, 2026-10-20 12:24: "Hey, if I'm in a meeting, I don't want you to
talk." The sound-yield hold (ADR-015) already keeps speech out of music and
videos, but a call is different: speech must not start at all, and a call
runs for an hour, far past the longest wait. The Desk's order: build on the
existing hold, on-device and read-only, no new permission, no audio
captured; his own manual mode as well; held lines never dropped. The design
page (Auto-Speech-Meeting-Hold-Design-2026-10-20-123144.html) got his yes
at 12:32 with its defaults.

## Decision

### Detection: the same property walk, one more flag
`CoreAudioProbe.playing_processes()` reads, for every process object,
`kAudioProcessPropertyIsRunningInput` ('piri', macOS 14.2+) beside the
output flag it already read. It is a property read on the HAL, not an input
IO proc: nothing is captured, and the TCC log shows no microphone request
(checked 2026-10-20 12:33 on macOS 26: 39 objects read in 131 ms, no
prompt). A process with the flag on is a **meeting** when it is on the
`meeting_apps` list (Teams, Zoom, FaceTime, the browsers for Meet, Slack,
Webex, Discord); otherwise it is "microphone in use, not a meeting app"
(his CrownTalk on a press, the system's speech daemon) and never holds.
The meeting check runs before the exception list: Chrome is on his
exception list for its videos, and Meet in Chrome must still read as a
meeting. The device fallback cannot name a process, so it never declares a
meeting; without a process list he is not in a meeting (fail open, I-18.5).

### His own meeting mode: a time-boxed flag
`auto-speech yield meeting on` (60 minutes), `for 45m` / `2h` / `1h30m`
(8 h at most), `off`, `status`; the MCP tool `sound_yield(action="meeting",
apps=[...])` with the same words; the slash command `/auto-speech-meeting`.
The flag is `~/.local/state/auto-speech/meeting.json` (`AUTO_SPEECH_MEETING_FILE`;
the test runner points it into its isolated state directory), beside the
missed list, with `until`, `set_at` and `note`. Past `until` it reads as
off, so a forgotten `on` never mutes him for good. `yield off` switches the
sound hold and the microphone detection off together (they share the probe);
the manual flag still holds, because he set it by hand.

### A meeting never waits
`SoundYieldGate.assess()` settles a `reason`: `meeting` when a meeting app
holds the microphone or the flag is on, `sound` when a player plays, empty
otherwise; a meeting wins over sound. `wait_until_clear()` returns
`EXPIRED` with reason `meeting` and zero wait, at once, and a meeting that
begins during a sound hold ends that hold the same way. The daemon asks the
gate before synthesis (`_speak`, next to the stale-backlog rule) and sets
the line aside with no TTS call; a line the sink yields for a meeting is
recorded the same way. An explicit replay (`missed --play`, `hold=False`)
plays through, as it does through music. Nothing is dropped (I-18.6); a
longer wait for meetings would block the worker for an hour and then burst
(I-18.7).

### One summary line after the meeting
The daemon's main loop asks the gate every 2 s whether he is in a meeting.
When a meeting ends with lines set aside for it since the last summary (or
since ever, at the first look after a restart), it queues one line at the
narration priority, through the normal gate: "You missed N lines during the
meeting. Say auto-speech missed to hear them." `meeting_summary = false`
turns the line off. The daemon does not stay up for a meeting: if it idles
out, the summary comes at the first look after the next start. The lines
stay on the missed list for `missed --play`.

### One shape in three places
`[sound_yield]` gains `meeting_hold` (true), `meeting_apps` (the list),
`meeting_summary` (true); `yield status` and `yield check` say the meeting
state in the same words in the CLI and the MCP; the missed list has a
`meeting` reason and count.

## Alternatives considered
- **Calendar (Calendar.app, Outlook).** Every on-device route prompts
  (EventKit's Calendars, Automation for AppleScript, TCC on the store); the
  Outlook MCP is a network call; a booked event is not proof he joined.
  Off until his word.
- **Window titles, `log stream`, mic metering.** Screen Recording, a
  persistent subprocess with redaction, or TCC. Rejected.
- **The input device's running-somewhere flag as the trigger.** Cannot name
  the process; CrownTalk would trip it.
- **A longer wait for meetings; automatic replay; drop.** A stall and a
  burst; a burst; a loss.
- **A separate meeting module, or a flag under `~/.claude`.** A second
  system; the mute's home, a different meaning.

## Invariants (additions)
- **I-18.8 A meeting never waits.** With a meeting app holding the
  microphone or the manual flag on, no playback is held and no synthesis
  starts; the line is on the missed list with reason `meeting`.
- **I-18.9 The microphone alone never holds.** A process with input running
  that is not on `meeting_apps` holds nothing.
- **I-18.10 The flag expires.** `meeting.json` past its `until` reads as off.
- **I-18.11 One summary per meeting.** Lines set aside for a meeting are
  summarised once, by one spoken line, when the daemon sees the meeting end.
