# auto-speech Constitution

## Core Principles

### I. His machine, his silence
Tests MUST be silent and hermetic: they run only through `tests/run_all.sh`, which sets every
`AUTO_SPEECH_*` state path to a private dir and `AUTO_SPEECH_MPV_AO=null`. Nothing a test or a
build does MAY touch the live narrator: not its socket, its pid file, or its launchd label
`dev.auto-speech.narrator`, and it is never restarted by an agent. A live check that makes sound
is announced to the Desk first, is ≤10 s and quiet, and never touches his Music or any app of
his. Rationale: on 2026-10-18 a suite run cut his speech; on 2026-10-20 a test daemon took the
live label for two hours.

### II. Holds are honoured
A line held by the meeting hold or the sound yield (ADR-015, ADR-018) is not spoken, exported or
shown until it plays. Anything that appears on screen because of a line MUST NOT appear while a
hold stands; a hold that cannot be assessed counts as holding.

### III. Nothing opens on its own
No report, page, tab or app opens without his own word (his 2026-10-20 15:28). A feature that
puts a window in front of him is behind a setting that is off by default and does nothing when
its target is not installed. Launches never take focus (`open -g`).

### IV. One name on every surface
A setting has the same name in its TOML config, the `auto-speech` CLI and the MCP tool, and is
validated in `config_validation.py`. The caller owns state; a downstream tool shows only what it
is handed. External tools are driven only through their published contract.

### V. Make the work succeed; hard checks come last
Failures are fixed by making the work succeed, never by adding a gate (his 2026-10-15 12:51
doctrine). New code fails loudly or degrades to a logged no-op; it never fakes a success.
Every decision of substance gets an ADR in `docs/decisions/`.

## Standing limits

Nothing outward without his named yes (push, publish, upload), except a PR on this repo when an
order says so. Nothing installed or downloaded without his yes. No spend, no secrets printed, no
new access. Never drive his mouse or keyboard; never record or capture audio. Searches stay in
the repo or folder in hand.

## Development workflow

Work happens in a worktree under `.worktrees/` on a feature branch. Commits are small, staged by
named files, in the repo's emoji Conventional Commits form. A change is merged `--no-ff` once
`tests/run_all.sh` (run niced) is green, after the author reviews their own change; no review
rounds. Each shipped change updates `CHANGELOG.md` [Unreleased], `FEATURES.md` and, where it
touches operation, `docs/OPERATIONS.md`.

## Governance

This constitution sums up standing rules recorded in the agent brief and the ADRs; where they
differ, his latest words win and this file is amended. Amendments bump the version (MAJOR: a
principle removed or redefined; MINOR: one added or expanded; PATCH: wording) and are committed
with a note of the source. Every plan's Constitution Check goes through Principles I to V.

**Version**: 1.0.0 | **Ratified**: 2026-10-21 | **Last Amended**: 2026-10-21
