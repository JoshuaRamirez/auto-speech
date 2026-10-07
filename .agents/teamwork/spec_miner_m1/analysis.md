# Specification Mining Report: Unified `auto-speech` CLI Dispatcher (Milestone M1)

**Target Test File**: `tests/test_cli_dispatcher.py`  
**Target Code**: `bin/auto-speech` and `plugin/scripts/python/cli.py`  
**Date**: 2026-10-06  
**Author**: `spec_miner_m1` (Teamwork Preview Specification Miner)  
**Authoritative Sources**:
- `PROJECT.md` (§ Feature Inventory, § Interface Contracts, § Code Layout)
- `ORIGINAL_REQUEST.md` (header `## 2026-10-06T20:47:50Z`)
- Existing scripts: `plugin/scripts/python/speak.py`, `replay.py`, `control.py`, `doctor.py`, `self_update.py`, `autoplay_config.py`, `autoplay_enrollment.py`, `autoplay_scope.py`, `narrator_config.py`, `mpv_ipc.py`
- Existing hook scripts: `plugin/scripts/shell/autoplay_hook.sh`, `narrator_hook.sh`, `setup/bootstrap.sh`
- Existing test suites: `tests/test_replay_control.py`, `tests/test_speak_client.py`, `tests/test_doctor.py`, `tests/test_cli_rewrite.py`, `tests/test_narrator_hook.sh`, `tests/test_bootstrap_hook_idempotent.sh`

---

## Executive Summary
This report mines the authoritative interface contracts, expected behaviors, inputs, outputs, error return codes, and edge cases for the Milestone M1 unified CLI dispatcher. It specifies the exact test architecture and assertions for `tests/test_cli_dispatcher.py` to achieve 100% test coverage across all 12 subcommands and top-level entrypoints without requiring external runtime dependencies or audio hardware.

---

## Features Discovered

