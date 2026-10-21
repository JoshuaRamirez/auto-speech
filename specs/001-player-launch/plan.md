# Implementation Plan: Player launch — auto-speech drives the UI Utility's player

**Branch**: `feat/player-launch` | **Date**: 2026-10-21 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/001-player-launch/spec.md`

## Summary

After a line is heard and exported (ADR-020), `PlayerLauncher.after_line()` today stops at
"contract not wired yet". This feature wires it to the UI Utility caller contract v1 (rev 1.1):
the gates stay synchronous and cheap (off, held, not installed); a passing line is put in a
one-slot mailbox and the call returns at once. A worker thread owns one connection to
`~/.local/run/ui-utility.sock` (`UI_UTILITY_SOCK`), a reader thread drains it (replies to a queue,
events noted, never left to back up), and the worker sends `hello` once per connection, then
`open` of panel `player`, id `auto-speech`, with the history WAV, the sidecar's words, a title
naming the sender, and `play: false`. No socket → launch in the background (`ui-utility launch`, or
`open -g -a "UI Utility"`), then connect within 10 s. The hold is checked again just before `open`.
The last outcome is written to a small state file so `history status` (another process) can
report it. Tests use a stand-in contract server on a private socket; `run_all.sh` sets
`UI_UTILITY_SOCK`, `UI_UTILITY_SILENT=1` and `AUTO_SPEECH_PLAYER_LAUNCH=never`.

## Technical Context

**Language/Version**: Python 3.12 (the repo's `.venv`), bash for `run_all.sh`
**Primary Dependencies**: standard library only (`socket`, `json`, `threading`, `subprocess`)
**Storage**: the history store (ADR-020, read only here); one state file
`auto-speech-player.json` under the state dir (`state_paths.state_path`)
**Testing**: plain-Python test files run by `tests/run_all.sh`; a stand-in UI Utility server on a
Unix socket in the suite's private state dir
**Target Platform**: macOS (Darwin 25), the UI Utility at `~/Applications/UI Utility.app`
**Project Type**: daemon + CLI + MCP server (one repo)
**Performance Goals**: `after_line` returns in < 50 ms whatever the utility does (SC-003); a
running utility shows the line within 2 s, a launched one within 10 s (SC-001)
**Constraints**: never on the speech path; never during a hold; never focus; never the real app in
tests; never the live narrator's socket, pid file or launchd label
**Scale/Scope**: one player panel; one line at a time (newest wins); lines arrive every few seconds
at most

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Result |
|---|---|---|
| I. His machine, his silence | Tests use a stand-in; suite sets a private socket, silent mode and no-launch; no daemon or label touched; no restart | Pass |
| II. Holds are honoured | Gate before queueing and again before `open`; a gate that raises counts as holding | Pass |
| III. Nothing opens on its own | `[history] player` default off; launch with `-g` / `ui-utility launch`; nothing when not installed | Pass |
| IV. One name on every surface | No new setting; `history status` reports the outcome on CLI and MCP alike | Pass |
| V. Make the work succeed | Failures are logged once per kind and recorded as an outcome; nothing faked; ADR-021 records it | Pass |

Post-design re-check (after Phase 1): unchanged, Pass. The one new environment switch
(`AUTO_SPEECH_PLAYER_LAUNCH=never`) is isolation of the same kind as `AUTO_SPEECH_MPV_AO=null`,
not a gate on the work.

## Project Structure

### Documentation (this feature)

```text
specs/001-player-launch/
├── spec.md
├── plan.md              # this file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   └── player-handover.md   # the requests auto-speech sends, the replies it reads
├── checklists/requirements.md
├── TRIAL-LOG.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
plugin/scripts/python/
├── ui_utility_client.py     # NEW: the contract v1 client (connect, reader thread, request/reply, launch)
├── speech_player.py         # CHANGED: gates, mailbox, worker, outcome file, note()
├── speech_history.py        # unchanged API (HistoryLine.wav, .sidecar)
└── narrator_service.py      # unchanged call site (_heard → player.after_line); close on shutdown
tests/
├── ui_utility_standin.py    # NEW: a contract v1 stand-in server (records requests, scripted replies)
├── test_ui_utility_client.py  # NEW
├── test_speech_history.py   # CHANGED: launcher tests move from "contract pending" to hand-over
└── run_all.sh               # CHANGED: UI_UTILITY_SOCK, UI_UTILITY_SILENT=1, AUTO_SPEECH_PLAYER_LAUNCH=never
docs/decisions/ADR-021-player-launch.md   # NEW
config/history.toml.example, CHANGELOG.md, FEATURES.md, docs/OPERATIONS.md  # CHANGED
```

**Structure Decision**: the existing single-repo layout; the client is its own module so the
daemon-free CLI and tests can use it, and the launcher keeps its ADR-020 interface
(`after_line(line, settings=None) -> str`, `note(settings) -> str`).

## Complexity Tracking

No constitution violations. Two threads (worker and reader) are the least that keeps the hand-over
off the speech path and keeps the utility's events from backing up on an unread socket.
