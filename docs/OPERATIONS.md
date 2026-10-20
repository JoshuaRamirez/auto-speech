# Operations

How to run auto-speech unattended, watch its health, and recover when
something goes wrong. auto-speech targets macOS / Apple Silicon (MLX,
Homebrew `mpv`, BSD `stat`).

## Health at a glance

```
/auto-speech-doctor          # human-readable
/auto-speech-doctor json     # machine-readable; exits non-zero if unhealthy
```

`doctor` is the one command that answers "is it working?". It probes, and
classifies each as **OK / WARN / FAIL** (any FAIL ⇒ non-zero exit):

| Check | FAIL when | WARN when |
|-------|-----------|-----------|
| `mpv` | binary missing (no playback) | — |
| `uv` | — | missing (can't install/update) |
| `jq` | — | missing (Stop hook cannot parse session id from the payload; autoplay stays silent) |
| `venv` | interpreter absent | — |
| `disk` | `/tmp` free < 50 MiB | < 200 MiB |
| `logs` | — | a log exceeds the rotation cap (rotation lagging) |
| `narrator` | — | stale/recycled daemon pid (reclaimed next start) |
| `queue` | — | narration queue saturated (dropping oldest) |
| `progress` | — | stalled: the queue is non-empty and the worker has not moved past its step's budget plus 120 s (a reading, not a gate; `auto-speech narrate stop` restarts it) |
| `config` | — | a user config has an unknown key / bad type / bad enum |
| `updates` | — | venv out of sync with `uv.lock` (run `bash setup/bootstrap.sh --force`) |
| `scope`, `autoplay` | — | informational (opt-IN, ALL/SOLO, global mute) |

A WARN means "degraded but running"; only a FAIL is "broken".

## Running unattended

Autoplay is opt-IN: nothing plays until a session enrolls with
`/auto-speech-autoplay-on`. The narrator daemon idle-exits between
bursts of work. The properties that make multi-day unattended
operation safe:

- **Logs are size-capped** (default 5 MiB, 3 backups). They cannot fill
  `/tmp`. Tune with `AUTO_SPEECH_LOG_MAX_BYTES` / `AUTO_SPEECH_LOG_BACKUPS`.
- **The narration queue is bounded** (`max_queue_depth`, default 32). A burst
  of phases sheds the oldest rather than growing memory; drops are logged.
- **Daemon restarts are crash- and PID-reuse-safe.** A dead or recycled pid
  is reclaimed on the next start, and the stop path never signals a process
  that isn't our daemon.
- **The venv self-reconciles** to the committed lock on SessionStart (see
  "Staying current").

## Log locations

| File | Written by | Rotated |
|------|------------|---------|
| `/tmp/auto-speech-narrator-daemon.log` | narrator daemon (structured) | in-process (RotatingFileHandler) |
| `/tmp/auto-speech-narrator-daemon.out` | narrator daemon (raw stdio/tracebacks) | pre-spawn |
| `/tmp/auto-speech-autoplay.log` | autoplay worker | pre-spawn (per turn) |
| `/tmp/auto-speech-webapp.log` | web app (start_webapp.sh) | pre-spawn |
| `/tmp/auto-speech-sync.log` | self-update bootstrap | append (low volume) |

## Staying current (self-update)

The committed `uv.lock` pins every dependency. To keep a machine's venv in
sync with it:

- **Automatic:** install the SessionStart hook once —
  `bash setup/install-bootstrap-hook.sh`. Each new session runs
  `setup/bootstrap.sh`, which is a **no-op unless `uv.lock` changed**; when it
  changed it runs `uv sync` once (single-flight, backgrounded, non-destructive
  — it keeps the `narrate` extra if mlx-lm is installed) and stamps the new
  hash. It **never** runs `git pull`.
- **Manual:** `bash setup/bootstrap.sh --force` (or `/auto-speech-update`
  if you installed the extras) reconciles the venv synchronously. Pull
  source changes yourself first if you want them; this only updates
  dependencies.

`doctor`'s `updates` check warns when the venv is out of sync.

## Configuration & env-var precedence

User config lives in `~/.config/auto-speech/{autoplay,narrator,sound-yield,speaker}.toml`
(copy from the `*.toml.example` files). A typo never crashes the tool — it
falls back to defaults — but `/auto-speech-doctor` surfaces it as a WARN.

Autoplay resolves each value highest-precedence first:

| Value | Env var | then | default |
|-------|---------|------|---------|
| coalesce window | `AUTO_SPEECH_AUTOPLAY_COALESCE` | `autoplay.toml` | 1.0 s |
| narration wait | `AUTO_SPEECH_NARRATION_WAIT_MAX` | `autoplay.toml` | 90 s |
| queue wait | `AUTO_SPEECH_QUEUE_WAIT_MAX` | — | 600 s |
| min length | `AUTO_SPEECH_AUTOPLAY_MIN_LEN` | — | 20 chars |

Config file search order (first found wins): `$AUTO_SPEECH_AUTOPLAY_CONFIG`
/ `$AUTO_SPEECH_NARRATOR_CONFIG` → `~/.config/auto-speech/*.toml` → the shipped
`config/*.toml.example`.

Sound yield (`[sound_yield]` in `sound-yield.toml`, or
`$AUTO_SPEECH_SOUND_YIELD_CONFIG`) has no env-var overrides; `auto-speech
yield …` and the MCP tool `sound_yield` write the same file, and the daemon
re-reads it on every hold poll, so no restart is needed. Keys: `enabled`
(true), `hold_for` (`players`; `any` for the pre-0.6.0 rule), `players` (the
built-in list, ADR-017), `exceptions` ([]), `max_wait_seconds` (180),
`poll_seconds` (0.5).
`after_max_wait` (v0.3.0) is gone: nothing held is dropped any more.

The missed list, the lines held or set aside while another app played
sound, is one JSON file: `~/.local/state/auto-speech/missed.json`
(`$AUTO_SPEECH_MISSED_FILE` overrides it; `tests/run_all.sh` points it into
the isolated state directory). The daemon writes it; `auto-speech missed`
reads it, `missed --play` replays through the daemon, `missed --clear`
empties it under the same file lock.

## Runtime state and isolation

Every runtime file lives in one directory: the daemon socket
`auto-speech-daemon.sock`, the daemon pid, log and watermark, the narrator
events file, the mpv pid, the autoplay beacon and now-playing markers, the
say-worker log. The directory is `AUTO_SPEECH_TMP_ROOT`, default `/tmp`.
The live daemon and the hooks never set it. `tests/run_all.sh` sets it to a
private per-run directory (and `AUTO_SPEECH_DAEMON_SOCK` inside it), prints
both, and stops any daemon a test started there, so a test run cannot
reach the live service. A development checkout that must run its own
daemon sets the same variable before `narrator_service_start.sh`.

No test plays sound. `tests/run_all.sh` exports `AUTO_SPEECH_MPV_AO=null`, so
mpv plays to its null output, and every test that can reach mpv sets the same
at import (`tests/quiet_audio.py`; `tests/conftest.py` for a bare `pytest`).
The variable is the sink's: a daemon started with `AUTO_SPEECH_MPV_AO=null`
is silent too, and unset it leaves mpv's default output. A test that must be
heard runs only with the explicit opt-in `AUTO_SPEECH_TEST_REAL_AUDIO=1`.

## Muting & scoping

- **One session only:** `/auto-speech-scope solo` makes only the current
  session read aloud; `/auto-speech-scope all` restores ALL scope (every
  enrolled session).
- **This session on:** `/auto-speech-autoplay-on` — autoplay is opt-IN, so
  nothing plays until a session enrolls.
- **This session off:** `/auto-speech-autoplay-off` (withdraws enrollment).
- **Everything off:** `touch ~/.claude/auto-speech.disabled` (global mute);
  `/auto-speech-autoplay-on` clears it.
- **Another app is playing sound:** speech holds by default and plays when
  the sound stops (`auto-speech yield status`). `auto-speech yield add
  Music` lets that app's sound through; `auto-speech yield off` turns the
  hold off; `auto-speech yield check` says who is playing now. A hold and
  its outcome are logged in `/tmp/auto-speech-narrator-daemon.log`
  (`sound-yield: …`, `missed: …`), and the daemon's `status` reply
  (DaemonClient) carries `sound_yield.holding`, the last hold's outcome
  and `missed` counts (`held`, `missed`).
- **You listened for a long while:** nothing was dropped. `auto-speech
  missed` lists what you did not hear, oldest first; `missed --play`
  replays it in that order over any other sound; `missed --clear` forgets
  it. Lines still held play by themselves when the sound stops.

## Troubleshooting

| Symptom | Check | Likely fix |
|---------|-------|------------|
| No audio at all | `/auto-speech-doctor` | `mpv` FAIL → `brew install mpv`; or global mute marker present |
| One session silent | `/auto-speech-doctor` (scope + queue lines) | it never enrolled — run `/auto-speech-autoplay-on`; or SOLO is held by another session |
| Narration never speaks | `doctor` `narrator` + `/tmp/auto-speech-narrator-daemon.out` | provider downgraded to Mock (MLX load failed) — see the daemon log |
| Config change ignored | `doctor` `config` | a typo/bad type warned there; fix the TOML |
| Stale "already running" | — | reclaimed automatically on next start (PID-identity guard) |
| Deps differ across machines | `doctor` `updates` | `bash setup/bootstrap.sh --force` |
