# auto-speech

A Claude Code plugin that converts a Claude response into spoken audio using a local
TTS model on Apple Silicon, with Fibonacci-scaled buffered playback for minimal
time-to-first-audio and seamless streaming.

## Command

```
/speak          # speak the last assistant message
/speak 2        # speak the 2nd-most-recent assistant message
/speak 5        # speak the 5th-most-recent assistant message
```

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
