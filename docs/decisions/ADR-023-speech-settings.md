# ADR-023: auto-speech's settings in the UI Utility's settings window

Status: accepted, 2026-10-21 (#m1021-2314-d88c, card 80)

## Context
His 2026-10-21 22:26, through the Desk (#m1021-2233-42db): "keep the Mac
version that wasn't named AutoSpeech… integrate the newest thing into it so
they're the same thing and I can do settings on them both. And I want them
to icon bar up the top." The UI Utility's contract v1 rev 1.3 adds a
settings piece: one settings window for every caller, opened from its menu
bar item. The caller registers groups of items and applies each change; the
window only shows the values.

## Decision
`speech_control.ControlDriver` keeps one settings piece open beside the
control, behind the same `[history] player` setting (ADR-022), on the same
connection and worker. `speech_settings.SpeechSettings` holds what each
item means.

- **Id `auto-speech-settings`, source "Speech".** The order named the id
  `auto-speech`, but the UI Utility keeps one id space across pieces
  (PanelHub: opening a settings piece closes a control with its id), and
  the control already has `auto-speech`.
- **Two groups.** Speech: mute (the global marker
  `~/.claude/auto-speech.disabled`), autoplay reads (verbatim | summary,
  `autoplay.toml` mode; "raw" shown only while the file says raw), summary
  size (`autoplay.toml` summary_size), hold speech while another app plays
  sound (`sound-yield.toml` enabled), rate (0.75 to 2, step 0.25), name the
  speaker (`speaker.toml` enabled). Transcript: show the transcript when the
  control opens (`history.toml` show_list, sent as the control's
  `show.list`), lines kept (`history.toml` keep, 10 to 1000, step 10: how
  many heard lines the control lists), speech control (`history.toml`
  player), clear the missed list (action).
- **Apply where it lives, then confirm.** A `setting` event is applied on
  the worker: written to its file or marker, which the daemon already reads
  again where it is used (the gate per call, the announcer by mtime, the
  autoplay worker per turn, the history per load). The rate is applied to
  the sink now as well. Then `values` confirms what is in effect. A change
  that fails (out of range, a write that cannot land) sends the old value
  back and is logged with why. The UI Utility's files are never written.
- **The rate is kept.** `history.toml` gains `rate` (and `show_list`). A
  rate set at the control or in settings is written there, and the daemon
  applies it to the sink at start. This closes ADR-022's "a restart starts
  at 1".
- **Off closes both.** Turning the speech control off (from settings or
  `history player-off`) closes the control and the settings piece. A copy
  without the piece (rev 1.2) gets the control alone; a refused open is
  logged once and leaves the control alone. If he closes the control, the
  settings stay while the connection does.

## Consequences
- The list's length follows `keep` (200 by default) instead of a fixed 50.
  Missed and held lines are still never cut (#m1021-2128-2357).
- autoplay.toml is rewritten whole when its mode or size changes: scalar
  keys and one level of tables, which is its whole shape; comments are not
  kept.
