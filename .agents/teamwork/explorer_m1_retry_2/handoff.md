# Handoff Report: Investigation and Fix Strategy for `handle_speak` Stdin Blocking & Input Resolution

**Date**: 2026-10-06T21:46:15Z  
**Author**: `teamwork_preview_explorer` (`explorer_m1_retry_2`)  
**Target Repository**: `/Users/joshua/Developer/auto-speech-worktree`  
**Target Files**:
- `plugin/scripts/python/cli.py`
- `tests/test_cli_dispatcher.py`

---

## 1. Observation

### 1.1 Unconditional `sys.stdin.read()` Blocking on Interactive Terminals
In `/Users/joshua/Developer/auto-speech-worktree/plugin/scripts/python/cli.py` lines 112–116 inside `handle_speak`:
```python
112:     if args.words:
113:         text = " ".join(args.words)
114:     else:
115:         text = sys.stdin.read() if sys.stdin else ""
116: 
117:     if not text.strip():
118:         if args.source_hash:
```
When `args.words` is empty (e.g. running `./bin/auto-speech speak --source-hash <hash>` or `./bin/auto-speech speak`):
- `sys.stdin` is the standard terminal stream (`sys.__stdin__`), where `sys.stdin.isatty()` evaluates to `True`.
- Line 115 unconditionally invokes `sys.stdin.read()`.
- On interactive terminals, `sys.stdin.read()` blocks the process indefinitely waiting for standard input EOF (Ctrl-D).
- Consequently:
  1. `./bin/auto-speech speak --source-hash <64_hex_chars>` never reaches line 118 to look up and play cached audio.
  2. `./bin/auto-speech speak` (without arguments) hangs indefinitely instead of immediately failing with exit code 2 (`EXIT_INVALID_INPUT`).

### 1.2 Test Suite Masking via `io.StringIO`
In `tests/test_cli_dispatcher.py`:
- All existing tests in `TestCliSpeak` (lines 145, 154, 163, 173, 184) mock `sys.stdin` using `io.StringIO(...)`:
  ```python
  180:     def test_speak_source_hash_cache_playback(self) -> None:
  ...
  184:         with mock.patch("sys.stdin", io.StringIO("")):
  185:             rc = cli.main(["speak", "--source-hash", valid_hash], client=mock_client)
  ```
- Because `io.StringIO("").isatty()` returns `False` and `.read()` returns `""` immediately without blocking, all automated tests simulated piped/redirected input and succeeded. There were zero tests exercising interactive terminal behavior (`sys.stdin.isatty() == True`).

### 1.3 Related Finding in `handle_seek`
As noted by `challenger_m1_1` in `/Users/joshua/Developer/auto-speech/.agents/teamwork/challenger_m1_1/handoff.md`:
In `plugin/scripts/python/cli.py` lines 296–308:
```python
296: def handle_seek(args: argparse.Namespace) -> int:
297:     if not _has_active_session():
298:         print(
299:             "control: no active playback session (unified daemon uses synchronous audio).",
300:             file=sys.stderr,
301:         )
302:         return EXIT_NO_SESSION
303: 
304:     target = args.target.strip()
...
```
`handle_seek` checks `_has_active_session()` on line 297 *before* validating `args.target`. When no playback session is active, `./bin/auto-speech seek foo` returns exit code 2 instead of the contractually required code 4 (`EXIT_BAD_ARG`).

---

## 2. Logic Chain

1. **Premise**: Per PROJECT.md §Interface Contracts and Dispatch Requirements:
   - When `args.words` is empty and `--source-hash` is provided on an interactive terminal (`sys.stdin.isatty() == True`), text must default to `""` without reading stdin, immediately triggering cache lookup.
   - When `sys.stdin` is piped (not a TTY), `sys.stdin.read()` must be called. If stdin text is non-empty, it must be spoken (with `--source-hash` passed if provided); if empty, it proceeds to `--source-hash` cache lookup.
   - When neither words nor `--source-hash` are provided and stdin is a TTY, `handle_speak` must print `"speak: no text provided on command line or stdin"` to `sys.stderr` and return `EXIT_INVALID_INPUT` (2) immediately without blocking on `sys.stdin.read()`.
   - When positional words are provided (`args.words`), positional tokens take absolute precedence and stdin must not be read.
2. **TTY Detection Invariant**:
   - `sys.stdin` may be `None` (in daemon or detached contexts) or may be a mocked stream without `isatty()`.
   - Safe TTY detection must be guarded:
     ```python
     is_tty = False
     if sys.stdin is not None:
         try:
             is_tty = bool(sys.stdin.isatty())
         except (AttributeError, ValueError, OSError):
             is_tty = False
     ```
