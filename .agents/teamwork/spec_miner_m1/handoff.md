# Handoff Report: Specification Mining for Milestone M1 (`tests/test_cli_dispatcher.py`)

**Agent**: `teamwork_preview_spec_miner` (`spec_miner_m1`)  
**Working Directory**: `/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m1`  
**Milestone**: M1 (Unified `auto-speech` CLI Dispatcher)  
**Date**: 2026-10-06  

---

## 1. Observation

1. **Architecture & Scope**:
   - `PROJECT.md:4`–`6`:
     > "1. Unified CLI Surface: Single executable command-line dispatcher auto-speech linked into ~/.local/bin/auto-speech, routing to all subsystem functions (speech, replay, transport, autoplay, narrate, hooks, diagnostics, updates)."
   - `PROJECT.md:77`–`96`:
     > "Subcommands:
     > - speak [text ...] [--source-hash H] [--session-id ID] [--priority P] [--voice V] [--speed S]
     > - replay [--ordinal N]
     > - pause, resume, stop, restart
     > - seek TARGET
     > - autoplay {on, off, status, mode, scope}
     > - narrate {on, off, status, stop, config}
     > - hook {stop, narrator, bootstrap}
     > - doctor [--json]
     > - update [--force]
     > Exit Codes:
     > - 0: Success (or cleanly muted)
     > - 1: General failure / daemon unreachable / doctor FAIL
     > - 2: No active playback session / invalid input / missing target
     > - 3: mpv IPC failure
     > - 4: Invalid target argument
     > - 6: Playback failure
     > - 130: Interrupted (SIGINT)"
   - `PROJECT.md:116`:
     > "- tests/test_cli_dispatcher.py: New unit and integration tests for CLI dispatcher."

2. **Subcommand Implementations & Ground Truth Contracts**:
   - **`speak`** (`plugin/scripts/python/speak.py:151`–`160`):
     - Validates `--source-hash` length 64 and hex characters: `if len(sh) != 64 or any(c not in "0123456789abcdef" for c in sh): ... return 2`.
     - Checks stdin text (`speak.py:161`): `transcript_text = sys.stdin.read()`.
     - Checks global mute (`plugin/scripts/python/autoplay_gate.py:54`–`60`): `AutoplayGate().worker_gated_off()`.
   - **`replay`** (`plugin/scripts/python/replay.py:47`–`64`):
     - Validates ordinal >= 1: `if args.ordinal < 1: ... return 2`.
     - Empty cache check: `if not entries: ... return EXIT_NO_CACHE_ENTRY` (exit 2).
     - Ordinal out of bounds: `if args.ordinal > len(entries): ... return EXIT_NO_CACHE_ENTRY` (exit 2).
     - Playback failure (`replay.py:81`): `return EXIT_PLAYBACK_FAIL` (exit 6).
     - KeyboardInterrupt (`replay.py:84`): `return EXIT_INTERRUPTED` (exit 130).
   - **Transport Controls** (`plugin/scripts/python/control.py:8`–`46`):
     - `EXIT_OK = 0`, `EXIT_NO_SESSION = 2`, `EXIT_IPC_FAIL = 3`, `EXIT_BAD_ARG = 4`.
     - Active session check: `_ensure_session()` returns `EXIT_NO_SESSION` (2) when no active session.
     - Seek validation (`control.py:42`–`45`): `target = args.target.strip()`; if empty returns `EXIT_BAD_ARG` (4).
   - **`autoplay`** (`plugin/scripts/python/autoplay_enrollment.py:65`–`87`, `autoplay_scope.py:68`–`86`, `auto-speech-autoplay-mode.md:11`–`18`):
     - `on`: Enrolls session via marker `~/.claude/auto-speech-autoplay-enabled/<session_id>`. Missing session ID exits 1.
     - `off`: Unlinks marker file; idempotent.
     - `mode`: Accepts `raw`, `verbatim`, `small`, `medium`, `large`. Unknown value exits non-zero (exit 2). Empty shows current mode.
     - `scope`: Accepts `all`, `solo` (with session ID). Empty shows current scope. Setting `solo` without session ID exits 1.
   - **`narrate`** (`plugin/commands-extra/auto-speech-narrate-*.md`, `narrator_config.py:35`–`70`):
     - `on`: Creates marker in `~/.claude/auto-speech-narrate-sessions/<session_id>` and starts daemon. Missing ID exits 1.
     - `off`: Removes marker.
     - `config`: Subactions `show`, `path`, `edit`. Unknown action exits 2.
   - **`hook`** (`plugin/scripts/shell/autoplay_hook.sh:34`–`85`, `narrator_hook.sh:21`–`50`):
     - `stop`: Accepts Claude Code payload (`session_id`, `transcript_path`) and Antigravity payload (`conversationId`, `transcriptPath`). Respects global mute and `AUTO_SPEECH_SUPPRESS_HOOKS=1`.
     - `narrator`: Accepts `PreToolUse`/`PostToolUse` event payloads, appends to `/tmp/auto-speech-narrator-events.jsonl` when session is enrolled.
     - `bootstrap`: Reconciles venv with `uv.lock`.
   - **`doctor` & `update`** (`plugin/scripts/python/doctor.py:275`–`286`, `setup/bootstrap.sh:26`–`35`):
     - `doctor`: Supports `--json` flag; exits 0 on healthy and 1 on check failure.
     - `update`: Supports `--force` flag.