| # | Category | Feature | Description | Inputs | Outputs | Error Behavior | Discovered Via |
|---|----------|---------|-------------|--------|---------|----------------|----------------|
| 1 | Top-Level CLI | `auto-speech --help` | Display program description and enumerate all 12 subcommands | `argv=["--help"]` or `["-h"]` | stdout containing synopsis, options, and subcommand list | Exit 0 | `PROJECT.md` § Interface Contracts |
| 2 | Top-Level CLI | Missing Subcommand | Invoking CLI without subcommands | `argv=[]` | stderr with usage summary | Exit 2 (argparse missing subcommand) | `PROJECT.md` § Interface Contracts |
| 3 | Top-Level CLI | Invalid Subcommand | Invoking CLI with non-existent command | `argv=["foobar"]` | stderr with invalid choice message | Exit 2 | `PROJECT.md` § Interface Contracts |
| 4 | Trampoline | `bin/auto-speech` Shell Trampoline | Resolves true project root, points to `.venv/bin/python`, routes `$@` to `cli.py` | CLI arguments via terminal | Executed python process output | Propagates python exit code | `PROJECT.md` Feature 2 |
| 5 | Speak | Positional Text Arguments | Speak text supplied directly as positional CLI arguments | `argv=["speak", "Hello", "world"]` | Audio queued/spoken via daemon; exit 0 | Exit 1 if daemon unreachable | `PROJECT.md` Feature 4, `speak.py` |
| 6 | Speak | Piped Stdin Text | Read text from stdin when no positional text arguments given | `argv=["speak"]`, `sys.stdin="Text from stdin"` | Audio queued/spoken via daemon; exit 0 | Exit 1 if daemon unreachable | `PROJECT.md` Feature 4, `speak.py` |
| 7 | Speak | Arg Precedence over Stdin | Positional text takes precedence when both args and stdin exist | `argv=["speak", "arg text"]`, `stdin="piped text"` | Uses "arg text"; ignores stdin | Exit 0 on success | `speak.py:161` |
| 8 | Speak | `--source-hash` Playback | Direct playback from audio cache using 64-hex SHA-256 hash | `argv=["speak", "--source-hash", "<64-hex>"]` | DaemonClient.play_cache invoked; exit 0 | Exit 2 if hash not 64 hex chars | `PROJECT.md` Feature 4, `speak.py:151` |
| 9 | Speak | Empty Input Short-Circuit | No positional args, whitespace/empty stdin, and no `--source-hash` | `argv=["speak"]`, `stdin="   "` | stderr: error indicating empty input | Exit 2 (invalid input) | `PROJECT.md` § Exit Codes |
| 10 | Speak | Global Mute Check | Check `AutoplayGate().worker_gated_off()` / `~/.claude/auto-speech.disabled` | `argv=["speak", "text"]` when disabled marker exists | Notice to stderr/stdout; no audio queued | Exit 0 (cleanly muted) | `PROJECT.md` § Exit Codes (0: Cleanly muted) |
| 11 | Speak | Priority Flag Forwarding | Pass `--priority` (1, 2, 3) to daemon client | `argv=["speak", "text", "--priority", "1"]` | Daemon client receives `priority=1` | Exit 2 if non-integer | `PROJECT.md` Feature 4, `speak.py:144` |
| 12 | Speak | Session ID Forwarding | Pass `--session-id` to daemon client for tracking | `argv=["speak", "text", "--session-id", "sess-1"]` | Daemon client receives `session_id="sess-1"` | Exit 0 | `PROJECT.md` Feature 4, `speak.py:138` |
| 13 | Speak | Voice / Speed Options | Forward optional voice profile and speed parameters | `argv=["speak", "text", "--voice", "alloy", "--speed", "1.2"]` | Parameters forwarded to synthesizer/daemon | Exit 2 if speed non-float | `PROJECT.md` § Interface Contracts |
| 14 | Speak | Daemon Unreachable | Daemon socket not responding and start script fails | `argv=["speak", "text"]` with dead socket | stderr: connection failure diagnostics | Exit 1 | `PROJECT.md` § Exit Codes, `speak.py:94` |
| 15 | Replay | Default Ordinal Playback | Replay most recent cached WAV (ordinal=1) | `argv=["replay"]` | Plays 1st entry from CacheStore recency list | Exit 0 on success | `PROJECT.md` Feature 5, `replay.py:40` |
| 16 | Replay | Explicit Valid Ordinal | Replay Nth cached entry | `argv=["replay", "--ordinal", "2"]` | Plays 2nd entry from CacheStore recency list | Exit 0 on success | `PROJECT.md` Feature 5, `replay.py:40` |
| 17 | Replay | Zero or Negative Ordinal | Ordinal argument < 1 | `argv=["replay", "--ordinal", "0"]` | stderr: `replay: --ordinal must be >= 1, got 0` | Exit 2 | `replay.py:47` |
| 18 | Replay | Empty Cache Error | Replay requested when no cache entries exist | `argv=["replay"]` with empty cache dir | stderr: `replay: no cache entries found...` | Exit 2 | `replay.py:53` |
| 19 | Replay | Ordinal Out of Bounds | Ordinal requested exceeds available entries | `argv=["replay", "--ordinal", "5"]` with 2 entries | stderr: `replay: only 2 cache entries...` | Exit 2 | `replay.py:59` |
| 20 | Replay | Playback Failure | Audio sink raises `AudioSinkError` | Mock sink raising `AudioSinkError("mpv failure")` | stderr: `replay: playback failed: mpv failure` | Exit 6 (`EXIT_PLAYBACK_FAIL`) | `replay.py:17`, `tests/test_replay_control.py:136` |
| 21 | Replay | Keyboard Interrupt | User cancels playback with Ctrl+C | Mock sink raising `KeyboardInterrupt` | Sink interrupted cleanly | Exit 130 (`EXIT_INTERRUPTED`) | `replay.py:18`, `tests/test_replay_control.py:160` |
| 22 | Replay | Global Mute Check | Honor global mute marker during replay | Disabled marker exists | Clean exit without playing audio | Exit 0 | `PROJECT.md` § Exit Codes |
| 23 | Transport | `pause` No Session | Pause playback when no active session exists | `argv=["pause"]` | stderr: `control: no active playback session...` | Exit 2 (`EXIT_NO_SESSION`) | `control.py:14`, `PROJECT.md` § Exit Codes |
| 24 | Transport | `pause` Active Session | Pause playback with active session | `argv=["pause"]` (mocked session) | mpv IPC command `{"command": ["set_property", "pause", true]}` sent | Exit 0 (`EXIT_OK`) | `control.py:8`, `mpv_ipc.py` |
| 25 | Transport | `resume` No Session | Resume playback when no active session exists | `argv=["resume"]` | stderr: `control: no active playback session...` | Exit 2 (`EXIT_NO_SESSION`) | `control.py:14` |
| 26 | Transport | `resume` Active Session | Resume playback with active session | `argv=["resume"]` (mocked session) | mpv IPC command `{"command": ["set_property", "pause", false]}` sent | Exit 0 (`EXIT_OK`) | `control.py:8` |
| 27 | Transport | `stop` No Session | Stop playback when no active session exists | `argv=["stop"]` | stderr: `control: no active playback session...` | Exit 2 (`EXIT_NO_SESSION`) | `control.py:14`, `PROJECT.md` Feature 6 |
| 28 | Transport | `stop` Active Session | Stop playback with active session | `argv=["stop"]` (mocked session) | mpv IPC command / daemon interrupt sent | Exit 0 (`EXIT_OK`) | `control.py:34`, `PROJECT.md` Feature 6 |
| 29 | Transport | `restart` No Session | Restart playback when no active session exists | `argv=["restart"]` | stderr: `control: no active playback session...` | Exit 2 (`EXIT_NO_SESSION`) | `control.py:14` |
| 30 | Transport | `restart` Active Session | Restart playback with active session | `argv=["restart"]` (mocked session) | mpv IPC seek 0 absolute sent | Exit 0 (`EXIT_OK`) | `control.py:30` |
| 31 | Transport | `seek` No Session | Seek playback when no active session exists | `argv=["seek", "+15"]` | stderr: `control: no active playback session...` | Exit 2 (`EXIT_NO_SESSION`) | `control.py:39` |
| 32 | Transport | `seek` Missing Target | Seek invoked with no target argument | `argv=["seek"]` | stderr: required argument error | Exit 2 (argparse) | `control.py:57` |
| 33 | Transport | `seek` Empty Target | Seek invoked with whitespace target when session valid | `argv=["seek", "   "]` (mocked session) | stderr: `control: seek requires a target...` | Exit 4 (`EXIT_BAD_ARG`) | `control.py:44`, `tests/test_replay_control.py:55` |
| 34 | Transport | `seek` Valid Target | Seek invoked with valid relative/absolute target | `argv=["seek", "+15"]` or `["seek", "end"]` | mpv IPC seek command sent | Exit 0 (`EXIT_OK`) | `control.py:46` |
| 35 | Transport | mpv IPC Socket Error | mpv socket communication failure | `argv=["pause"]` (socket error) | stderr: mpv socket unavailable | Exit 3 (`EXIT_IPC_FAIL`) | `control.py:10`, `mpv_ipc.py:50` |
| 36 | Autoplay | `autoplay on` With ID | Enroll session in autoplay | `argv=["autoplay", "on", "--session-id", "sess-1"]` | Marker `~/.claude/auto-speech-autoplay-enabled/sess-1` created | Exit 0 | `autoplay_enrollment.py:65` |
| 37 | Autoplay | `autoplay on` From Env | Enroll session via `CLAUDE_CODE_SESSION_ID` | `argv=["autoplay", "on"]`, `env={"CLAUDE_CODE_SESSION_ID": "sess-env"}` | Marker `.../sess-env` created | Exit 0 | `autoplay_enrollment.py:108` |
| 38 | Autoplay | `autoplay on` Missing ID | Attempt to enroll without session ID | `argv=["autoplay", "on"]` without env or arg | stderr: `autoplay on: no session ID provided...` | Exit 1 | `autoplay_enrollment.py:68` |
| 39 | Autoplay | `autoplay on` Idempotent | Re-enroll an already enrolled session | `argv=["autoplay", "on", "--session-id", "sess-1"]` twice | No-op, marker remains | Exit 0 | `autoplay_enrollment.py:71` |
| 40 | Autoplay | `autoplay off` With ID | Withdraw session from autoplay | `argv=["autoplay", "off", "--session-id", "sess-1"]` | Marker `.../sess-1` removed | Exit 0 | `autoplay_enrollment.py:76` |
| 41 | Autoplay | `autoplay off` Absent Marker | Withdraw session not currently enrolled | `argv=["autoplay", "off", "--session-id", "sess-1"]` (no marker) | No error, idempotent | Exit 0 | `autoplay_enrollment.py:83` |
| 42 | Autoplay | `autoplay status` | Display current autoplay status summary | `argv=["autoplay", "status"]` | stdout formatted status (enrolled state, scope, mode) | Exit 0 | `autoplay_enrollment.py:96`, `autoplay_scope.py:88` |
| 43 | Autoplay | `autoplay mode` (Show) | Show current autoplay mode and size | `argv=["autoplay", "mode"]` | stdout: resolved mode, size, and config path | Exit 0 | `auto-speech-autoplay-mode.md:20` |
| 44 | Autoplay | `autoplay mode` (Set Valid) | Set autoplay mode (`raw`, `verbatim`, `small`, `medium`, `large`) | `argv=["autoplay", "mode", "medium"]` | Wrote `~/.config/auto-speech/autoplay.toml`; stdout confirmation | Exit 0 | `auto-speech-autoplay-mode.md:31` |
| 45 | Autoplay | `autoplay mode` (Invalid) | Set unknown autoplay mode | `argv=["autoplay", "mode", "ultra"]` | stderr: `autoplay-mode: unknown value ultra...` | Exit 2 | `auto-speech-autoplay-mode.md:18` |
| 46 | Autoplay | `autoplay scope` (Show) | Show current autoplay spotlight scope | `argv=["autoplay", "scope"]` | stdout: `autoplay scope: ALL` or `SOLO` | Exit 0 | `autoplay_scope.py:88` |
| 47 | Autoplay | `autoplay scope all` | Reset scope to ALL (clear solo spotlight) | `argv=["autoplay", "scope", "all"]` | Marker `~/.claude/auto-speech-autoplay-solo` removed | Exit 0 | `autoplay_scope.py:78` |
| 48 | Autoplay | `autoplay scope solo` | Set scope to SOLO for active session | `argv=["autoplay", "scope", "solo", "--session-id", "sess-1"]` | Writes `sess-1` to solo marker; enrolls session | Exit 0 | `autoplay_scope.py:68` |
| 49 | Autoplay | `autoplay scope solo` Missing ID | Set scope to SOLO without session ID | `argv=["autoplay", "scope", "solo"]` without env/arg | stderr: `scope solo requires a session id` | Exit 1 | `auto-speech-scope.md:52` |
| 50 | Autoplay | `autoplay scope` (Invalid) | Set unknown scope argument | `argv=["autoplay", "scope", "custom"]` | stderr: `scope: unknown value custom...` | Exit 2 | `auto-speech-scope.md:21` |
| 51 | Narrate | `narrate on` With ID | Enable narration for session | `argv=["narrate", "on", "--session-id", "sess-1"]` | Marker `~/.claude/auto-speech-narrate-sessions/sess-1` created | Exit 0 | `auto-speech-narrate-on.md:23` |
| 52 | Narrate | `narrate on` Missing ID | Attempt to enable narration without session ID | `argv=["narrate", "on"]` without env/arg | stderr: `narrate on: session ID not set...` | Exit 1 | `auto-speech-narrate-on.md:18` |
| 53 | Narrate | `narrate off` | Disable narration for session | `argv=["narrate", "off", "--session-id", "sess-1"]` | Marker `.../sess-1` removed | Exit 0 | `auto-speech-narrate-off.md:20` |
| 54 | Narrate | `narrate status` | Display narration daemon and session status | `argv=["narrate", "status"]` | stdout status report (daemon PID, queue depth) | Exit 0 | `auto-speech-narrate-status.md` |
| 55 | Narrate | `narrate stop` | Terminate narrator daemon process | `argv=["narrate", "stop"]` | Daemon stopped; stdout confirmation | Exit 0 | `auto-speech-narrate-stop.md` |
| 56 | Narrate | `narrate config` (Show) | Show active narrator configuration | `argv=["narrate", "config"]` or `["narrate", "config", "show"]` | stdout: TOML configuration dump | Exit 0 | `auto-speech-narrate-config.md:35` |
| 57 | Narrate | `narrate config path` | Print path to active narrator configuration | `argv=["narrate", "config", "path"]` | stdout: configuration file path | Exit 0 | `auto-speech-narrate-config.md:33` |
| 58 | Narrate | `narrate config edit` | Open narrator config in system editor | `argv=["narrate", "config", "edit"]` | Invokes `$EDITOR` on config file | Exit 0 | `auto-speech-narrate-config.md:37` |
| 59 | Narrate | `narrate config` (Invalid) | Invalid action for narrate config | `argv=["narrate", "config", "delete"]` | stderr: `config: unknown action delete...` | Exit 2 | `auto-speech-narrate-config.md:15` |
| 60 | Hook | `hook stop` (Claude Code) | Execute Stop hook logic for Claude Code payload | `argv=["hook", "stop"]`, `stdin='{"hook_event_name": "Stop", "session_id": "sess-1", "transcript_path": "/p"}'` | Worker triggered / beacon updated | Exit 0 | `autoplay_hook.sh:27` |
| 61 | Hook | `hook stop` (Antigravity) | Execute Stop hook for Antigravity payload | `argv=["hook", "stop"]`, `stdin='{"hook_event_name": "Stop", "conversationId": "conv-1", "transcriptPath": "/p"}'` | Handles conversationId and transcriptPath | Exit 0 | `PROJECT.md` Feature 19 |
| 62 | Hook | `hook stop` (Muted/Unenrolled) | Stop hook ignores muted or unenrolled session | `stdin` for unenrolled session | Bails early, no worker spawned | Exit 0 | `autoplay_hook.sh:53,82` |
| 63 | Hook | `hook stop` (Suppress Guard) | Stop hook ignores nested claude invocations | `env={"AUTO_SPEECH_SUPPRESS_HOOKS": "1"}` | Bails early | Exit 0 | `autoplay_hook.sh:47` |
| 64 | Hook | `hook narrator` (PreToolUse) | Ingest lifecycle tool event into JSONL | `argv=["hook", "narrator"]`, `stdin='{"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "s"}'` | Decorated event appended to `/tmp/...events.jsonl` | Exit 0 | `narrator_hook.sh:17` |
| 65 | Hook | `hook narrator` (Antigravity) | Ingest event with `conversationId` | `stdin='{"hook_event_name": "PostToolUse", "conversationId": "c"}'` | Handles `conversationId` mapping | Exit 0 | `PROJECT.md` Feature 19 |
| 66 | Hook | `hook narrator` (Gate Bail) | Narrator hook ignores unenrolled session | `stdin` for unenrolled session | Bails early, no event written | Exit 0 | `narrator_hook.sh:44` |
| 67 | Hook | `hook bootstrap` | SessionStart dependency sync check | `argv=["hook", "bootstrap"]` | Evaluates lock hash vs stamp | Exit 0 | `setup/bootstrap.sh:33` |
| 68 | Hook | `hook bootstrap --force` | Force dependency synchronization | `argv=["hook", "bootstrap", "--force"]` | Runs sync unconditionally | Exit 0 on sync success | `setup/bootstrap.sh:27` |
| 69 | Doctor | Healthy System Check | Run all health probes when system is healthy | `argv=["doctor"]` | stdout human report with `Summary: OK` | Exit 0 | `doctor.py:55` |
| 70 | Doctor | JSON Output Flag | Output health report formatted as JSON | `argv=["doctor", "--json"]` | stdout valid JSON matching schema | Exit 0 (if healthy) | `doctor.py:276` |
| 71 | Doctor | Unhealthy Probe Failure | Return exit code 1 when any critical probe fails | `argv=["doctor"]` with failed disk / mpv probe | stdout reports FAIL; exit 1 | Exit 1 | `doctor.py:286`, `tests/test_doctor.py` |
| 72 | Update | Dependency Update Check | Synchronize venv dependencies to uv.lock | `argv=["update"]` | Runs bootstrap sync logic | Exit 0 | `setup/bootstrap.sh` |
| 73 | Update | Forced Update | Force `uv sync` regardless of lock match | `argv=["update", "--force"]` | Passes `--force` to bootstrap logic | Exit 0 | `auto-speech-update.md:20` |
| 74 | Subcommand Help | Subcommand `--help` (all 12) | Verify `--help` displays syntax and options for every subcommand | `argv=[subcmd, "--help"]` for each of 12 subcommands | stdout contains subcommand synopsis | Exit 0 | `PROJECT.md` § Interface Contracts |

