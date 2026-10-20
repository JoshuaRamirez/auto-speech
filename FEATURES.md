# auto-speech — feature catalog

The authoritative inventory of what auto-speech does, by capability
domain, as of v0.5.0 (2026-10-18). Each entry names the surface and the
module that owns it. Release-by-release history is in
[CHANGELOG.md](CHANGELOG.md) and [docs/releases/](docs/releases/INDEX.md).

## Speech synthesis
- **Local Kokoro TTS on Apple Silicon** via mlx-audio; no cloud, no keys
  (`tts_engine.py`, `tts_executor.py`, ADR-001).
- **Fibonacci-scaled buffered playback**: sub-second time-to-first-audio on
  long text (`chunk_planner.py`, `fibonacci.py`, ADR-002).
- **Boundary snapping** of chunks to sentence edges (`boundary_snapper.py`,
  ADR-004).
- **Resilient synthesis**: a phrase that faults the engine is split and
  retried; an unspeakable chunk degrades to a gap, never fails the message
  (`span_splitter.py`, `resilient_synthesizer.py`).
- **Audio-friendly rewrite** through the user's own `claude` CLI: verbatim
  (lossless) or summary at small / medium / large (`claude_cli_rewriter.py`,
  `cli_rewrite.py`, ADR-005, ADR-011).
- **Apple `say` fallback engine** when the MLX engine is unavailable
  (`apple_say_engine.py`).
- **Voice profiles and calibration** (`voice_profile.py`,
  `voice_profile_store.py`, `calibrator.py`).

## Playback
- **Single mpv sink**, synchronous, interruptible, with a pid file for
  transport control (`native_audio_sink.py`, `mpv_ipc.py`, ADR-009).
- **Speech yields to other apps' sound** (v0.3.0, ADR-015): a playback does
  not start while another process is outputting audio; it keeps its turn
  and plays when the sound stops. Core Audio process list with
  running-output flag, device running-somewhere fallback; no permission;
  own playback never counts; `interrupt()` releases a hold
  (`sound_yield.py`).
- **Nothing held is ever dropped: the missed list** (v0.4.0, ADR-015):
  past the longest wait (180 s from the ask) a held line is set aside, not
  dropped; once the sound stops only lines younger than that play by
  themselves, older backlog is set aside too. `auto-speech missed` lists
  held and missed lines oldest first, `missed --play` replays the missed
  ones in that order over any other sound (each leaves the list once it
  has played), `missed --clear` forgets them; MCP tool `missed`, slash
  command `/auto-speech-missed`; file
  `~/.local/state/auto-speech/missed.json` (`missed_speech.py`).
- **No speech during a meeting** (ADR-018): a meeting app (Teams, Zoom,
  FaceTime, a browser on Meet, Slack, Webex, Discord) holding the
  microphone, read from the same Core Audio process walk (input-running
  flag; no capture, no permission), or his own `auto-speech yield meeting
  on|off|for 45m` (60 min by default, 8 h at most, expires by itself; MCP
  `sound_yield(action="meeting")`, `/auto-speech-meeting`), sets every line
  aside on the missed list at once with reason `meeting`, no synthesis, no
  stall; one spoken line after the meeting says how many were missed. An
  open microphone that is not a meeting app never holds. Keys
  `meeting_hold`, `meeting_apps`, `meeting_summary`; flag file
  `~/.local/state/auto-speech/meeting.json` (`sound_yield.py`,
  `narrator_service.py`).
- **Exception list and off switch** for the hold, identical in the CLI
  (`auto-speech yield`), the MCP tool (`sound_yield`) and
  `~/.config/auto-speech/sound-yield.toml`; live re-read, no restart.
