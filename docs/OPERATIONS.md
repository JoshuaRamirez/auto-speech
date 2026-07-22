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
| `venv` | interpreter absent | — |
| `disk` | `/tmp` free < 50 MiB | < 200 MiB |
| `logs` | — | a log exceeds the rotation cap (rotation lagging) |
| `narrator` | — | stale/recycled daemon pid (reclaimed next start) |
| `queue` | — | narration queue saturated (dropping oldest) |
| `config` | — | a user config has an unknown key / bad type / bad enum |
| `updates` | — | venv out of sync with `uv.lock` (run `bash setup/bootstrap.sh --force`) |
| `scope`, `autoplay` | — | informational (ALL/SOLO, global mute) |

A WARN means "degraded but running"; only a FAIL is "broken".

## Running unattended

Nothing special is required — autoplay is on by default and the narrator
daemon idle-exits between bursts of work. The properties that make multi-day
unattended operation safe:

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

User config lives in `~/.config/auto-speech/{autoplay,narrator}.toml`
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

## Muting & scoping

- **One session only:** `/auto-speech-scope solo` makes only the current
  session read aloud; `/auto-speech-scope all` restores every session.
- **This session off:** `/auto-speech-autoplay-off` (per-session opt-out).
- **Everything off:** `touch ~/.claude/auto-speech.disabled` (global mute);
  `/auto-speech-autoplay-on` clears it.

## Troubleshooting

| Symptom | Check | Likely fix |
|---------|-------|------------|
| No audio at all | `/auto-speech-doctor` | `mpv` FAIL → `brew install mpv`; or global mute marker present |
| One session silent | `/auto-speech-doctor` (scope + queue lines) | a per-session opt-out marker, or SOLO is held by another session |
| Narration never speaks | `doctor` `narrator` + `/tmp/auto-speech-narrator-daemon.out` | provider downgraded to Mock (MLX load failed) — see the daemon log |
| Config change ignored | `doctor` `config` | a typo/bad type warned there; fix the TOML |
| Stale "already running" | — | reclaimed automatically on next start (PID-identity guard) |
| Deps differ across machines | `doctor` `updates` | `bash setup/bootstrap.sh --force` |
