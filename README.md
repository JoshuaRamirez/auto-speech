# auto-speech

[![CI](https://github.com/JoshuaRamirez/auto-speech/actions/workflows/ci.yml/badge.svg)](https://github.com/JoshuaRamirez/auto-speech/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/JoshuaRamirez/auto-speech)](https://github.com/JoshuaRamirez/auto-speech/releases)

Speaks Claude Code responses aloud using a **local Kokoro TTS model on Apple
Silicon** — no cloud TTS, no API keys. Fibonacci-scaled buffered playback gives
sub-second time-to-first-audio even on long responses. Includes end-of-turn
autoplay, a localhost web app, a Chrome "speak selection" extension, and an
optional real-time narrator that describes what Claude is doing while it works.

## Requirements

- **Apple Silicon Mac** (the MLX wheels are macOS/arm64-only; the lockfile
  resolves for darwin exclusively)
- **Homebrew** (used to install `mpv` and `jq` if missing)
- **uv** (`brew install uv`) — manages the Python 3.12 venv
- Disk + network note: Kokoro-82M model weights download from Hugging Face
  on first synthesis (a few hundred MB, cached locally)

## Quickstart

```bash
git clone https://github.com/JoshuaRamirez/auto-speech && cd auto-speech
bash setup/install.sh          # 1. REQUIRED — venv + locked deps + mpv + jq + spaCy model
bash setup/verify.sh           # 2. REQUIRED — synthesizes and PLAYS a test WAV (you should hear audio)
bash setup/install-plugin.sh   # 3. REQUIRED — installs the 8 slash commands into ~/.claude/commands
bash setup/install-hook.sh     # 4. REQUIRED for autoplay — adds the Stop hook to ~/.claude/settings.json
```

Optional extras:

```bash
bash setup/install-bootstrap-hook.sh   # SessionStart self-update (uv sync when uv.lock changes)
bash setup/install-narrator-hooks.sh   # real-time narration hooks (see Narration)
bash setup/install-mcp.sh              # `speak` MCP tool: say any words aloud (see MCP server)
bash setup/install-plugin.sh --with-extras   # + 15 extra slash commands
```

Every script is idempotent — safe to re-run. Step 4 is what makes the
headline feature work: without the Stop hook, nothing plays at end of turn.

## Commands

The default install exposes 8 curated commands:

| Command | Does |
|---|---|
| `/auto-speech-speak [n]` | speak the n-th most recent assistant message (default 1) |
| `/auto-speech-replay [n]` | replay the n-th most recent cached entry |
| `/auto-speech-app` | launch the localhost web app |
| `/auto-speech-autoplay-on` | enable end-of-turn autoplay for THIS session (off by default) |
| `/auto-speech-autoplay-off` | turn THIS session back off |
| `/auto-speech-autoplay-mode` | show or set the autoplay mode (`verbatim\|small\|medium\|large`) |
| `/auto-speech-scope [all\|solo]` | read ALL sessions, or only THIS one |
| `/auto-speech-doctor [json]` | health check; exits non-zero when unhealthy |

15 more (playback transport, narrator controls, autoplay-status, yield, missed, update)
live in `plugin/commands-extra/` — install with
`bash setup/install-plugin.sh --with-extras`. Every command is prefixed
`auto-speech-` so nothing collides with Claude Code built-ins or other
plugins; new commands must follow the convention.

## Autoplay

The end-of-turn autoplay reads each completed assistant response aloud.
It is **off by default**. Gating is per-session **opt-in**:
`/auto-speech-autoplay-on` touches a marker file
(`~/.claude/auto-speech-autoplay-enabled/<session_id>`) enrolling just
that session; every session without a marker stays silent, including one
whose id cannot be determined. `/auto-speech-autoplay-off` withdraws the
enrollment. A global panic mute at `~/.claude/auto-speech.disabled` takes
precedence over everything.

Concurrent playbacks (multiple sessions, or rapid turns in one session)
are serialized through a strict cross-session FIFO queue: each pending
playback waits for the current one to finish, in arrival order. An active
playback is **never** cut off by a newer one; back-to-back turns within
the coalesce window collapse to the newest, and identical audio already
in flight is deduplicated.

The read shape is set via `/auto-speech-autoplay-mode` (or
`~/.config/auto-speech/autoplay.toml`, which also holds
`coalesce_seconds` and `narration_wait_max_seconds`):

| Mode | Length | When |
|---|---|---|
| `verbatim` | full content, lossless | when every fact matters |
| `small` | 1-3 sentence summary (default) | quick status |
| `medium` | 3-5 sentence summary | balanced |
| `large` | 6-10 sentence summary, preserves nuance | long technical replies |

## MCP server: `speak`

A stdio MCP server whose first tool, `speak(text)`, says the words it
gets aloud with the local Kokoro voice (the others: `autoplay_turn`,
`narrate_event`, `playback_control`, `session_configure`, `sound_yield`, `missed`,
`doctor`). It reads them exactly as
written: no transcript lookup, no LLM rewrite, no summary. So the caller
should write text the way it should sound.

```bash
bash setup/install-mcp.sh      # claude mcp add --scope user auto-speech
```

Installed as a managed plugin, the server is auto-discovered from
`plugin/.mcp.json` and needs no separate step; `/auto-speech-doctor`
reports whether it is registered. Other MCP clients can launch
`plugin/scripts/shell/run_mcp.sh` as a stdio server. The call returns as soon as the words are queued. A detached
worker then waits its turn in the same cross-session FIFO as autoplay,
so it never cuts off another playback, and speaks. Repeated calls are
spoken in order. The global mute (`~/.claude/auto-speech.disabled`)
silences it too. Text is capped at 20,000 characters, and worker logs go
to `/tmp/auto-speech-say.log`. Remove it with `bash setup/uninstall-mcp.sh`.

## Yielding to other apps' sound

By default auto-speech **does not start speaking while another app is
playing sound**: music, a video, a call. The speech keeps its place in the
queue and plays once the other sound stops. **Nothing held is ever
dropped.** If the sound goes on past the longest wait (180 s, counted from
when the line was asked for) the line is set aside on the **missed list**;
and when the sound stops, only lines younger than that still play by
themselves, so a long listen does not end in a stale backlog read out line
by line. Detection reads Core Audio's process list (which processes are
running output), needs no permission, and never captures audio.
auto-speech's own playback never counts.

```bash
auto-speech missed                       # what you have not heard, oldest first
auto-speech missed --play                # replay the missed lines in that order, over any other sound
auto-speech missed --clear               # forget them
```

Each replayed line leaves the list once it has played; lines still held
play by themselves when the sound stops. MCP: `missed(action="list" |
"play" | "clear")`, same text. The list is one file,
`~/.local/state/auto-speech/missed.json`.

An **exception list** names apps whose sound does not hold speech, by
bundle id or name. The switch and the list have the same verbs in the
CLI, the MCP tool and the config file:

```bash
auto-speech yield status                 # switch, exception list, longest hold
auto-speech yield add Music Spotify      # these apps' sound does not hold speech
auto-speech yield remove Spotify
auto-speech yield clear
auto-speech yield off                    # speech plays over other apps' sound
auto-speech yield on                     # default
auto-speech yield check                  # who is playing right now; would speech hold?
```

MCP: `sound_yield(action="add", apps=["com.apple.Music"])`, same actions.
Config: `~/.config/auto-speech/sound-yield.toml`, section `[sound_yield]`
with `enabled`, `exceptions`, `max_wait_seconds` and `poll_seconds`; see
`config/sound-yield.toml.example`. A change takes effect at the next
playback, or at the next poll of a hold already in progress; no restart.
The slash commands `/auto-speech-yield` and `/auto-speech-missed` (extras)
run the same CLI. The reasoning behind the 180 s wait, the missed list
and the replay order is in
[ADR-015](docs/decisions/ADR-015-sound-yield.md).

## Naming the desk only when the speaker changes

Several desks (Claude Code sessions) speak through one daemon. A line
opens with **"<desk> here,"** only when the session speaking differs from
the last one heard, or when the last line was heard more than a quiet gap
ago (15 minutes by default). One desk talking is heard as one voice; a
change of desk is always named. Names come from the desk agents' notes
(`session:` → `name:`, else the note's file name: "Master Desk", "Aspen
Desk", "Development Desk", "Clerk - Desk"); a session no note names is
announced as "Another desk". A line that already opens with its own name
is not doubled. Every `speak` path carries its session (the CLI and
`speak.py` take it from `CLAUDE_CODE_SESSION_ID`; the MCP tool from the
call or its own environment); a line with no session is spoken as sent.

```bash
auto-speech speaker status               # switch, quiet gap, how many desks are known
auto-speech speaker gap 600              # the same desk names itself again after 10 quiet minutes
auto-speech speaker name <session> Side Desk   # name a session by hand (wins over the notes)
auto-speech speaker forget <session>
auto-speech speaker who [session]        # what a session would be announced as
auto-speech speaker off                  # lines are spoken exactly as sent
auto-speech speaker on                   # default
```

MCP: `speaker(action="gap", seconds=600)`, same actions. Config:
`~/.config/auto-speech/speaker.toml`, section `[speaker]` with `enabled`,
`quiet_gap_seconds`, `agents_dir`, and `[speaker.names]`; see
`config/speaker.toml.example`. A change is read at the next line; no
restart. Slash command `/auto-speech-speaker` (extras). Cached audio gets
the name as a short clip in front of it. The reasoning, and the quiet gap,
is in [ADR-016](docs/decisions/ADR-016-speaker-names.md).

## Web app

`/auto-speech-app` (or `bash plugin/scripts/shell/run_server.sh`) serves
`http://127.0.0.1:7860` — loopback only. Paste-to-speak with Claude-CLI
rewrite, playback transport (pause/seek/stop), a replay-cache browser, and
the `/api/synthesize` + `/api/voices` endpoints the Chrome extension uses.
The model stays hot in the server process, so repeat synthesis is fast.

## Chrome extension

`chrome-extension/` is **AutoSpeech**: right-click any selected text →
*speak selection*, synthesized by the local Kokoro server with automatic
fallback to the browser's built-in voices when the server isn't running.
Voice picker populates live from `/api/voices`.

Install (unpacked): `chrome://extensions` → Developer mode → *Load
unpacked* → select `chrome-extension/`. It needs the web app running for
Kokoro-quality audio. The extension is versioned independently of the
repo (currently 0.4.x); details in
[chrome-extension/README.md](chrome-extension/README.md).

## Narration (optional)

A separate pipeline narrates what Claude is doing **while it happens**:
hook events feed a phase classifier (Explore / Edit / Run / Delegate);
on each phase transition a local MLX LLM produces one spoken sentence.
Off by default, per-session opt-in, and end-of-turn autoplay waits for
the narration queue to drain so the two never overlap. Set up with
`bash setup/install-narrator-hooks.sh` +
`bash setup/install-plugin.sh --with-extras`, then
`/auto-speech-narrate-install` and `/auto-speech-narrate-on`. Details in
[docs/OPERATIONS.md](docs/OPERATIONS.md).

## Troubleshooting

Run `/auto-speech-doctor` first — it checks binaries, disk, logs, the
narrator daemon, queue depth, and autoplay scope. The full runbook (log
locations, config precedence, muting, self-update) is
[docs/OPERATIONS.md](docs/OPERATIONS.md).

## Uninstall

```bash
bash setup/uninstall.sh
```

Removes the command symlinks, all hooks, markers, and temp files; leaves
the repo, the venv, and Homebrew packages. Hook-specific uninstallers
(`uninstall-hook.sh`, `uninstall-bootstrap-hook.sh`,
`uninstall-narrator-hooks.sh`) exist for partial removal.

## Security & privacy

- Synthesis and narration run locally: no telemetry, no cloud TTS, no API
  keys stored. The rewrite step that shapes text for speech goes through
  your own `claude` CLI login (same trust boundary as typing into Claude
  Code) — see [SECURITY.md](SECURITY.md).
- The web server binds `127.0.0.1` only and is unauthenticated — any
  local process can reach it; do not port-forward it. Web pages cannot
  drive it: CORS is granted only to Chrome-extension origins.
- The installers edit `~/.claude/settings.json` (hooks). The optional
  bootstrap hook auto-runs `uv sync` against the committed lockfile on
  session start — it never pulls source from the network.
- Kokoro-82M weights (Apache-2.0) are fetched from Hugging Face on first
  use.
- Vulnerability reports: see [SECURITY.md](SECURITY.md).

## Design history

The feature inventory is [FEATURES.md](FEATURES.md); release history is
[CHANGELOG.md](CHANGELOG.md) with per-version notes in
[docs/releases/](docs/releases/INDEX.md).

Built through a documented OOAD process — the artifacts ship in-repo:
[docs/specification/](docs/specification/README.md) (conceptualization →
analysis → design), [docs/plan/](docs/plan/implementation-plan.md),
[docs/decisions/](docs/decisions/) (15 ADRs), and
[docs/micro-design/](docs/micro-design/) (per-phase class designs).
Contributor guide: [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).

## Acknowledgements

- [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) (Apache-2.0) — the TTS model
- [mlx-audio](https://github.com/Blaizzy/mlx-audio) (MIT) — Apple-Silicon TTS runtime
- [misaki](https://github.com/hexgrad/misaki) (Apache-2.0) — G2P
- [num2words](https://github.com/savoirfairelinux/num2words) (LGPL, used as an unmodified dependency)
- [Flask](https://flask.palletsprojects.com/) (BSD-3-Clause)

## License

MIT — see [LICENSE](LICENSE).
