# Worker Handoff Report: Milestone 1 Bugfixes (`worker_m1_fix`)

**Date**: 2026-10-06T21:56:30Z  
**Target Repository / Worktree**: `/Users/joshua/Developer/auto-speech-worktree`  
**Agent**: `teamwork_preview_worker` (`worker_m1_fix`)  
**Assigned Task**: Apply 3 bugfixes in `plugin/scripts/python/cli.py` and expand `tests/test_cli_dispatcher.py`.

---

## 1. Observation

### 1.1 Pre-Fix Reproduction
1. **Seek Target Syntax Validation**:
   - Running `./bin/auto-speech seek foo` returned exit code `2` with stderr: `control: no active playback session (unified daemon uses synchronous audio).`
   - Root cause in `cli.py`: `handle_seek` evaluated `_has_active_session()` before parsing or validating `args.target`. When no session was active, invalid arguments exited with code `2` (`EXIT_NO_SESSION`) rather than the contractually required code `4` (`EXIT_BAD_ARG`).
2. **Speak Stdin TTY Blocking with `--source-hash`**:
   - Running `./bin/auto-speech speak --source-hash <hash>` on an interactive terminal unconditionally called `sys.stdin.read()`, blocking indefinitely waiting for EOF on stdin before ever checking cache.
   - Running `./bin/auto-speech speak` with no arguments on an interactive terminal similarly hung waiting for input instead of failing fast with code `2`.
3. **Hook Non-Dict JSON Payloads**:
   - Running `echo "[1, 2, 3]" | ./bin/auto-speech hook stop` produced an unhandled `AttributeError: 'list' object has no attribute 'get'` and terminated with exit code `1`.
   - Running `echo "[1, 2, 3]" | ./bin/auto-speech hook narrator` produced an identical traceback and exit code `1`.
   - Non-dict JSON literals (`42`, `null`, `"string"`, `true`) were parsed by `json.loads` without error, bypassing the `try-except` block and crashing on downstream `.get()` lookups.

### 1.2 Implemented Changes
In `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/cli.py`:
1. **`import math`**: Added to standard library imports.
2. **`handle_seek`**:
   - Reordered validation logic to check `args.target` syntax first (`empty`, `end`, `+N`, `-N`, `N` with finite float checks via `math.isfinite()`).
   - If invalid, prints `control: seek requires a target (+N, -N, N, or 'end')` to `sys.stderr` and returns `EXIT_BAD_ARG` (`4`) immediately.
   - If valid, checks `_has_active_session()` and returns `EXIT_NO_SESSION` (`2`) if no playback is active.
3. **`handle_speak`**:
   - Added `is_tty` detection via `sys.stdin.isatty()`.
   - When `args.words` is empty and stdin is a TTY:
     - If `args.source_hash` is present: sets `text = ""` without reading stdin, immediately routing to cache lookup/playback.
     - If `args.source_hash` is absent: prints error to `sys.stderr` and returns `EXIT_INVALID_INPUT` (`2`) without blocking.
   - When stdin is not a TTY (piped/redirected): preserves full `sys.stdin.read()` reading behavior.
4. **`handle_hook`**:
   - In `hook stop`: after `json.loads()`, added `if not isinstance(payload, dict): payload = {}` and converted `session_id` and `transcript_path` to strings safely. Exits cleanly with code `0`.
   - In `hook narrator`: after `json.loads()`, added `if not isinstance(payload, dict): payload = {}`, converted `session_id` and `event` to strings safely. Exits cleanly with code `0`.

In `/Users/joshua/Developer/auto-speech-worktree/tests/test_cli_dispatcher.py`:
1. Added 4 TTY and piped stdin tests in `TestCliSpeak`:
   - `test_speak_tty_source_hash_triggers_cache_without_reading_stdin`
   - `test_speak_tty_no_words_no_hash_exits_two_without_reading_stdin`
   - `test_speak_piped_non_tty_with_text_and_source_hash_uses_piped_text`
   - `test_speak_piped_non_tty_empty_with_source_hash_triggers_cache`
2. Updated and expanded seek validation tests in `TestCliTransportControls`:
   - `test_seek_no_session_valid_target_exits_two` (tests `+15`, `-10`, `30`, `end` when session inactive)
   - `test_seek_missing_target_exits_two` (missing argument exits 2)
   - `test_seek_empty_whitespace_target_unmocked_exits_four` (empty/whitespace exits 4 unmocked)
   - `test_seek_invalid_target_unmocked_exits_four` (invalid syntax `foo`, `+abc`, `nan`, etc. exits 4 unmocked)
   - `test_seek_invalid_target_even_when_session_inactive_mocked_exits_four` (exits 4 even with mocked inactive session)
