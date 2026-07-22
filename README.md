# auto-speech

[![CI](https://github.com/JoshuaRamirez/auto-speech/actions/workflows/ci.yml/badge.svg)](https://github.com/JoshuaRamirez/auto-speech/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/JoshuaRamirez/auto-speech)](https://github.com/JoshuaRamirez/auto-speech/releases)

Speaks Claude Code responses aloud using a **local Kokoro TTS model on Apple
Silicon** — no cloud, no API keys. Fibonacci-scaled buffered playback gives
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
bash setup/install-plugin.sh --with-extras   # + 13 extra slash commands
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
| `/auto-speech-autoplay-on` | re-enable end-of-turn autoplay for THIS session (on by default) |
| `/auto-speech-autoplay-off` | opt THIS session out |
| `/auto-speech-autoplay-mode` | show or set the autoplay mode (`verbatim\|small\|medium\|large`) |
| `/auto-speech-scope [all\|solo]` | read ALL sessions, or only THIS one |
| `/auto-speech-doctor [json]` | health check; exits non-zero when unhealthy |

13 more (playback transport, narrator controls, autoplay-status, update)
live in `plugin/commands-extra/` — install with
`bash setup/install-plugin.sh --with-extras`. Every command is prefixed
`auto-speech-` so nothing collides with Claude Code built-ins or other
plugins; new commands must follow the convention.

## Autoplay

The end-of-turn autoplay reads each completed assistant response aloud.
It is **on by default for every session**. Gating is per-session
**opt-out**: `/auto-speech-autoplay-off` touches a marker file
(`~/.claude/auto-speech-autoplay-sessions/<session_id>`) that silences
just that session, leaving all others playing. A global panic mute at
`~/.claude/auto-speech.disabled` takes precedence over everything.

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

## Web app

`/auto-speech-app` (or `python plugin/scripts/python/web_server.py`) serves
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

- Everything runs locally: no telemetry, no cloud TTS, no API keys.
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

Built through a documented OOAD process — the artifacts ship in-repo:
[docs/specification/](docs/specification/README.md) (conceptualization →
analysis → design), [docs/plan/](docs/plan/implementation-plan.md),
[docs/decisions/](docs/decisions/) (14 ADRs), and
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