- **Players list: sound holds speech only when a player app makes it**
  (v0.6.0, ADR-017). Core Audio says an app is running output, not whether
  that output is silent; an unknown app's open stream never holds. Built-in
  players (Music, TV, Podcasts, QuickTime, Safari, Spotify, VLC, IINA, mpv,
  afplay, the browsers, Zoom, FaceTime, Teams, the ChatGPT and Claude
  apps) plus his additions; a bundle id covers its helpers; `hold_for =
  any` restores the every-app rule. `yield hold-for | player-add |
  player-remove | player-reset`, MCP `sound_yield` same actions,
  `[sound_yield] hold_for` / `players`; `yield check` says why each running
  app does or does not hold.
- **Transport**: pause, resume, restart, seek (`+N`, `-N`, `N`, `end`), stop
  (`control.py`, CLI, MCP `playback_control`).
- **Replay cache**: content-addressed WAVs keyed by source hash; replay the
  n-th most recent (`cache_store.py`, `replay.py`, ADR-007, ADR-008).

## Who is talking
- **The desk is named only when the speaker changes** (v0.5.0, ADR-016):
  a line opens with "<desk> here," only when its session differs from the
  last one heard, or after a quiet gap (15 min default). Names come from
  the desk agents' notes (`session:` → `name:` or the note's file name;
  personal notes never read in; `[speaker.names]` wins); a session no note
  names is "Another desk"; a line that names itself is not doubled; a line
  set aside while held never moves the speaker; cached audio gets a
  "<name> here." clip in front. Every `speak` path carries its session
  (CLI and `speak.py` from `CLAUDE_CODE_SESSION_ID`, MCP from the call or
  its own environment, narration from its hook events). Same verbs in
  `auto-speech speaker`, the MCP tool `speaker`, slash command
  `/auto-speech-speaker` and `~/.config/auto-speech/speaker.toml`
  (`speaker.py`).

## Queueing and priority
- **Unified daemon** on a UNIX socket with a 4-tier priority arbiter:
  P1 user interrupt, P2 explicit MCP/CLI speak, P3 autoplay, P4 narration;
  P1 barge-in purges P3/P4; P4 sheds oldest when full
  (`narrator_service.py`, `priority_arbiter.py`, `unix_ipc_server.py`,
  `daemon_client.py`).
- **Cross-session FIFO** for autoplay workers: an active playback is never
  cut off by a newer one; burst coalescing; in-flight dedup
  (`playback_ticket.py`, `dedup_guard.py`, ADR-006).
- **Daemon lifecycle**: pid-reuse-safe start/stop scripts, idle shutdown,
  stale marker sweep, size-capped rotating logs (`narrator_state.py`,
  `auto_speech_log.py`).
- **One runtime state directory** (`AUTO_SPEECH_TMP_ROOT`, default `/tmp`)
  for the socket, pid files, logs and markers, so tests and development
  daemons run beside the live service without touching it
  (`state_paths.py`, v0.3.0).

## End-of-turn autoplay
- **Stop-hook autoplay**, off by default, per-session opt-in
  (`/auto-speech-autoplay-on|off`), global panic mute at
  `~/.claude/auto-speech.disabled` (`autoplay_hook.sh`,
  `autoplay_worker.py`, `autoplay_gate.py`, `autoplay_enrollment.py`,
  ADR-012).
- **Scope**: all enrolled sessions, or solo spotlight on one
  (`autoplay_scope.py`).
- **Mode**: raw, verbatim, or summary small / medium / large
  (`autoplay_config.py`, `~/.config/auto-speech/autoplay.toml`).
- **Transcript selection**: locate the session transcript, pick the n-th
  most recent assistant turn, skip cron ticks (`transcript_locator.py`,
  `message_selector.py`, ADR-003).

## Real-time narration (optional)
- **Phase classifier** over hook events (Explore / Edit / Run / Delegate)
  and one spoken sentence per phase transition from a local MLX or Ollama
  LLM, or a mock (`narrator_phase_classifier.py`,
  `narrator_mlx_summarizer.py`, `narrator_ollama_summarizer.py`).
