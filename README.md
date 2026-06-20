# auto-speech

A Claude Code plugin that converts a Claude response into spoken audio using a local
TTS model on Apple Silicon, with Fibonacci-scaled buffered playback for minimal
time-to-first-audio and seamless streaming.

## Commands

All commands are namespaced with `auto-speech-` so they don't collide with
built-in Claude Code commands or other plugins (see "Naming convention" below).

Playback (per-response autoplay + manual replay):
```
/auto-speech-speak [n]      # speak the n-th most recent assistant message (default 1)
/auto-speech-replay [n]     # replay the n-th most recent cached entry
/auto-speech-app            # launch the localhost web app
/auto-speech-pause          # mpv pause
/auto-speech-resume         # mpv resume
/auto-speech-restart        # mpv seek to 0
/auto-speech-seek           # mpv seek by +N, -N, N, or 'end'
/auto-speech-end            # stop mpv playback
/auto-speech-autoplay-on        # re-enable end-of-turn read for THIS session (on by default)
/auto-speech-autoplay-off       # opt THIS session out
/auto-speech-autoplay-mode      # set or show summary mode (verbatim|small|medium|large)
/auto-speech-autoplay-status    # show session opt-out marker, opt-out dir, mpv, log tail
/auto-speech-scope [all|solo]   # read ALL sessions, or only THIS one (spotlight)
```

Diagnostics & maintenance:
```
/auto-speech-doctor [json]      # health check; exits non-zero when unhealthy
/auto-speech-update             # reconcile the venv with uv.lock (no git pull)
```

## Autoplay

The end-of-turn autoplay reads each completed assistant response aloud.
It is **on by default for every session**. Gating is per-session
**opt-out**: `/auto-speech-autoplay-off` touches a marker file that
silences just that session, leaving all others playing:

```
~/.claude/auto-speech-autoplay-sessions/<session_id>
```

Behaviour with the per-session opt-out marker:

| State of `auto-speech-autoplay-sessions/<session_id>` | Effect |
|---|---|
| absent (default) | Autoplay fires for that session. |
| present | That session is silenced; other sessions are unaffected. |

A global panic mute at `~/.claude/auto-speech.disabled` takes precedence
over everything and silences every session regardless of per-session
state.

Concurrent playbacks (multiple sessions, or rapid turns in one session)
are serialized through a strict cross-session FIFO queue
(`/tmp/auto-speech-playback-queue/`): each pending playback waits for
the current one to finish before starting, in arrival order. An active
playback is **never** cut off by a newer one. Back-to-back Stop events
within the coalesce window still collapse to the newest, and literally
identical audio already in flight is deduplicated rather than replayed.

The end-of-turn read shape is controlled by `~/.config/auto-speech/autoplay.toml`
(four modes — see the table below), tunable also via `/auto-speech-autoplay-mode`:

| Mode | Length | When |
|---|---|---|
| `verbatim` | full content, lossless | when every fact matters |
| `summary` `small` | 1-3 sentences, what happened (default) | quick status |
| `summary` `medium` | 3-5 sentences | balanced |
| `summary` `large` | 6-10 sentences, preserves nuance | long technical replies |

Plus `coalesce_seconds` (default 1) and `narration_wait_max_seconds`
(default 90) in the same config — formerly hardcoded as env vars,
now overridable both in the config file and via the legacy env vars.

The end-of-turn autoplay supports four modes via `~/.config/auto-speech/autoplay.toml`:

| Mode | Length | When |
|---|---|---|
| `verbatim` | full content, lossless | when every fact matters |
| `summary` `small` | 1-3 sentences, what happened (default) | quick status |
| `summary` `medium` | 3-5 sentences | balanced |
| `summary` `large` | 6-10 sentences, preserves nuance | long technical replies |

Real-time narration (play-by-play of in-flight turns; local LLM, default off):
```
/auto-speech-narrate-install [model]  # install mlx-lm + pull a default model, write user config
/auto-speech-narrate-on               # enable narration in this project, start daemon
/auto-speech-narrate-off              # disable narration in this project (daemon idle-exits)
/auto-speech-narrate-stop             # stop the daemon immediately
/auto-speech-narrate-status           # marker / pid / queue depth / recent log
/auto-speech-narrate-config [show|edit|path]  # inspect or edit ~/.config/auto-speech/narrator.toml
```

## Narration

A separate pipeline (independent of end-of-turn autoplay) summarises what
Claude is doing **while it happens** and speaks the summary aloud in
newscaster voice. PreToolUse / PostToolUse / Stop / UserPromptSubmit hooks
feed an event stream; a phase classifier groups consecutive tool calls of
the same category (Explore / Edit / Run / Delegate); on each phase
transition a local LLM produces one sentence; the line plays through the
existing TTS pipeline in strict FIFO order.

End-of-turn autoplay waits for the narration FIFO to drain before reading
the final response, so the two pipelines never overlap.

Gating is **per-session** via a marker file
(`~/.claude/auto-speech-narrate-sessions/<session_id>`). Each Claude Code
session opts in independently — running `/auto-speech-narrate-on` in one
window does not turn on narration for a parallel Claude Code session in
the same project. The per-project marker scheme from the original Phase
23 was deprecated: a single project may have many concurrent sessions
and the user only wants to hear narration for the session they're
attending to.

Provider is pluggable. The shipped default is `mock` (templated, no deps);
`/auto-speech-narrate-install` flips it to `mlx` and pulls a 4-bit Qwen 3B
model (configurable). Ollama / OpenAI / Anthropic providers are reserved
slots for future implementations.

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
- [docs/OPERATIONS.md](docs/OPERATIONS.md) — run unattended, monitor with
  `/auto-speech-doctor`, troubleshoot, log locations, env-var precedence
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) — run tests + lint, the
  hermetic CI subset, and the dependency-lock workflow

## Known deferred work

Two items from the hardening pass were intentionally left undone:

- **End-to-end integration test** (full Stop-hook → autoplay worker
  → cli_rewrite → speak.py → mpv → audible WAV). Doing this properly
  requires a fake `claude` binary on PATH AND a mock mpv that records
  start invocations without actually playing audio. The current
  17-file suite covers each link in the chain individually, so the
  marginal value of a true end-to-end run is low relative to the
  fixture cost. Re-evaluate if a future regression spans multiple
  components.
- **`REASON` phase category** is declared in
  `narrator_phase_classifier.Category` and the MockSummarizer's verb
  tables, but never assigned. The intent was to narrate assistant
  text-only chunks (no tool call) as a "thinking" phase. Claude Code
  doesn't emit such chunks through any of the hook events we use
  (PreToolUse / PostToolUse / Stop / UserPromptSubmit), so wiring
  the category needs a different signal source — e.g., tailing the
  transcript JSONL — which we don't currently have a clean place
  for. The category is left in the enum so a future implementation
  doesn't need a migration.