---

## Edge Cases

| # | Feature | Input | Observed Behavior |
|---|---------|-------|-------------------|
| 1 | `speak` | Text supplied as both positional arguments and non-empty stdin | Positional arguments are spoken; stdin is safely ignored or unread. |
| 2 | `speak` | Stdin contains only whitespace (`"   \n\t  "`) with no positional args | Treated as empty input: exits with code 2 and diagnostic error on stderr. |
| 3 | `speak` | Large text input (>50,000 characters) via stdin | Text is read completely without buffer truncation and passed to client. |
| 4 | `speak` | Text with Unicode emojis, special symbols, and accents (`"Speech test: 🚀 Café, naïve & €100"`) | Forwarded without UTF-8 encoding or decoding errors. |
| 5 | `speak` | `--source-hash` with invalid uppercase hex (`"AABB..."` instead of lowercase) | Normalized via `.strip().lower()` to 64 lowercase hex chars or rejected if non-hex. |
| 6 | `speak` | `--source-hash` with invalid length (e.g. 63 or 65 characters) | Rejects with exit 2 and stderr message `"speak: --source-hash must be 64 hex chars"`. |
| 7 | `replay` | Non-numeric string provided to `--ordinal` (e.g. `--ordinal abc`) | Argparse catches invalid int, emits error on stderr, exits with code 2. |
| 8 | `replay` | Cache directory does not exist or has zero WAV files | Exits with code 2 and stderr `"replay: no cache entries found"`. |
| 9 | `replay` | Ordinal requested is larger than available entry count (e.g. cache has 1, requested 2) | Exits with code 2 and stderr `"replay: only 1 cache entries; you asked for #2"`. |
| 10 | `replay` | Subprocess or NativeAudioSink raises `AudioSinkError` | Exits with code 6 (`EXIT_PLAYBACK_FAIL`) and stderr `"replay: playback failed"`. |
| 11 | `replay` | SIGINT / `KeyboardInterrupt` during audio playback | Audio sink is interrupted; exits with code 130 (`EXIT_INTERRUPTED`). |
| 12 | `pause`/`resume`/`stop`/`restart` | No mpv socket and no active daemon playback session | Exits with code 2 (`EXIT_NO_SESSION`) and stderr `"control: no active playback session"`. |
| 13 | `seek` | Target string is whitespace only (`"   "`) when session exists | Exits with code 4 (`EXIT_BAD_ARG`) and stderr `"control: seek requires a target"`. |
| 14 | `seek` | Target string missing completely (`auto-speech seek`) | Argparse catches missing positional argument, exits with code 2. |
| 15 | `autoplay on` | Invoked in environment where `CLAUDE_CODE_SESSION_ID` is unset and `--session-id` omitted | Exits with code 1 and stderr `"autoplay on: no session ID provided to enroll"`. |
| 16 | `autoplay on` | Enrolling session that is already enrolled | Returns code 0 cleanly (idempotent; marker untouched). |
| 17 | `autoplay off` | Withdrawing session when marker file does not exist | Returns code 0 cleanly (idempotent). |
| 18 | `autoplay mode` | Setting unknown mode string (e.g. `"fast"`, `"draft"`) | Exits with code 2 and stderr `"autoplay-mode: unknown value fast (expected raw \| verbatim \| small \| medium \| large)"`. |
| 19 | `autoplay mode` | User config file directory `~/.config/auto-speech` does not exist prior to set | Automatically creates parent directory `mkdir -p` and writes `autoplay.toml`. |
| 20 | `autoplay scope` | Setting `solo` when no session ID can be resolved | Exits with code 1 and stderr `"scope solo requires a session id"`. |
| 21 | `autoplay scope` | Setting `solo` for valid session ID | Writes session ID to `auto-speech-autoplay-solo` AND auto-enrolls session in `SessionEnrollment`. |
| 22 | `autoplay scope` | Setting `all` when solo marker file is already absent | Returns code 0 cleanly (idempotent). |
| 23 | `narrate on` | Invoked with legacy per-project `.claude/narrate.enabled` present | Creates per-session marker AND unlinks legacy project marker. |
| 24 | `narrate config` | Unrecognized action argument (e.g. `"reset"`, `"remove"`) | Exits with code 2 and stderr `"config: unknown action reset (expected show\|edit\|path)"`. |
| 25 | `hook stop` | Stdin payload is completely empty or invalid JSON string (`"not a json"`) | Safely bails without uncaught Python exception; exits 0 so caller shell does not abort. |
| 26 | `hook stop` | Payload has `conversationId` instead of `session_id` (Antigravity payload) | Extracts `conversationId` correctly and evaluates enrollment under that ID. |
| 27 | `hook stop` | Payload has `transcriptPath` instead of `transcript_path` | Extracts `transcriptPath` correctly and hands to worker. |
| 28 | `hook narrator` | Event payload contains unknown event name or missing tool name | Decorates and appends valid JSONL record or handles gracefully without crash. |
| 29 | `doctor` | Invoked with `--json` when a probe returns FAIL | Exits with code 1 and emits valid JSON with `"healthy": false`. |
| 30 | `bin/auto-speech` | Trampoline executed via relative symlink (`~/.local/bin/auto-speech`) | Successfully resolves link target to locate true project root and `.venv`. |