3. Added 4 hook defense tests in `TestCliHook`:
   - `test_hook_stop_non_dict_json_payloads_bail_cleanly`
   - `test_hook_stop_missing_keys_bails_cleanly`
   - `test_hook_narrator_non_dict_json_payloads_bail_cleanly`
   - `test_hook_narrator_missing_keys_bails_cleanly`
4. Added trampoline integration test in `TestCliTrampolineIntegration`:
   - `test_bin_hook_non_dict_stdin_exit_zero` (runs `bin/auto-speech hook stop` and `bin/auto-speech hook narrator` with `[1, 2, 3]`, `42`, `null`, `"foo"`, `true` via subprocess).

---

## 2. Logic Chain

1. **Seek Ordering**: Syntax validation operates purely on the supplied command line tokens and requires zero hardware or IPC state. Prioritizing syntax validation ensures that malformed commands fail with parameter validation errors (`EXIT_BAD_ARG = 4`) regardless of playback state, satisfying the specification in `PROJECT.md §Interface Contracts`.
2. **Speak TTY Stdin Handling**: `sys.stdin.read()` blocks until EOF when connected to a terminal. Because users providing `--source-hash` intend to replay or synthesize content identified by hash rather than typing standard input, avoiding `sys.stdin.read()` on interactive terminals eliminates terminal hangs while preserving piped stdin workflows (`echo "hello" | auto-speech speak`).
3. **Hook Robustness**: External lifecycle hooks (Claude Code and Antigravity) pipe event data into standard input. When non-standard, malformed, or non-object JSON values arrive, falling back to `{}` guarantees that `.get()` calls succeed and that the hooks complete silently with exit code `0` without corrupting terminal state or raising tracebacks.

---

## 3. Caveats

1. **Directly Running Seek Without Arguments**: Running `./bin/auto-speech seek` (zero arguments) is rejected by `argparse` because `target` is a positional required argument. `argparse` emits a usage error and returns exit code `2`. This is expected and compliant with `PROJECT.md` line 91 ("`2`: missing target").
2. **Piped JSON to Hook Narrator with Existing Session ID in Environment**: If `CLAUDE_CODE_SESSION_ID` is defined in the ambient environment and points to an enrolled narration session, passing a non-dict JSON body will still record a narration event with `"event": "unknown"` and `"payload": {}`. This is intentional fallback behavior.

---

## 4. Conclusion

All 3 defects have been fully resolved using minimal, genuine implementations:
- `handle_seek` prioritizes target syntax validation over session checks, returning exit code 4 on invalid targets.
- `handle_speak` detects TTY stdin and defaults text to `""` when `--source-hash` is present, avoiding terminal hangs.
- `handle_hook` sanitizes parsed JSON payloads to guarantee dictionary types, exiting 0 cleanly without tracebacks.
- Unit test coverage in `tests/test_cli_dispatcher.py` expanded from 74 tests to 84 tests, with all 84 tests passing.
- The complete regression suite (`bash tests/run_all.sh`) passes all 50/50 test suites.
- Code quality checks (`ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py`) report 0 violations.

---

## 5. Verification Method

To independently verify all changes in `/Users/joshua/Developer/auto-speech-worktree`:

1. **Direct Seek Exit Code Verification**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   ./bin/auto-speech seek foo
   echo "Exit code: $?"   # Expected: 4
   ```

2. **Direct Hook Non-Dict JSON Smoke Test**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   echo "[1, 2, 3]" | ./bin/auto-speech hook stop
   echo "Exit code: $?"   # Expected: 0
   echo "[1, 2, 3]" | ./bin/auto-speech hook narrator
   echo "Exit code: $?"   # Expected: 0
   ```

3. **Expanded Unit Test Suite**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/python -m unittest tests/test_cli_dispatcher.py
   # Expected: Ran 84 tests in ~0.7s, OK
   ```

4. **Full 50-Suite Regression Suite**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   bash tests/run_all.sh
   # Expected: ran: 50, failed: 0, all tests passed
   ```

5. **Ruff Linter Check**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   .venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py
   # Expected: All checks passed!
   ```