3. **Current State of Repository & Worktree**:
   - `bin/auto-speech` does not yet exist.
   - `plugin/scripts/python/cli.py` does not yet exist.
   - `tests/test_cli_dispatcher.py` does not yet exist.
   - `tests/run_all.sh` runs cheap tests using standard library `unittest` directly without pytest.

---

## 2. Logic Chain

1. From Observation 1, Milestone M1 requires building a unified CLI dispatcher `bin/auto-speech` and `cli.py` supporting 12 distinct subcommands with standardized exit codes (0, 1, 2, 3, 4, 6, 130).
2. From Observation 2, every underlying subsystem already possesses strictly defined behavior, exit codes, and argument constraints implemented across existing modules (`speak.py`, `replay.py`, `control.py`, `autoplay_enrollment.py`, `autoplay_scope.py`, `doctor.py`, `autoplay_hook.sh`, `narrator_hook.sh`).
3. Therefore, testing `tests/test_cli_dispatcher.py` must exhaustively assert:
   - Command-line argument parsing and help output for the root CLI and all 12 subcommands.
   - Forwarding of text arguments, stdin streams, and flags to underlying handlers.
   - Handling of empty inputs, invalid formats, missing session IDs, and absent targets according to the specified exit code contract.
   - Preservation of global mute semantics (exiting 0 cleanly without synthesizing or playing audio).
4. From Observation 3, the test harness must be implemented in pure Python using `unittest` (compatible with `tests/run_all.sh` cheap lane) and run 100% hermetically by virtualizing the filesystem (`HOME`, `XDG_CONFIG_HOME`, `TMPDIR`) and mocking socket/subprocess/audio dependencies.

---

## 3. Caveats

- **No live audio or daemon requirement**: Tests in `tests/test_cli_dispatcher.py` must NOT depend on running daemons or audio devices. All daemon interactions and audio sinks must be tested via mock facades (`mock.patch("cli.DaemonClient")` / `mock.patch("replay.NativeAudioSink")`).
- **Trampoline integration test**: Executing `bin/auto-speech --help` as a subprocess requires that `bin/auto-speech` have execute permissions (`chmod +x bin/auto-speech`). In unit testing, tests should primarily drive `cli.main(argv)` directly in-process for speed and deterministic coverage, with one integration test for the bash trampoline.
- **No caveats** regarding interface contracts; all 12 subcommands and exit codes are fully mapped and documented.

---

## 4. Conclusion

A complete, granular specification of 74 test cases and 30 edge cases has been mined and documented in `/Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m1/analysis.md`. The implementer (`worker_m1`) can directly construct `tests/test_cli_dispatcher.py` following the test classes, mock boundaries, and assertion contracts defined in this report.

---

## 5. Verification Method

To verify the mined specification against the target worktree:

1. **Inspect Analysis Report**:
   ```bash
   cat /Users/joshua/Developer/auto-speech/.agents/teamwork/spec_miner_m1/analysis.md
   ```
2. **Verify Baseline Test Suite Passes in Target Worktree**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree && bash tests/run_all.sh
   ```
3. **Verify Hermetic Isolation Feasibility**:
   Check that `tests/test_replay_control.py`, `tests/test_speak_client.py`, and `tests/test_doctor.py` execute cleanly without external dependencies:
   ```bash
   .venv/bin/python tests/test_replay_control.py
   .venv/bin/python tests/test_speak_client.py
   .venv/bin/python tests/test_doctor.py
   ```
4. **Invalidation Conditions**:
   - Any departure in `cli.py` from the 12 enumerated subcommands or standard exit codes (0, 1, 2, 3, 4, 6, 130).
   - Any test that modifies real user files under `~/.claude/` or `~/.config/auto-speech/`.