---

## Detailed Test Specification Matrix for `tests/test_cli_dispatcher.py`

The test suite will be structured as standard Python `unittest.TestCase` classes requiring **no runtime dependencies** (no MLX, no mpv, no live audio hardware, no live network).

### Test Suite Structure

```
tests/test_cli_dispatcher.py
├── TestCliHelpAndRouting
│   ├── test_top_level_help_exits_zero_and_lists_subcommands
│   ├── test_top_level_missing_subcommand_exits_two
│   ├── test_top_level_invalid_subcommand_exits_two
│   └── test_subcommand_help_for_all_12_commands
├── TestCliSpeak
│   ├── test_speak_positional_arguments_success
│   ├── test_speak_multiple_positional_tokens_joined
│   ├── test_speak_piped_stdin_success
│   ├── test_speak_positional_precedence_over_stdin
│   ├── test_speak_empty_input_exits_two
│   ├── test_speak_whitespace_only_stdin_exits_two
│   ├── test_speak_source_hash_cache_playback
│   ├── test_speak_invalid_source_hash_exits_two
│   ├── test_speak_global_mute_clean_exit_zero
│   ├── test_speak_priority_and_session_forwarding
│   └── test_speak_daemon_unreachable_exits_one
├── TestCliReplay
│   ├── test_replay_default_ordinal_one_success
│   ├── test_replay_explicit_ordinal_success
│   ├── test_replay_zero_or_negative_ordinal_exits_two
│   ├── test_replay_empty_cache_exits_two
│   ├── test_replay_ordinal_out_of_bounds_exits_two
│   ├── test_replay_audio_sink_error_exits_six
│   ├── test_replay_keyboard_interrupt_exits_130
│   └── test_replay_global_mute_clean_exit_zero
├── TestCliTransportControls
│   ├── test_pause_no_session_exits_two
│   ├── test_resume_no_session_exits_two
│   ├── test_stop_no_session_exits_two
│   ├── test_restart_no_session_exits_two
│   ├── test_seek_no_session_exits_two
│   ├── test_seek_missing_target_exits_two
│   ├── test_seek_empty_whitespace_target_exits_four
│   ├── test_transport_active_session_pause_resume_stop_restart
│   └── test_transport_active_session_seek_success
├── TestCliAutoplay
│   ├── test_autoplay_on_with_explicit_session_id
│   ├── test_autoplay_on_with_env_session_id
│   ├── test_autoplay_on_missing_session_id_fails
│   ├── test_autoplay_on_idempotent
│   ├── test_autoplay_off_with_session_id
│   ├── test_autoplay_off_idempotent
│   ├── test_autoplay_status_output
│   ├── test_autoplay_mode_show
│   ├── test_autoplay_mode_set_valid_modes
│   ├── test_autoplay_mode_set_invalid_mode_fails
│   ├── test_autoplay_scope_show
│   ├── test_autoplay_scope_set_all
│   ├── test_autoplay_scope_set_solo_with_session
│   ├── test_autoplay_scope_set_solo_missing_session_fails
│   └── test_autoplay_scope_set_invalid_scope_fails
├── TestCliNarrate
│   ├── test_narrate_on_with_session_id_creates_marker_and_starts_daemon
│   ├── test_narrate_on_missing_session_id_fails
│   ├── test_narrate_off_removes_marker
│   ├── test_narrate_status_output
│   ├── test_narrate_stop_terminates_daemon
│   ├── test_narrate_config_show
│   ├── test_narrate_config_path
│   ├── test_narrate_config_edit
│   └── test_narrate_config_invalid_action_fails
├── TestCliHook
│   ├── test_hook_stop_claude_code_payload
│   ├── test_hook_stop_antigravity_payload
│   ├── test_hook_stop_muted_bails_early
│   ├── test_hook_stop_suppressed_bails_early
│   ├── test_hook_stop_malformed_json_bails_cleanly
│   ├── test_hook_narrator_tool_event_appended
│   ├── test_hook_narrator_antigravity_event
│   ├── test_hook_narrator_unenrolled_session_bails_early
│   ├── test_hook_bootstrap_checks_and_syncs
│   └── test_hook_bootstrap_forced
├── TestCliDoctorAndUpdate
│   ├── test_doctor_healthy_system_text_output
│   ├── test_doctor_json_output_flag
│   ├── test_doctor_unhealthy_probe_exits_one
│   ├── test_update_calls_bootstrap
│   └── test_update_forced_flag
└── TestCliTrampolineIntegration
    └── test_bin_trampoline_help_invocation
```

