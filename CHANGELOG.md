# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The Chrome extension under `chrome-extension/` is versioned independently
(its own changelog lives in `chrome-extension/README.md`).

## [Unreleased]

### Added
- **One line id, one play (ADR-025).** A speak request takes `line_id`
  (MCP speak, `auto-speech speak --line-id`, `DaemonClient.speak`,
  `narrate_event`). A line already queued, playing, or done in the last ten
  minutes is acknowledged with its state and not queued again. The status
  reply answers for an id (`{"action": "status", "line_id": ID}` → `"line":
  {"id", "state", …}` with timestamps, kept an hour), and so does `auto-speech
  narrate status --line-id ID`. A line that is synthesizing reads `queued`
  with `synth_started`.
- **`auto-speech missed add --text … --source NAME [--at HH:MM] [--id ID]`**
  (ADR-024): a caller puts a held or cut line on the missed list through the
  daemon; the speech control lists it unplayed under its source.
- **auto-speech's settings in the UI Utility's settings window (ADR-023).**
  His 2026-10-21 22:26: "I can do settings on them both. And I want them to
  icon bar up the top." With `[history] player` on, the daemon opens a
  settings piece (contract 1.3) beside the control: mute, autoplay reads,
  summary size, hold speech while another app plays sound, rate, name the
  speaker; show the transcript at open, lines kept, the speech control on,
  clear the missed list. Each change lands in its own file or marker and is
  confirmed; a failure sends the old value back and logs why. The rate is
  kept in `history.toml` and applied at start, so a restart keeps it; the
  control lists `keep` heard lines. Off closes both pieces.
