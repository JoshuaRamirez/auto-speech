# Project: auto-speech Unified CLI Dispatcher & MCP Lifecycle Hook Parity

## Architecture
The `auto-speech` system expands from individual script entrypoints into a unified dual-surface architecture:
1. **Unified CLI Surface**: Single executable command-line dispatcher `auto-speech` linked into `~/.local/bin/auto-speech`, routing to all subsystem functions (speech, replay, transport, autoplay, narrate, hooks, diagnostics, updates).
2. **Expanded MCP Surface**: Complete JSON-RPC 2.0 tool suite (`speak`, `autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`, `doctor`) providing AI agents programmatic parity with shell lifecycle hooks.
3. **Execution Sandboxing**: All development, implementation, and empirical verification execute in an isolated Git worktree (`worktree/cli-mcp-parity`), bidirectionally reconciled and cleaned up.

```
              ┌───────────────────────────┐     ┌────────────────────────────┐
              │  Terminal / Shell Hooks   │     │      AI Agents (MCP)       │
              └─────────────┬─────────────┘     └──────────────┬─────────────┘
                            │                                  │
                            ▼ (~/.local/bin/auto-speech)       ▼ (tools/call)
              ┌───────────────────────────┐     ┌────────────────────────────┐
              │    bin/auto-speech        │     │        mcp_server.py       │
              │  (Bash Venv Trampoline)   │     │  (Expanded 6-Tool Suite)   │
              └─────────────┬─────────────┘     └──────────────┬─────────────┘
                            │                                  │
                            ▼                                  ▼
              ┌──────────────────────────────────────────────────────────────┐
              │               Core Python Subsystem & Clients                │
              │  - cli.py: Subcommand Parser & Router                        │
              │  - DaemonClient: In-memory Priority IPC (P1-P4)              │
              │  - NativeAudioSink & MpvIpc: Synchronous mpv & IPC Socket    │
              │  - CacheStore: Content-addressable WAV audio cache           │
              │  - MessageSelector & ClaudeCliRewriter: Transcript & Prompts │
              │  - SessionEnrollment & SoloScope: State & Spotlight markers  │
              │  - Doctor & HealthReport: 10-probe System Diagnostics        │
              └─────────────────────────────┬────────────────────────────────┘
                                            │
                                            ▼ (/tmp/auto-speech-daemon.sock)
                              ┌───────────────────────────┐
                              │    narrator_service.py    │
                              │     (Unified Daemon)      │
                              └───────────────────────────┘
```

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Worktree Sandbox Setup | Isolated Git worktree `worktree/cli-mcp-parity` at `~/Developer/auto-speech-worktree` with symlinked `.venv` | M0 | ORIGINAL_REQUEST §R4 |
| 2 | CLI Dispatcher Trampoline | `bin/auto-speech` bash trampoline resolving true project root and `.venv/bin/python` | M1 | ORIGINAL_REQUEST §R1 |
| 3 | CLI Core Router (`cli.py`) | Pure Python CLI dispatcher implementing top-level parser and 12 subcommands | M1 | ORIGINAL_REQUEST §R1 |
| 4 | CLI Speech Synthesis (`speak`) | Direct speech synthesis handling CLI args, piped stdin, `--source-hash` cache lookup, and mute checks | M1 | ORIGINAL_REQUEST §R1 |
| 5 | CLI Playback Replay (`replay`) | Cached audio replay by ordinal using `CacheStore` and `DaemonClient.play_cache` | M1 | ORIGINAL_REQUEST §R1 |
| 6 | CLI Transport Controls | Interactive playback controls (`pause`, `resume`, `stop`, `restart`, `seek`) via mpv IPC and daemon interrupt | M1 | ORIGINAL_REQUEST §R1 |
| 7 | CLI Autoplay Management | Autoplay controls (`on`, `off`, `status`, `mode`, `scope`) managing enrollment markers and TOML config | M1 | ORIGINAL_REQUEST §R1 |
| 8 | CLI Narration Controls | Narration controls (`on`, `off`, `status`, `stop`, `config`) managing session markers and daemon lifecycle | M1 | ORIGINAL_REQUEST §R1 |
| 9 | CLI Hook Simulation | Hook triggers (`hook stop`, `hook narrator`, `hook bootstrap`) accepting JSON payloads on stdin | M1 | ORIGINAL_REQUEST §R1 |
| 10 | CLI Diagnostics & Update | Subcommands `doctor` (text & `--json`) and `update` (`--force`) | M1 | ORIGINAL_REQUEST §R1 |
| 11 | Symlink & PATH Integration | Link `~/.local/bin/auto-speech -> bin/auto-speech` and update `setup/install-plugin.sh` | M1 | ORIGINAL_REQUEST §R1 |
| 12 | MCP Expanded Tool Advertisement | `tools/list` advertises full 6-tool suite (`speak`, `autoplay_turn`, `narrate_event`, `playback_control`, `session_configure`, `doctor`) | M2 | ORIGINAL_REQUEST §R2 |
| 13 | MCP Tool: `speak` | Verbatim text synthesis with global mute checking and `DaemonClient.speak` | M2 | ORIGINAL_REQUEST §R2 |
| 14 | MCP Tool: `autoplay_turn` | In-process transcript extraction (Claude Code & Antigravity), rewriting, and playback with closed-loop caching | M2 | ORIGINAL_REQUEST §R2 |
| 15 | MCP Tool: `narrate_event` | Ingest lifecycle activity events into `/tmp/auto-speech-narrator-events.jsonl` with enrollment check and daemon check | M2 | ORIGINAL_REQUEST §R2 |
| 16 | MCP Tool: `playback_control` | Programmatic transport control (pause, resume, stop, restart, seek, replay) | M2 | ORIGINAL_REQUEST §R2 |
| 17 | MCP Tool: `session_configure` | Programmatic management of session enrollment, solo scope, and mode configuration | M2 | ORIGINAL_REQUEST §R2 |
| 18 | MCP Tool: `doctor` | Programmatic diagnostics returning structured `HealthReport` | M2 | ORIGINAL_REQUEST §R2 |
| 19 | Hook & Router Reconcile | Update `autoplay_hook.sh` and `narrator_hook.sh` for Antigravity `.conversationId` and unified CLI delegation | M3 | ORIGINAL_REQUEST §R3 |
| 20 | Slash Command Reconcile | Update 21 slash commands (`plugin/commands/` and `plugin/commands-extra/`) to delegate to `auto-speech` | M3 | ORIGINAL_REQUEST §R3 |
| 21 | Test Suite & Regression Hardening | Dedicated unit tests for CLI (`tests/test_cli_dispatcher.py`), updated `tests/test_mcp_server.py`, and 100% green pass of all 49+ tests | M4 | ORIGINAL_REQUEST §Verification |
| 22 | Bidirectional Merge & Cleanup | Merge `main` into worktree, verify tests green, merge worktree to `main`, remove worktree, and write artifact documentation | M5 | ORIGINAL_REQUEST §R4, §R5 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M0 | Isolated Git Worktree Environment Setup | Create `worktree/cli-mcp-parity` at `~/Developer/auto-speech-worktree`, symlink `.venv`, verify baseline tests | none | DONE |
| M1 | Unified `auto-speech` CLI Dispatcher | Implement `bin/auto-speech`, `plugin/scripts/python/cli.py`, subcommands, symlink `~/.local/bin/auto-speech`, unit tests | M0 | DONE |
| M2 | Expanded MCP Server Tool Suite | Implement 6-tool suite in `plugin/scripts/python/mcp_server.py`, mute checks, unit tests in `tests/test_mcp_server.py` | M0 | DONE |
| M3 | Hook Reconcile & Architectural Coherence | Update shell hooks (`autoplay_hook.sh`, `narrator_hook.sh`), reconcile 21 slash commands, preserve test invariants | M1, M2 | DONE |
| M4 | Comprehensive Test Suite & Regression Verification | Verify all 49+ tests via `bash tests/run_all.sh` and Tier 5 E2E tests, ensure 0 ruff lint errors | M1, M2, M3 | DONE |
| M5 | Bidirectional Worktree Merge & Cleanup | Merge `main` into worktree, verify green, merge to `main`, remove worktree, generate report artifact | M4 | DONE |