---

## Hermetic Test Harness Architecture

To guarantee hermetic execution without contaminating developer files (`~/.claude/` or `~/.config/auto-speech/`):

1. **Virtual Filesystem Fixture**:
   Every test creates an isolated `tempfile.TemporaryDirectory()`.
   Sets:
   - `HOME = tmpdir / "home"`
   - `XDG_CONFIG_HOME = tmpdir / "home" / ".config"`
   - `AUTO_SPEECH_DAEMON_SOCK = str(tmpdir / "daemon.sock")`
   - `AUTO_SPEECH_AUTOPLAY_CONFIG = str(tmpdir / "autoplay.toml")`
   - `AUTO_SPEECH_NARRATOR_CONFIG = str(tmpdir / "narrator.toml")`

2. **Standard I/O Redirection**:
   Invocations call `cli.main(argv)` directly in-process.
   `sys.stdin` is redirected using `io.StringIO(stdin_content)`.
   `sys.stdout` and `sys.stderr` are captured using `io.StringIO()`.

3. **In-Memory Mocking Boundaries**:
   - `DaemonClient`: mock `DaemonClient.speak()` and `DaemonClient.play_cache()`.
   - `NativeAudioSink`: mock `NativeAudioSink.play()`.
   - `MpvIpc`: mock socket send/receive.
   - `Doctor`: inject mocked `Doctor` probes (mock `which`, mock `disk_usage`).

4. **Integration with `tests/run_all.sh`**:
   `tests/test_cli_dispatcher.py` will be registered in `CHEAP` tests in `tests/run_all.sh`.
   Runs on bare Python 3.12 with standard library only.
