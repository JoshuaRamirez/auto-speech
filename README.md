# auto-speech

A Claude Code plugin that converts a Claude response into spoken audio using a local
TTS model on Apple Silicon, with Fibonacci-scaled buffered playback for minimal
time-to-first-audio and seamless streaming.

## Commands

All commands are namespaced with `auto-speech-` so they don't collide with
built-in Claude Code commands or other plugins (see "Naming convention" below).

```
/auto-speech-speak [n]      # speak the n-th most recent assistant message (default 1)
/auto-speech-replay [n]     # replay the n-th most recent cached entry
/auto-speech-app            # launch the localhost web app
/auto-speech-pause          # mpv pause
/auto-speech-resume         # mpv resume
/auto-speech-restart        # mpv seek to 0
/auto-speech-seek           # mpv seek by +N, -N, N, or 'end'
/auto-speech-end            # stop mpv playback
/auto-speech-autoplay-on    # remove the autoplay disable marker
/auto-speech-autoplay-off   # touch the autoplay disable marker
```

## Naming convention

**Every user-level slash command this project installs is prefixed with
`auto-speech-`.** Generic short names like `/pause`, `/resume`, `/end`, and
`/speak` belong to the shared `~/.claude/commands/` namespace; claiming them
from a single plugin breaks Claude Code's built-ins (e.g., session resume)
and any other plugin a user might install.

The prefix:
- prevents collisions across the user's entire plugin ecosystem,
- keeps commands grouped under tab-completion (typing `/auto-` shows them all),
- mirrors the marketplace-plugin convention (`commit-commands:commit`,
  `anthropic-skills:pdf`), so if this project is ever promoted to a real
  marketplace plugin the command names stay stable.

Any new slash command added to `plugin/commands/` MUST follow this convention.
The install script (`setup/install-plugin.sh`) and uninstall script
(`setup/uninstall.sh`) symlink and remove only namespaced names. The uninstall
script also cleans up the legacy unprefixed names for users upgrading from
before this convention.

## Layout

```
auto-speech/
├── docs/
│   ├── specification/   OOAD macro-process output (conceptualization → analysis → design)
│   ├── plan/            implementation plan with embedded micro-process stubs
│   ├── decisions/       ADRs (architecture decision records)
│   └── micro-design/    class-level designs produced during execution
├── plugin/              Claude Code plugin payload
│   ├── commands/        slash commands (speak.md)
│   └── scripts/         python + shell implementation
├── config/              calibration + voice config
├── setup/               one-time install scripts
└── tests/               reference inputs & captured outputs
```

## Read first
- [docs/specification/README.md](docs/specification/README.md) — the spec
- [docs/plan/implementation-plan.md](docs/plan/implementation-plan.md) — the plan