## Interface Contracts
### CLI Dispatcher: `bin/auto-speech` & `plugin/scripts/python/cli.py`
- **Executable**: `auto-speech <subcommand> [<args>]`
- **Subcommands**:
  - `speak [text ...] [--source-hash H] [--session-id ID] [--priority P] [--voice V] [--speed S]`
  - `replay [--ordinal N]`
  - `pause`, `resume`, `stop`, `restart`
  - `seek TARGET`
  - `autoplay {on, off, status, mode, scope}`
  - `narrate {on, off, status, stop, config}`
  - `hook {stop, narrator, bootstrap}`
  - `doctor [--json]`
  - `update [--force]`
- **Exit Codes**:
  - `0`: Success (or cleanly muted)
  - `1`: General failure / daemon unreachable / doctor FAIL
  - `2`: No active playback session / invalid input / missing target
  - `3`: mpv IPC failure
  - `4`: Invalid target argument
  - `6`: Playback failure
  - `130`: Interrupted (SIGINT)

### MCP Server: `plugin/scripts/python/mcp_server.py`
- **Protocol**: JSON-RPC 2.0 over stdio newline-delimited JSON.
- **Tools**:
  - `speak`: `{"text": string}`
  - `autoplay_turn`: `{"session_id": string (optional), "transcript_path": string (optional), "mode": string (optional)}`
  - `narrate_event`: `{"event": string, "session_id": string, "payload": object}`
  - `playback_control`: `{"action": "pause"|"resume"|"stop"|"restart"|"seek"|"replay", "target": string (optional), "ordinal": integer (optional)}`
  - `session_configure`: `{"subsystem": "autoplay"|"narrate", "action": "on"|"off"|"mode"|"scope", "session_id": string (optional), "value": string (optional)}`
  - `doctor`: `{"json_format": boolean (optional)}`
- **Global Mute Enforcement**: All speech-producing tools (`speak`, `autoplay_turn`, `narrate_event`, `playback_control` for replay/resume) verify `AutoplayGate().worker_gated_off()`. If muted, return notice/error without synthesizing or playing audio.

## Code Layout
- `bin/auto-speech`: Executable bash trampoline resolving true project root and `.venv`.
- `plugin/scripts/python/cli.py`: Core Python dispatcher routing subcommands.
- `plugin/scripts/python/mcp_server.py`: MCP server expanded with 6 tools.
- `plugin/scripts/shell/autoplay_hook.sh`: Updated for Antigravity `.conversationId` and CLI integration.
- `plugin/scripts/shell/narrator_hook.sh`: Updated for Antigravity `.conversationId` and CLI integration.
- `plugin/commands/`: 8 curated slash commands delegating to `auto-speech`.
- `plugin/commands-extra/`: 13 extra slash commands delegating to `auto-speech`.
- `tests/test_cli_dispatcher.py`: New unit and integration tests for CLI dispatcher.
- `tests/test_mcp_server.py`: Expanded unit tests for MCP server tools.
- `reports/`: Timestamped artifact documentation.