3. **Execution Decision Tree**:
   - `if args.words`:
     `text = " ".join(args.words)` (stdin untouched).
   - `elif is_tty`:
     - `if args.source_hash`:
       `text = ""` (stdin untouched; allows `not text.strip()` to trigger cache lookup on line 118).
     - `else`:
       Print `"speak: no text provided on command line or stdin"` to stderr and return `EXIT_INVALID_INPUT` (2) immediately without touching stdin.
   - `else`:
     `text = sys.stdin.read() if sys.stdin else ""`
     - If `text.strip()` is non-empty: speaks `text`.
     - If `text.strip()` is empty:
       - `if args.source_hash`: triggers cache lookup.
       - `else`: prints error to stderr and returns `EXIT_INVALID_INPUT` (2).
4. **Impact Analysis**:
   - Existing piped workflows (`echo "text" | auto-speech speak`, `cat file | auto-speech speak`) continue to read stdin normally because `is_tty` is `False`.
   - Existing unit tests pass unmodified because `io.StringIO` has `isatty() == False`.
   - Interactive terminal calls with `--source-hash` play cache instantly.
   - Interactive terminal calls without arguments fail fast with clear diagnostics and code 2 instead of freezing the terminal.

---

## 3. Caveats

1. **Mocking in Unit Tests**: When unit testing TTY behavior, using `mock.MagicMock()` for `sys.stdin` automatically gives `.isatty()` a MagicMock return value, which evaluates to `True` in boolean context. Tests testing non-TTY/piped behavior should continue using `io.StringIO` (where `isatty() == False`).
2. **`handle_seek` Dependency**: While the dispatch prompt specifically requests the fix strategy for `handle_speak`, the developer implementer should also apply the trivial re-ordering in `handle_seek` (moving target parsing before `_has_active_session()`) to ensure the challenger's entire regression gate passes.

---

## 4. Conclusion & Actionable Fix Strategy

### Recommendation A: Exact Drop-In Patch for `plugin/scripts/python/cli.py` (`handle_speak`)

Replace lines 112–116 in `plugin/scripts/python/cli.py`:

```python
<<<<<<< BEFORE
    if args.words:
        text = " ".join(args.words)
    else:
        text = sys.stdin.read() if sys.stdin else ""
=======
    is_tty = False
    if sys.stdin is not None:
        try:
            is_tty = bool(sys.stdin.isatty())
        except (AttributeError, ValueError, OSError):
            is_tty = False

    if args.words:
        text = " ".join(args.words)
    elif is_tty:
        if args.source_hash:
            text = ""
        else:
            print("speak: no text provided on command line or stdin", file=sys.stderr)
            return EXIT_INVALID_INPUT
    else:
        text = sys.stdin.read() if sys.stdin else ""
>>>>>>> AFTER
```

### Recommendation B: Exact Drop-In Patch for `plugin/scripts/python/cli.py` (`handle_seek`)

Reorder lines 296–308 in `plugin/scripts/python/cli.py`:

```python
<<<<<<< BEFORE
def handle_seek(args: argparse.Namespace) -> int:
    if not _has_active_session():
        print(
            "control: no active playback session (unified daemon uses synchronous audio).",
            file=sys.stderr,
        )
        return EXIT_NO_SESSION

    target = args.target.strip()
    if not target:
        print("control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
        return EXIT_BAD_ARG

    if target == "end":
...
=======
def handle_seek(args: argparse.Namespace) -> int:
    target = args.target.strip()
    if not target:
        print("control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
        return EXIT_BAD_ARG

    if target == "end":
        cmd: list[Any] = ["seek", 999999, "absolute"]
    elif target.startswith("+"):
        try:
            val = float(target[1:])
            cmd = ["seek", val, "relative"]
        except ValueError:
            print("control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
            return EXIT_BAD_ARG
    elif target.startswith("-"):
        try:
            val = float(target)
            cmd = ["seek", val, "relative"]
        except ValueError:
            print("control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
            return EXIT_BAD_ARG
    else:
        try:
            val = float(target)
            cmd = ["seek", val, "absolute"]
        except ValueError:
            print("control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
            return EXIT_BAD_ARG

    if not _has_active_session():
        print(
            "control: no active playback session (unified daemon uses synchronous audio).",
            file=sys.stderr,
        )
        return EXIT_NO_SESSION
>>>>>>> AFTER
```

### Recommendation C: Unit Tests to Add to `tests/test_cli_dispatcher.py`

In `TestCliSpeak` class:

```python
    def test_speak_tty_source_hash_triggers_cache_without_reading_stdin(self) -> None:
        """On an interactive terminal (TTY), --source-hash triggers cache lookup without blocking on stdin."""
        valid_hash = "a" * 64
        mock_client = mock.MagicMock()
        mock_client.play_cache.return_value = DaemonResponse(status="ok", action="play_cache")
        mock_stdin = mock.MagicMock()
        mock_stdin.isatty.return_value = True
        mock_stdin.read.side_effect = AssertionError("sys.stdin.read() must not be called on TTY when --source-hash is present")

        with mock.patch("sys.stdin", mock_stdin):
            rc = cli.main(["speak", "--source-hash", valid_hash], client=mock_client)

        self.assertEqual(rc, 0)
        mock_client.play_cache.assert_called_once_with(valid_hash)
        mock_stdin.read.assert_not_called()

    def test_speak_tty_no_words_no_hash_exits_two_without_reading_stdin(self) -> None:
        """On an interactive terminal (TTY), missing input exits fast with code 2 without blocking on stdin."""
        mock_stdin = mock.MagicMock()
        mock_stdin.isatty.return_value = True
        mock_stdin.read.side_effect = AssertionError("sys.stdin.read() must not be called on TTY without input")
        stderr = io.StringIO()

        with (
            mock.patch("sys.stdin", mock_stdin),
            mock.patch("sys.stderr", stderr),
        ):
            rc = cli.main(["speak"])

        self.assertEqual(rc, 2)
        mock_stdin.read.assert_not_called()
        self.assertIn("speak: no text provided on command line or stdin", stderr.getvalue())

    def test_speak_piped_non_tty_with_text_and_source_hash_uses_piped_text(self) -> None:
        """When stdin is piped (non-TTY) with text, stdin text is spoken and passed with source_hash."""
        valid_hash = "a" * 64
        mock_client = mock.MagicMock()
        mock_client.speak.return_value = DaemonResponse(status="ok", action="speak")

        with mock.patch("sys.stdin", io.StringIO("piped text overrides empty")):
            rc = cli.main(["speak", "--source-hash", valid_hash], client=mock_client)

        self.assertEqual(rc, 0)
        mock_client.speak.assert_called_once()
        args, kwargs = mock_client.speak.call_args
        self.assertEqual(args[0], "piped text overrides empty")
        self.assertEqual(kwargs.get("source_hash"), valid_hash)
        mock_client.play_cache.assert_not_called()

    def test_speak_piped_non_tty_empty_with_source_hash_triggers_cache(self) -> None:
        """When stdin is piped (non-TTY) and empty, --source-hash proceeds to cache playback."""
        valid_hash = "a" * 64
        mock_client = mock.MagicMock()
        mock_client.play_cache.return_value = DaemonResponse(status="ok", action="play_cache")

        with mock.patch("sys.stdin", io.StringIO("   \n\t  ")):
            rc = cli.main(["speak", "--source-hash", valid_hash], client=mock_client)

        self.assertEqual(rc, 0)
        mock_client.play_cache.assert_called_once_with(valid_hash)
        mock_client.speak.assert_not_called()
```

In `TestCliSeek` class:

```python
    def test_seek_invalid_target_unmocked_session_exits_four(self) -> None:
        """Invalid seek target returns exit code 4 even when no playback session is active."""
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            rc = cli.main(["seek", "foo"])
        self.assertEqual(rc, 4)
        self.assertIn("seek requires a target", stderr.getvalue())
```

---

## 5. Verification Method

1. **Interactive Terminal Non-Blocking Test**:
   ```bash
   cd /Users/joshua/Developer/auto-speech-worktree
   # Test 1: speak without args on interactive terminal must exit 2 immediately (not hang)
   ./bin/auto-speech speak
   # Expected: Exit 2, prints "speak: no text provided on command line or stdin"

   # Test 2: speak --source-hash on interactive terminal must not hang on stdin
   # (Will attempt cache lookup via daemon/NativeAudioSink immediately)
   ./bin/auto-speech speak --source-hash 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
   ```

2. **Piped Input Tests**:
   ```bash
   # Test 3: Piped text works as expected
   echo "hello world" | ./bin/auto-speech speak

   # Test 4: Empty piped text with --source-hash proceeds to cache lookup
   echo "" | ./bin/auto-speech speak --source-hash 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
   ```

3. **Seek Validation Test**:
   ```bash
   # Test 5: Invalid target without active session must exit 4
   ./bin/auto-speech seek foo
   [ $? -eq 4 ] && echo "PASS: seek foo returned 4"
   ```

4. **Hermetic Test Suite**:
   ```bash
   .venv/bin/python -m unittest tests/test_cli_dispatcher.py
   bash tests/run_all.sh
   .venv/bin/ruff check plugin/scripts/python/cli.py tests/test_cli_dispatcher.py
   ```