- **The speech control: a Mac-native floating control with the transcript
  below it (ADR-022).** His 2026-10-21 20:07: "i want the same thing that
  shows up like in the screenshot" / "and i wanted a transcript". With
  `[history] player` on, the daemon opens the UI Utility's control piece
  (contract 1.2) at start and keeps it current: the speaker as its title,
  the line playing on top, the history newest first, and every missed
  line as an unplayed item. Its play, hold, stop, skip, rate, play-item
  and play-all act on the daemon: pause and resume through mpv, a heard
  line replayed from its WAV, a missed line played through the missed
  list. Never opened during a hold. The per-line player panel (ADR-021)
  no longer opens by default (#m1021-2027-04bb).
- **The UI Utility's player shows each line after it is heard (ADR-021).**
  His 2026-10-20 13:38: "Each time the Desk speaks, a small player pops up
  and plays the line's WAV". With `[history] player = true`
  (`auto-speech history player-on`) the daemon hands each exported line to
  the UI Utility by its contract v1 rev 1.1: `hello`, then `open` on one
  panel (`auto-speech`) with the history WAV, its word timings and
  `<sender> · HH:MM:SS`, ready to play and never playing on its own. The
  utility is launched in the background when its socket is missing. Never
  during a hold (checked again just before showing), nothing while the
  utility is not installed, and never on the speech path: a worker thread
  does the I/O and the newest line wins. `history status` shows the last
  outcome (`auto-speech-player.json` in the state dir). Built through one
  Spec Kit trial (`specs/001-player-launch/`). `ui_utility_client.py`;
  tests against a stand-in server; `tests/run_all.sh` sets
  `UI_UTILITY_SOCK`, `UI_UTILITY_SILENT=1` and
  `AUTO_SPEECH_PLAYER_LAUNCH=never`, so no test starts the real app.
- **Speech holds up under load (ADR-019).** His 2026-10-20 14:02, after a
  load of 272 on 16 cores timed five lines out and cut one off: the daemon
  clears any background clamp, puts its speech threads at user-interactive
  QoS and runs `say` and mpv under `taskpolicy -t 0 -l 0`; a niced start
  goes through `launchctl submit` so the daemon runs at nice 0. A play ends
  only on a stall (`play_stall_seconds`, mpv's position read over IPC),
  never on the wall clock while the clip advances; budgets stretch with the
  load per core (`load_timeout_scale_cap`); a timed-out line is queued
  again whole, its WAV kept for a cut-off play, after a wait for the load
  to fall (`timeout_retries`, `retry_load_per_core`, `retry_wait_seconds`),
  and only past its retries is it set aside. `tests/test_speech_under_load.py`
  (19 cases, one a short niced load test with the real `say` and mpv, silent).
- **Every line played is kept as a WAV and a sidecar, up to 20 GB
  (ADR-020).** His 2026-10-20 13:38 (the Master Desk #m1020-1339-68ee): "A
  history button shows everything said before. Only the last N WAVs are
  kept (N configurable), so the disk never fills." When the sink has played
  a line the daemon copies its WAV into `~/.local/state/auto-speech/history/`
  (`AUTO_SPEECH_HISTORY_DIR`) with a JSON sidecar: the text, when it was
  spoken, the sender (session and desk name), priority, duration and word
  timings, and, for a line from desk-say, its file name (`origin_file`, at
  Spoken Transcript's asking: `speak(..., origin_file=...)`). `say` gives no word timings, so they are estimated from word
  length and punctuation pauses and marked `source: "estimate"`. A line set
  aside by the meeting hold or the sound yield exports nothing until it
  plays. His 14:50: "save WAV files up to … the size of a small,
  medium-sized video game": the store is bounded by size, `max_gb`, the
  same name in `[history] max_gb` (`~/.config/auto-speech/history.toml`),
  `auto-speech history max-gb 20` and MCP `history(action="max-gb", gb=20)`,
  20 GB by default (about 21,000 lines at the measured mean clip); the
  oldest go first after every export. `keep` (200) is only how many of the
  newest lines the player's view shows; it deletes nothing. It is the one
  WAV store: Spoken Transcript reads it and never writes it.
  `auto-speech history [list|status|on|off|max-gb|keep|player-on|player-off]`
  and the MCP tool `history` list the lines newest first with their WAV
  paths (`--json` / `as_json` for the sidecars). A player launcher behind
  `[history] player` (default off) never opens during a hold and does
  nothing while the UI Utility is not installed; driving it by the
  utility's contract is the next step. `tests/run_all.sh` isolates the
  history dir and config. Tests: the store, the estimate, pruning, the
  shared verb, the launcher, the daemon's export only when heard, all
  silent.
- **No speech during a meeting (ADR-018).** His 2026-10-20 12:24: "if I'm
  in a meeting, I don't want you to talk." The sound-yield gate has a second
  blocker kind, *meeting*: a process on the `meeting_apps` list (Teams, Zoom,
  FaceTime, the browsers for Meet, Slack, Webex, Discord) whose Core Audio
  process object reports input running, read in the same property walk as
  the output flag (no audio captured, no permission, no prompt), or his own
  time-boxed `auto-speech yield meeting on` (60 min; `for 45m`, 8 h at most;
  `off`; `status`), also the MCP tool `sound_yield(action="meeting")` and
  `/auto-speech-meeting`. A meeting never waits: each line goes to the
  missed list at once with reason `meeting`, without synthesis, and when the
  meeting ends the daemon speaks one line, "You missed N lines during the
  meeting." A process holding the microphone that is not a meeting app (his
  CrownTalk, the speech daemon) never holds. `[sound_yield]` keys
  `meeting_hold`, `meeting_apps`, `meeting_summary`; flag file
  `~/.local/state/auto-speech/meeting.json` (`AUTO_SPEECH_MEETING_FILE`);
  `yield status` / `yield check` say the meeting state; the missed list
  counts `meeting` lines. Tests: the gate against a fake probe, table and
  clock, the daemon's set-aside and summary, the CLI and MCP verbs, all
  silent.

### Changed
- **Chrome's speech holds auto-speech, like Spotify (ADR-017 amendment).**
  His 2026-10-21 18:54: "dont play auto speech when im using it" (Chrome's
  read-aloud). Chrome and its helpers are now always-hold apps: while they
  play, speech holds; a line held past the longest wait goes to the missed
  list for the Spoken Transcript app's `missed --play`. The exception and
  players lists no longer apply to Chrome; his config is unchanged.
  `yield status` lists the always-hold apps and `yield check` says why
  Chrome holds (#m1021-1854-f2a9).
- **No private paths in the published tree.** The agents' teamwork logs
  (`.agents/teamwork/`, all of it, the 139 files once published included)
  and their working files `PROJECT.md` and `TEST_READY.md` are no longer
  tracked and are ignored; earlier history keeps them. The speaker names' default Agents
  folder is now `~/Desk/Agents`; point `agents_dir` in `speaker.toml`,
  `AUTO_SPEECH_AGENTS_DIR` or `DESK_DIR` at the desk's folder.
- **The test suite never plays sound.** His 2026-10-20 10:40: "turn off the
  speech tests". `NativeAudioSink` takes an mpv audio output (`--ao`) from
  `AUTO_SPEECH_MPV_AO` or its new `audio_output` argument; `tests/run_all.sh`
  exports `AUTO_SPEECH_MPV_AO=null`, and every test that can reach mpv calls
  `tests/quiet_audio.ensure_quiet()` at import (a `tests/conftest.py` does the
  same for a bare `pytest`), so no test run opens the speakers. Real audio in
  a test needs the explicit opt-in `AUTO_SPEECH_TEST_REAL_AUDIO=1`; the
  consumer test's `--listen` is ignored without it. `tests/test_quiet_tests.py`
  covers the option, the helper, the runner's export and the wiring.
- **The MCP server tests read a private HOME.** `tests/test_mcp_server.py`
  builds servers without `home=`, which read this machine's global mute flag
  (`~/.claude/auto-speech.disabled`), enrollment and scope; the suite failed
  at 10:47 on 2026-10-20 while his global mute was on. The test file now sets
  HOME to a temporary directory at import.

### Fixed
- **A line played four times under heavy load (ADR-025).** On 2026-10-22 a
  pegged Mac took 2.5 minutes to synthesize one line. desk-speaker and the
  daemon client's own retry sent it again, and every copy played. Copies of
  one id are now acknowledged, not queued. The client's retries carry one id.
- **A stop stops; a restart drains first; only live sessions are heard
  (ADR-024).** His 2026-10-21 23:38: "you just interrupted yourself during the
  10 m thing. bug. fix." / "i think im hearing things from other
  conversations". A stop now holds every later line, from any caller, on the
  missed list until a play or resume (`stopped` in the status reply and
  `narrate status`; kept across a restart). On SIGTERM the line in flight
  finishes and the queue goes on the missed list. `auto-speech narrate
  restart` waits for the drain (and desk-speaker's pending) before
  `launchctl kickstart`, and refuses on timeout. Enrolment markers whose
  session is not alive (no transcript written in 24 h, marker older than
  that) are ignored and listed by `doctor`.
- **The speech control lists every missed and held line.** The newest-50 cap
  was cut after the missed lines were merged in, so when the 50 newest heard
  lines were all newer than the missed ones (live on 2026-10-21: 50 lines
  from 19:13, 20 missed from 17:11), the control showed none unplayed. Only
  the history is capped now; every missed or held line is listed, by time.
- **The missed summary is spoken once per meeting, with its own count.**
  "You missed 4 lines during the meeting" was said again at every daemon
  start (ten times on 2026-10-21 after an 11:00 meeting): the cutoff for
  what a summary had counted lived in memory. Each meeting line now carries
  `announced` in `missed.json`, set in the same locked write that takes it
  for a summary, so a line is counted once, ever, and the next meeting is
  counted alone. Missed lines older than a day leave the list by
  themselves (held lines never do); the daemon logs how many at start
  (#m1021-1615-4497).
- **A desk-say line's sidecar names its file and its session.** None of the
  113 live sidecars carried `origin_file` or `sender.session_id`, which
  Spoken Transcript threads on (its ADR-003, ADR-006): desk-speaker
  (the desk repo) called `speak(text, priority)` alone. desk-speaker now records
  the writing session when desk-say runs, and sends both fields (the desk repo
  c89779488). auto-speech already carried them from the socket to the
  sidecar; `test_a_desk_say_request_reaches_the_sidecar_with_its_file_and_session`
  now holds that whole path. With a session, `sender.name` is filled too.
- **The history's text is the line as heard, without `say` markup.** The
  desk's lines carry `say` commands (`[[rate 180]] … [[slnc 600]] …`), which
  went into the sidecar's `text` and were counted as words in the timing
  estimate. The sidecar now keeps the text with every `[[…]]` command removed
  (`strip_say_markup`; text without one is kept exactly), and the estimate
  drops them as words and places each `[[slnc N]]` as N ms of silence where
  it stands (ignored if the silences would fill the clip).
- **A slow mpv no longer ends a line.** 2026-10-21 06:14:50Z, under load:
  `speak error: TimeoutError('timed out')`. The stall watch's progress read
  (ADR-019) got no reply from mpv in 5 s, and `MpvIpc.send` let the bare
  socket timeout out, past the probe's `MpvIpcError` catch, so the line
  failed. Now every failure in `MpvIpc.send` (a timeout, a socket error,
  bytes that are not UTF-8) is an `MpvIpcError`; a missed read counts as no
  progress for the stall window and never ends the line on its own; each
  progress read waits at most 1 s (`timeout=` on `send`), so a slow reply
  no longer holds the wait loop for up to 10 s. A failed connect closes its
  socket. `tests/test_mpv_ipc.py`.

- **Each line no longer lists the whole system temp dir.** The narrator
  synthesized into the system temp dir and, after every line, globbed it
  for fragments: 56,785 entries on 2026-10-20, seconds per line under load
  (and the cause of `test_playback_cannot_hang`'s timing failures under
  load). Lines are now synthesized in `auto-speech-synth/` under the state
  dir, which holds only the line in hand.

- **A test daemon took the live narrator's launchd label, and the narrator
  could not find mpv** (the Master Desk #m1020-2230-a808, his 22:28: "i
  haven't heard anything yet"). From 2026-10-21 03:02Z every line ended in
  `MpvNotInstalledError`: a niced suite run (desk-heavy) started its daemon
  through the ADR-019 launchd path under `dev.auto-speech.narrator`, which
  removed his narrator, and launchd gave the test daemon a bare PATH and none
  of the test's `AUTO_SPEECH_*` settings, so it took the live socket. Now:
  mpv is found on PATH, then at `/opt/homebrew/bin/mpv` or
  `/usr/local/bin/mpv`; a daemon on a private state dir never goes through
  launchd unless it names a label of its own, and `tests/run_all.sh` sets
  `AUTO_SPEECH_START_VIA_LAUNCHD=never`; a launchd job gets PATH with
  Homebrew's bin and every `AUTO_SPEECH_*` setting through `/usr/bin/env`.
  The nice value is read with `getpriority`: at nice 20 macOS's `nice(0)`
  fails, which the start script read as 0 (not niced) and the daemon as
  nothing to restore. Tests: `test_narrator_start_label.py`, the mpv
  fallback in `test_native_audio_sink.py`.
- **Playback cannot hang on one line.** On 2026-10-19 the narrator sat in
  one synthesis from 09:37 to 14:49 (the log's last line was that line's
  `speak:`), the queue stood at 47/32 dropping the oldest, and the doctor
  still said HEALTHY. Now every synthesis runs under a budget sized to the
  line (`synthesis_timeout_factor`, default 3× its spoken length, floor
  30 s) and every play under the clip's length plus
  `play_timeout_margin_seconds` (default 15): a line past its budget is
  set aside on the missed list as "timed out" and the next plays. A stuck
  synthesis worker is abandoned and a fresh one serves the next call
  (`TTSExecutor.submit(timeout=)`, `SynthesisTimeout`). The worker stamps
  each step (`taken`, `synthesizing`, `playing`, `done`, `idle`, with the
  step's budget) to `/tmp/auto-speech-narration-progress`, and the doctor's
  new `progress` check WARNs "stalled" when the queue is non-empty and no
  step has moved past its budget plus 120 s: a reading, not a gate. Tests:
  `tests/test_playback_cannot_hang.py` (8), five new in `test_doctor.py`.
- **auto-speech's mpv no longer claims the media keys.** mpv defaults to
  `--input-media-keys=yes`, so while a line played it could take the macOS
  play/pause key, and an AirPods Digital Crown press meant for Crown Talk's
  push-to-talk paused the speech instead. The sink now starts mpv with
  `--input-media-keys=no` (`native_audio_sink.py`; test
  `test_mpv_never_takes_the_media_keys`).
- **The doctor no longer warns on `[autoplay] bypass_llm`.** The key is
  read by `autoplay_config` (as is its older name `bypass_rewrite`), but
  the config validator did not list it, so a valid `autoplay.toml` showed
  "unknown key bypass_llm (ignored)". Both names are now known booleans
  (`config_validation.py`).

## [0.6.0] - 2026-10-18

Sound holds speech only when a player app makes it. Baseline tag `v0.5.0`;
commits `3632f1a` (tests first), `44161b4` (gate, CLI, MCP, config),
403bd3b (tests, version, docs) plus merge 1d48c78, landed on `main`
by a `--no-ff` merge of `feat/players-list`; `tests/run_all.sh`: 55
test files ran, 0 failed, under the isolated runner. Release notes:
[docs/releases/v0.6.0.md](docs/releases/v0.6.0.md).

### Changed
- **An unknown app's open stream never holds speech** (ADR-017). Core
  Audio's running-output flag is true for a stream that carries silence as
  much as for music, and on 2026-10-18 an editor's helper with a silent
  open stream held every line for an afternoon (fourteen lines to the
  missed list, none heard). Now a running app holds speech only when it is
  on the **players list**: Music, TV, Podcasts, QuickTime Player, Safari
  and its WebKit GPU process, FaceTime, Spotify, VLC, IINA, mpv, afplay,
  ffplay, Chrome, Arc, Edge, Firefox and its media process, Brave, Zoom,
  Teams, the ChatGPT app, the Claude app, plus whatever is added. A bundle
  id covers its helpers (`com.google.Chrome` covers
  `com.google.Chrome.helper`); the exception list still subtracts; the
  device fallback still holds for anything and says the lists cannot
  apply there. The output level was not used: reading it needs a Core
  Audio process tap, which captures audio and asks for the System Audio
  Recording permission, both outside the standing limits.

### Added
- `auto-speech yield hold-for players|any`, `player-add <apps…>`,
  `player-remove <apps…>` (`Chrome` removes `com.google.Chrome`),
  `player-reset`; MCP `sound_yield` with the same actions; `[sound_yield]
  hold_for` (`players`, the default, or `any`, the 0.3.0–0.5.0 rule) and
  `players` (the full list; absent means the built-in defaults). `yield
  status` shows the rule and the list; `yield check` names every running
  app with the reason it does or does not hold ("not on the players
  list"). Config validation knows both keys (`config_validation.py`).

## [0.5.0] - 2026-10-18

The desk is named only when the speaker changes. Baseline tag `v0.4.0`;
commits `e0973ff` (tests first), `b4658a0` (speaker module and daemon),
`23218d8` (CLI, MCP, config), `787ace9` (runner), `5cb95ad` (release notes) plus merge `6be53c2`, landed on
`main` by a `--no-ff` merge of `feat/speaker-change`; `tests/run_all.sh`:
55 test files ran, 0 failed, under the isolated runner with the caller's
Claude Code session cleared. Release notes:
[docs/releases/v0.5.0.md](docs/releases/v0.5.0.md).

### Added
- **Name the desk only when the speaker changes** (ADR-016). A line opens
  with "<desk> here," only when the session speaking differs from the last
  one heard, or when the last line was heard more than the quiet gap ago
  (15 minutes by default). Names come from the desk agents' notes
  (`session:` → `name:`, else the note's file name); a personal note is
  never read into the map; `[speaker.names]` names a session by hand and
  wins; a session no note names is announced as "Another desk". A line
  that already opens with its own name is not doubled. Only a line that
  was heard moves the speaker: a line held and set aside (ADR-015) does
  not. Cached audio (a cache hit, a `play_cache` replay) gets the name as
  a short "<name> here." clip, synthesized once per name under the state
  dir, played just before it. `auto-speech speaker status | on | off |
  gap <seconds> | name <session> <name> | forget <session> | who
  [session]`, MCP tool `speaker` (same actions), slash command
  `/auto-speech-speaker` (extras), `~/.config/auto-speech/speaker.toml`
  (`config/speaker.toml.example`), re-read when it changes. The daemon
  `status` reply carries `speaker` (`speaker.py`).
- **Every speak path carries its session.** The CLI `speak` and the
  standalone `speak.py` default `--session-id` from the shell's
  `CLAUDE_CODE_SESSION_ID`; the MCP `speak` tool takes an optional
  `session_id` and otherwise uses the session that launched the server;
  `playback_control` replays carry it; narration lines carry the session
  of their hook events.
- `config_validation` knows `[speaker]` (`names` is a table of string =
  string); `auto-speech doctor` reports its problems.

### Fixed

- A JSON line on the daemon socket whose `command` key names a known control
  (`status`, `speak`, `play_cache`, `interrupt`) is answered the same as
  `action`. Before, `{"command":"status"}` fell through to the legacy text
  wire and was spoken aloud (2026-10-18 17:51). `unix_ipc_server.control_payload()`
  reads either key; `tests/test_ipc_command_key.py` replays the line through a
  real server on a private socket.

## [0.4.0] - 2026-10-18

Nothing held is ever dropped: a missed list you can read, replay and clear
from the CLI and the MCP. Baseline tag `v0.3.0`; commits `2617431` (feature) plus this release-notes commit
landed on `main` by a `--no-ff` merge of `worktree/missed-list`;
`tests/run_all.sh`: 53 test files ran, 0 failed, under the isolated
runner with the caller's Claude Code session cleared. Release notes:
[docs/releases/v0.4.0.md](docs/releases/v0.4.0.md).

### Added
- **The missed list** (ADR-015, amended). A line held for another app's
  sound goes on the list as *held* the moment the hold starts and leaves
  it only once it has played. It stays as *missed* when the longest wait
  passes, when it turns out to be stale backlog once the sound stops, when
  a user interrupt releases its hold, or when the daemon restarted while
  it was held. `auto-speech missed` lists held and missed lines oldest
  first with when, why and the text; `missed --play` replays the missed
  ones in that order over any other sound, through the daemon, and each
  leaves the list once it has played; `missed --clear` forgets them. MCP
  tool `missed(action = list | play | clear)` prints the same text; slash
  command `/auto-speech-missed` (extras). One file:
  `~/.local/state/auto-speech/missed.json` (`AUTO_SPEECH_MISSED_FILE`
  overrides it; the test runner isolates it). The daemon `status` reply
  carries `missed` counts.
- `NativeAudioSink.play(hold=False)` plays one file over other sound (a
  replay); `already_waited` and `on_hold` carry the queue age and the hold
  callback to the gate; `last_hold` exposes the outcome.
  `DaemonClient.speak`/`play_cache` take `missed_id` and `hold`.

### Changed
- **Past the longest wait a held line is set aside, never dropped**
  (`EXPIRED` replaces `expired_drop`/`expired_play`). The longest wait
  (`max_wait_seconds`, 180 s) is now counted from when the line was asked
  for, so a line that waited behind an earlier hold is held only for the
  remainder; and once the sound stops, lines that sat behind a hold and
  are older than it go to the missed list instead of playing late, line
  by line. A line that is merely old because the queue was busy still
  plays.
- `yield status` and `yield check` say "then set aside as missed" where
  they said "then drop it".

### Removed
- `after_max_wait` (`drop` | `play`) from `[sound_yield]`: the loader
  names it in a warning and ignores it; `yield on/off/add` rewrite the
  file without it.

## [0.3.0] - 2026-10-18

Speech yields to other apps' sound, and the CLI and MCP surfaces reach
parity. Baseline tag `v0.2.0`; commits `e91a3c4..e91ea72 (plus this release-notes commit)` landed on `main`
by a `--no-ff` merge of `worktree/sound-yield`; `tests/run_all.sh`: 52 test files ran, 0 failed (630 counted unit tests plus the shell suites), under the isolated runner; live check 2026-10-18 16:06 (held 7.1 s behind an afplay tone, then spoke). Release notes:
[docs/releases/v0.3.0.md](docs/releases/v0.3.0.md).

### Added
- **Speech holds while another app plays sound** (ADR-015). By default a
  playback does not start while music, a video or a call is coming out of
  the speakers; it keeps its turn in the queue and plays when the sound
  stops. Past the longest hold (180 s) it is dropped and logged
  (`after_max_wait = "play"` plays it anyway). Detection reads Core
  Audio's process list and each process's running-output flag through
  ctypes, with the default output device's running-somewhere as fallback:
  no permission, no audio captured. auto-speech's own playback (its pid,
  its mpv, anything with `auto-speech` on its command line) never counts.
  `interrupt()` releases a hold like it stops a playback.
- **Exception list and off switch, the same in three places.**
  `auto-speech yield status | on | off | add <apps…> | remove <apps…> |
  clear | check` (alias `sound-yield`), the MCP tool `sound_yield(action,
  apps)` and `~/.config/auto-speech/sound-yield.toml` (`[sound_yield]`:
  `enabled`, `exceptions`, `max_wait_seconds`, `after_max_wait`,
  `poll_seconds`) share one action runner and one file. The daemon
  re-reads the file on every hold poll, so `yield off` or `yield add`
  releases a hold already in progress. `/auto-speech-yield` slash command
  (extras). `/auto-speech-doctor` validates the file. The daemon `status`
  reply reports `sound_yield.holding` and the last hold.
- `FEATURES.md` (feature catalog) and `docs/releases/` (per-version
  release notes with an index).
- **Unified `auto-speech` CLI dispatcher** (`bin/auto-speech`): speak,
  replay, pause, resume, restart, stop, seek, autoplay, narrate, yield,
  hook, doctor, update.
- **Expanded MCP server**: `speak`, `autoplay_turn`, `narrate_event`,
  `playback_control`, `session_configure`, `sound_yield`, `doctor`.
- **`speak` MCP server.** A stdio MCP server
  (`plugin/scripts/shell/run_mcp.sh`) with one tool, `speak(text)`, that
  says the words it gets aloud, verbatim. It returns as soon as the words
  are queued. A detached worker waits in the cross-session playback FIFO,
  honors the global mute, and never cuts off another playback. Register
  it with `bash setup/install-mcp.sh`.

### Fixed

- Leftover 0.1.0 opt-OUT copy on current surfaces now matches 0.2.0
  opt-IN: `/auto-speech-autoplay-status`, `/auto-speech-doctor`,
  `/auto-speech-scope`, and `docs/OPERATIONS.md`. Doctor reports `jq`.

### Fixed
- **Tests and development daemons can no longer reach the live speech
  service.** Every runtime path (`/tmp/auto-speech-daemon.sock`, the pid
  files, logs, markers, the mpv pid) now resolves through
  `AUTO_SPEECH_TMP_ROOT` (`state_paths.py` in Python, the same variable in
  the shell scripts; default `/tmp`, so the live daemon and hooks are
  unchanged). `tests/run_all.sh` exports a private per-run directory and
  socket, prints them, and stops any daemon a test started there.
  `tests/test_state_paths.py` proves it. Before this, a suite run could
  auto-start a worktree daemon on the live socket and send it an
  interrupt, cutting live speech.

### Changed
- `NativeAudioSink` takes a `sound_yield` gate (default on) and raises
  `PlaybackYielded` when a held playback is dropped; tests that drive a
  real sink against a mocked mpv rebind `DEFAULT_SOUND_YIELD_FACTORY`.
- Version strings: `auto-speech --version` and the MCP `serverInfo` report
  0.3.0.

## [0.2.0] - 2026-08-12

Autoplay actually works now — and it no longer speaks unless asked.

### Changed

- **BREAKING — autoplay is opt-IN per session.** It previously played in
  every session by default, with a marker to silence one. Enable a session
  with `/auto-speech-autoplay-on`; every other session stays silent.
  Enrollment markers live in a NEW directory
  (`~/.claude/auto-speech-autoplay-enabled/<session_id>`); the old
  `auto-speech-autoplay-sessions/` held opt-OUT markers and is never read
  as enrollment, so sessions that had asked for silence are not flipped on.
  `/auto-speech-scope solo` now enrolls the soloed session.
- A session whose id cannot be resolved (e.g. `jq` missing) no longer
  plays. Under the opt-out default it was deliberately let through to
  avoid total silence; under opt-in the safe direction is the opposite.
  `/auto-speech-doctor` reports `jq`.

### Fixed

- **Autoplay never played at all.** The Stop hook captured the beacon
  mtime with `stat -f %m` (whole seconds) and the worker compared it
  against Python's float `st_mtime`, so every worker read its own beacon
  as newer, declared itself superseded, and bailed during the coalesce
  window — silently, with no error surfaced. Captured at full precision,
  with a tolerance for float round-trip.
- **Every web playback after the first returned HTTP 500.** The web server
  holds one `MpvController` for the process lifetime and never calls
  `stop()`, leaving its FSM at `READY`; the next `start()` raised
  `IllegalTransition`, uncaught in both the speak and replay handlers.
- **A single bad phrase silenced a whole message on the CLI path.** The
  retry-split recovery for mlx-audio's content-dependent generate fault
  existed only in the web server, so `/auto-speech-speak` exited 5 with no
  audio. Extracted into shared `SpanSplitter` + `ResilientSynthesizer`
  collaborators used by every path; a chunk with nothing speakable now
  degrades to a gap instead of failing the message.
- The narrator dropped events that straddled a poll boundary: the tail
  advanced its offset past a partially-written line, losing both the
  fragment and its remainder. It now consumes only complete lines.
- A malformed `AUTO_SPEECH_AUTOPLAY_COALESCE` /
  `AUTO_SPEECH_NARRATION_WAIT_MAX` killed the autoplay worker with a
  traceback and no log line, violating its always-exit-0 contract.
- Type-confused JSON (`{"text": 123}`, non-string `voice`/`hash`/`target`,
  non-numeric `rewrite_timeout_s`) returned 500 instead of 400 across
  `/api/speak`, `/api/synthesize`, `/api/replay` and `/api/seek`.

### Added

- `SessionEnrollment` — testable model + writer for autoplay opt-in,
  mirroring `SoloScope`'s shape.
- Regression tests for each fix above, including the hook→worker mtime
  handoff the prior suite bypassed entirely, and the three-state hook gate
  (unenrolled bails / enrolled proceeds / global mute overrides).

## [0.1.0] - 2026-07-22

First public release.

### Added

- Local Kokoro TTS on Apple Silicon (mlx-audio) with Fibonacci-scaled
  buffered playback via mpv — sub-second time-to-first-audio on long texts —
  and a source-hash replay cache.
- End-of-turn autoplay via a Claude Code Stop hook: `verbatim` / `small` /
  `medium` / `large` modes, on by default with per-session opt-out, all/solo
  session scope, cross-session FIFO queue with coalescing and dedup.
- Optional real-time narrator subsystem: hook event stream → phase
  classifier → local MLX LLM → spoken one-liners while Claude works.
- Localhost web app (`127.0.0.1:7860`): paste-to-speak with Claude-CLI
  rewrite, playback transport, replay-cache browser, `/api/synthesize` +
  `/api/voices` for the browser extension, multi-voice with per-voice
  language codes, resilient chunk-splitting around an mlx-audio
  long-input bug.
- Chrome extension "AutoSpeech" (v0.4.0, independently versioned):
  right-click speak-selection backed by the local Kokoro server, browser-
  voice fallback, live voice picker, minimal host permissions.
- 8 curated Claude Code slash commands (speak, replay, app, autoplay-on/
  off/mode, scope, doctor) + 13 extra commands installable with
  `setup/install-plugin.sh --with-extras`. Commands resolve the clone
  location at runtime — no hardcoded paths.
- `setup/` scripts: locked-dependency install (uv), audible verify,
  plugin/hook installers with uninstall counterparts, optional
  SessionStart self-update bootstrap (lockfile-driven `uv sync`, never a
  network pull).
- `/auto-speech-doctor` health check (binaries, disk, logs, daemon,
  queue, scope; `--json` mode).
- CI on macOS: ruff, shellcheck, lockfile drift, hermetic test subset on
  a bare Python 3.12, and a Flask+numpy web-test lane.

### Security

- Web server binds loopback only and reflects CORS solely for
  Chrome-extension origins — arbitrary web pages cannot drive the API.
- No telemetry; all synthesis and narration run locally.

[Unreleased]: https://github.com/JoshuaRamirez/auto-speech/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/JoshuaRamirez/auto-speech/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/JoshuaRamirez/auto-speech/releases/tag/v0.1.0
