# ADR-020 — Every line played is kept as a WAV and a sidecar; the last 200 by default

**Status:** Accepted
**Date:** 2026-10-20
**Depends on:** ADR-019 (speech under load: a timed-out line is retried whole), ADR-015 (the missed list), ADR-016 (speaker names), ADR-018 (the meeting hold).

## Context
His 13:38, 2026-10-20 (the Master Desk #m1020-1339-68ee): "Each time the
Desk speaks, a small player pops up and plays the line's WAV. Controls:
play, pause, fast-forward, back to the beginning, rewind, with a scrubber.
Below the controls, the transcript, highlighting the current position … A
history button shows everything said before. Only the last N WAVs are kept
(N configurable), so the disk never fills."

The player is built by the UI Utility agent against its own caller
contract (`~/Developer/UI-Utility/docs/external/CONTRACT.md`): the caller
hands it one clip and its time-aligned words and holds the application
state; the utility holds only UI state. So the history belongs to
auto-speech, and the player shows only what it is handed.

Until now a played line left nothing behind except, for a line with a
source hash, its replay-cache entry (ADR-008), which has no text, no
sender and no time, and a staging WAV that is deleted after playback.

## Decision

### Export at the point of hearing
`NarratorService._play_item` returns True only when the sink played the
line. There, and nowhere else, the daemon calls
`SpeechHistory.export()`. A line the meeting hold sets aside before
synthesis, or the sound yield sets aside past the longest wait, a stale
backlog line, an interrupted hold: none reaches that point, so none is
exported. A held line that later plays (by itself or by `missed --play`),
or a timed-out line that plays on its retry (ADR-019), is exported then,
once. An export failure is logged and
never stops speech.

### On disk
One line is two files in `~/.local/state/auto-speech/history/`
(`$AUTO_SPEECH_HISTORY_DIR`): `<YYYYmmdd-HHMMSS-mmm>-<id>.wav`, a copy of
the WAV that played, and `<same>.json`, the sidecar. The WAV is written
first and the sidecar last, each by tmp + rename, so a line with no
sidecar is not in the history; a WAV left without one for over a minute
is removed at the next prune. Names sort in time order.

The sidecar (version 1): `text` (the words in the audio: with the
"<desk> here," prefix when it was synthesized in, without it when a cached
clip played after a separate name clip), `spoken_at` (local ISO time, ms)
and `spoken_at_epoch`, `sender` (`session_id`, and the desk `name` from
the agents' notes whether or not it was said aloud), `priority` and
`priority_name`, `duration_seconds`, `word_timings`, `wav`, `voice_id`,
`speed`, `source_hash`.

### Word timings: estimated, and marked so
The synthesizer is macOS `say -o file`, which writes a WAV and reports no
word boundaries: `say` has no option for them (getting them from
AVSpeechSynthesizer's word callbacks would mean a new native helper). So `word_timings.source` is `"estimate"`,
with its `method`: each word's share of the clip is its letters, plus a
pause after a comma (3 letters' worth) or a sentence end (6), spread over
the clip's measured duration. Words are in the UI Utility's shape,
`{"w", "start", "end"}`, so they can be handed over as they are. A
synthesizer that gives timings passes them to `export(word_timings=…)`
and they are kept with `source: "synthesizer"`. Forced alignment against
the WAV would be exact, and stays a later decision: it needs a model or a
download.

### N: `keep`, 200 by default
The same name in all three: `[history] keep` in
`~/.config/auto-speech/history.toml`, `auto-speech history keep N`, MCP
`history(action="keep", count=N)`. After every export the oldest lines
past N are deleted; lowering N prunes at once.

Why 200, measured from the live daemon log on 2026-10-20 (read only):
1,184 clips played since 10-18; the mean clip is 0.94 MB (about 20 s at
24 kHz 16-bit mono), the median 0.62 MB, the 90th percentile 1.8 MB. The
busiest day, 10-19, played 775 lines. At the mean, 200 lines is about
190 MB (about 370 MB if every one were a 90th-percentile clip), and holds
the last four hours or so of that busiest day, a whole quiet day. That is
enough for "what did it say this morning" and small next to the replay
cache, and the disk cannot fill: the history is bounded by N, not by time.
He raises or lowers it with one command.

### Settings and the shared verb
`[history] enabled` (true), `keep` (200), `player` (false). One
`run_action` serves `auto-speech history [list|status|on|off|keep N|
player-on|player-off]` and the MCP tool `history(action, count,
as_json)`, so both say the same thing. `list` is newest first with the
WAV paths; `--json` / `as_json` returns the sidecars with `wav_path` and
`sidecar_path`, which is what a caller hands the player. The config file
is read on every line: no restart needed. `config_validation` knows the
section, so the doctor warns on a typo.

### The player launcher, default off
`speech_player.PlayerLauncher.after_line()` runs after each export. With
`player = false` (the default) it does nothing. With it on, it does
nothing while a hold stands (the gate's assessment says hold: a meeting,
or a player app's sound; a gate that cannot say counts as holding), so
nothing pops up during a meeting; nothing while the UI Utility is not
installed (`ui-utility` on PATH, or `UI Utility.app` in /Applications or
~/Applications), said once in the log. Driving the utility by its
contract (launch, `hello`, `open` with the clip and words, `play`) is the
second step, wired in when the Desk hands over the published contract;
until then an installed utility is reported as "contract not wired yet"
and nothing opens. `history status` says which of these applies.

## Consequences
- A second copy of each played WAV is written (about 1 MB, a few ms after
  playback, on the worker thread). Bounded by N.
- `tests/run_all.sh` points `AUTO_SPEECH_HISTORY_DIR` and
  `AUTO_SPEECH_HISTORY_CONFIG` into its private state dir, so no test
  writes his history or his config.
- The prefix clip of a cached line (ADR-016) is not exported on its own;
  the sidecar's `sender` carries who it was.

## Invariants
- I-20.1 A line is exported if and only if the sink played it.
- I-20.2 After an export, at most `keep` lines are in the history.
- I-20.3 Estimated word timings are marked `source: "estimate"`, in order,
  within the clip, the last ending at its duration.
- I-20.4 The player never opens while the setting is off, while a hold
  stands, or while the utility is not installed.
