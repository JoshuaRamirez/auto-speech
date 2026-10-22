# ADR-017 — Sound holds speech only when a player app makes it; an unknown app's open stream never holds

**Status:** Accepted
**Date:** 2026-10-18
**Depends on:** ADR-015 (speech yields to other apps' sound; the missed list).

## Context
On 2026-10-18 from about 19:20 Joshua heard no updates ("I'm not hearing
any updates out loud", 19:33). Every line had been held and then set aside
as missed, fourteen in all, because Antigravity's helper
(`com.google.antigravity.helper`, pid 2856) counted as playing. It was
not making a sound anyone could hear: an editor's helper that keeps an
output stream open.

ADR-015's detector reads, for each process object in Core Audio's
`kAudioHardwarePropertyProcessObjectList`, the flag
`kAudioProcessPropertyIsRunningOutput`. That flag is true for a process
running IO with an active output stream, whether the stream carries music
or zeros. A read-only poll of the live list (19:41–19:43) showed the
helper running output the whole time, silent; Chrome's helper did the
same in bursts of seconds to minutes all afternoon (the oldest missed
line, 16:23, was held by it). The macOS 26.6.2 header exposes nothing
else per process: pid, bundle id, devices and the three running flags.

The output level of a process can only be read through a Core Audio
process tap. A tap captures the audio and asks for the System Audio
Recording permission. Both are outside the standing limits (never capture
or tap audio; no new permission, no new access), and the Desk ruled it
out on 19:45. So the daemon cannot tell a silent open stream from a quiet
passage of a real player, and a rule has to stand in for the measurement.

## Decision

### Hold only for apps on a players list
`hold_for = "players"` is the default. A process running output holds
speech only when its app is on the **players list**: apps that play
sound a person listens to. Built in: Music, TV, Podcasts, QuickTime
Player, Safari (and the WebKit GPU process that plays its media),
FaceTime, Spotify, VLC, IINA, mpv, afplay, ffplay, Chrome, Arc, Edge,
Firefox (and its media process), Brave, Zoom, Teams, the ChatGPT app and
the Claude app, plus whatever Joshua adds. **An unknown app's open stream
never holds.** The exception list still subtracts from the players, as
before, and `hold_for = "any"` restores the old rule (every app except
the exceptions) for anyone who prefers it.

Why an allow-list and not a longer deny-list: the two ways of being
wrong are not alike. Holding for a silent stream is a silent failure, as
on 2026-10-18: lines go to the missed list and nobody hears that
anything is missing. Playing over an unlisted app's sound is audible: he
hears it, and one `yield player-add` fixes it for good. A guess should
fail where it can be noticed.

Why Chrome stays a player even though its helper held lines this
afternoon: the open mic and speaker on Chrome's helper may be his
ChatGPT voice session, the long listen the hold was asked for on 10-18
15:28; a quiet moment in it looks the same to Core Audio as Antigravity's
silence, and this rule cannot tell them apart. If that proves wrong he
removes Chrome (`yield player-remove Chrome`) or excepts it.

### Matching
An entry matches an app's bundle id exactly, or as a prefix at a dot
(`com.google.Chrome` covers `com.google.Chrome.helper`, which is where
Chrome's sound comes from), or the bundle id's last component (`Music`),
or the process name (`afplay`); case-insensitive. The same matcher serves
the exception list. `player-remove Chrome` removes `com.google.Chrome`.

### Same names everywhere
CLI `auto-speech yield hold-for players|any`, `player-add <apps…>`,
`player-remove <apps…>`, `player-reset`; MCP `sound_yield(action=…,
apps=…)` with the same actions; config `[sound_yield] hold_for` and
`players` (the full list; when the key is absent the defaults apply;
the first write records the full list). `yield status` shows the rule
and the list; `yield check` names each running app and says why it does
or does not hold: "holds speech", "on the exception list", "not on the
players list", "auto-speech's own".

### What does not change
The hold itself, the longest wait, the missed list and the replay order
(ADR-015). The device fallback (no process list) still holds for anything,
and says that the players list and exceptions cannot apply there.

## Consequences
- The Antigravity exception added at 19:34 is no longer needed and comes
  off once this is live.
- A new game, a new player or a terminal tool not on the list is spoken
  over once; `yield check` shows it as "not on the players list" and
  `yield player-add` fixes it.
- The true fix, a per-process level with a short silence hysteresis,
  stays available as a later decision if Joshua ever allows a tap by
  name; nothing here precludes it.

## Invariants
- I-20.1 With `hold_for = players`, a process that is not own, not
  excepted and not a player never holds speech, whatever its flags.
- I-20.2 A player's helper matches by bundle-id prefix at a dot.
- I-20.3 Exceptions win over players; `hold_for = any` restores the
  ADR-015 rule unchanged.
- I-20.4 `status` names the rule and the list; `check` names every
  running app and the reason it does or does not hold.

## Amendment, 2026-10-21: Chrome always holds (#m1021-1854-f2a9)
His 18:54: "im using speech feature in chrome - this is what i wanted,
with a transcript below it. also, dont play auto speech when im using it.
also, when u cant say things, that app will let me play what i missed".
His exception list held Chrome out (ADR-018: "for its videos"), so
Chrome's read-aloud played under auto-speech.

Chrome is now an **always-hold app** (`ALWAYS_HOLD_APPS`): while Chrome
or one of its helpers runs output, speech holds as it does for Spotify,
and a line still held at the longest wait goes to the missed list, where
the Spoken Transcript app plays it (`missed --play`). The exception and
players lists do not apply to it. This is in code, not settings: his
config is unchanged and no setting turns it off. The meeting check still
comes first, so Meet in Chrome stays a meeting.

The cost is the one this ADR names above: Core Audio cannot tell
Chrome's read-aloud from a silent open stream in a tab, so a silent
stream can hold speech too and set lines aside. That is now the accepted
way to be wrong, since he can play what he missed.

- I-20.5 A process matching `ALWAYS_HOLD_APPS` that runs output holds
  speech (reason sound) whatever the players and exception lists say;
  only a meeting outranks it. `status` lists the always-hold apps, and
  `check` says why such a process holds.