- Per-session opt-in; autoplay waits for the narration queue to drain so
  the two never overlap (`narrator_hook.sh`, `/auto-speech-narrate-*`).

## Surfaces
- **CLI** `bin/auto-speech` (`cli.py`): `speak`, `replay`, `pause`,
  `resume`, `restart`, `stop`, `seek`, `autoplay {on,off,status,mode,scope}`,
  `narrate {on,off,status,stop,config}`, `yield {status,on,off,hold-for,player-add,player-remove,player-reset,add,remove,
  clear,check}`, `missed [--play|--clear]`, `hook {stop,narrator,bootstrap}`,
  `doctor`, `update`.
- **MCP server** (`mcp_server.py`, stdio JSON-RPC): `speak`,
  `autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`,
  `sound_yield`, `missed`, `doctor` (ADR-014 for the fire-and-forget shape).
- **Slash commands**: 8 curated (`plugin/commands/`) plus 17 extras
  (`plugin/commands-extra/`), every one prefixed `auto-speech-`.
- **Web app** at `http://127.0.0.1:7860` (loopback only): paste-to-speak,
  transport, replay browser, `/api/synthesize`, `/api/voices`, job status
  (`web_server.py`, `job_tracker.py`, ADR-010, ADR-013, ADR-014).
- **Chrome extension** AutoSpeech: speak selection via the local server,
  browser-voice fallback (`chrome-extension/`, versioned separately).

## Diagnostics and upkeep
- **Doctor** (`doctor.py`, `/auto-speech-doctor [json]`): binaries, venv,
  disk, logs, daemon, queue, progress (stalled: a non-empty queue with no
  step past its budget), scope, MCP registration, config validation
  (autoplay, narrator, sound-yield), venv-vs-lock drift.
- **Playback cannot hang, and is never cut off** (ADR-019): every synthesis
  runs under a budget sized to the line (`synthesis_timeout_factor` × its
  spoken length, floor 30 s), stretched by the load per core up to
  `load_timeout_scale_cap`; a play ends only after `play_stall_seconds` with
  no progress (mpv's position read over IPC), never on the wall clock while
  the clip advances. A line past its budget is queued again whole (its WAV
  kept for a cut-off play) after a wait for the load to fall
  (`retry_load_per_core`, `retry_wait_seconds`), up to `timeout_retries`;
  only then is it set aside on the missed list as "timed out". A stuck
  synthesis worker is abandoned for a fresh one (`tts_executor.py`). The
  worker stamps each step to `/tmp/auto-speech-narration-progress`.
- **Speech has priority under load** (ADR-019, `speech_priority.py`): the
  daemon clears any background clamp, runs its speech threads at
  user-interactive QoS and `say` and mpv under `taskpolicy -t 0 -l 0`
  (`AUTO_SPEECH_QOS_BOOST=0` turns that off); a niced start goes through
  `launchctl submit` so the daemon runs at nice 0.
- **Self-update**: SessionStart bootstrap hook runs `uv sync` when
  `uv.lock` changes (`self_update.py`).
- **Idempotent installers** for deps, plugin commands, hooks, MCP
  registration, and uninstallers for each (`setup/`).

## Tests
- `tests/run_all.sh`: full suite (python + shell + web + heavy),
  `--hermetic` (stdlib-only subset for CI) and `--web` lanes. The sound-yield
  gate and the sink hold are tested against a fake audio layer; tests that
  drive a real sink against a mocked mpv disable the gate.
- **Silent by default.** No test plays sound: `run_all.sh` exports
  `AUTO_SPEECH_MPV_AO=null` (mpv's null output) and every test that can
  reach mpv calls `tests/quiet_audio.ensure_quiet()` at import, so a bare
  `pytest` or `python tests/test_x.py` is silent too. Real audio in a test
  needs the opt-in `AUTO_SPEECH_TEST_REAL_AUDIO=1`.
