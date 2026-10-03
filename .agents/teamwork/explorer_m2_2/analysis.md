# Analysis: Milestone M2 — `speak.py` Thin Client Refactor

## 1. Executive Summary

Milestone M2 refactors `plugin/scripts/python/speak.py` from a heavy standalone pipeline runner into an ultra-fast, zero-overhead thin CLI client.

In the previous architecture, `speak.py` imported `PipelineOrchestrator`, initialized MLX models / Kokoro TTS, spawned detached `mpv` background processes, and performed duration sleeps. This caused secondary process sprawl, model loading latency (>2.5s per invocation), and test timeouts.

In the Unified Daemon Server architecture:
1. `narrator_service.py` is the single authoritative owner of the audio hardware and Kokoro TTS engine in-process.
2. `speak.py` acts strictly as an IPC bridge: it reads text from `sys.stdin`, connects to the daemon's UNIX domain socket (`/tmp/auto-speech-daemon.sock` or `$AUTO_SPEECH_DAEMON_SOCK`), transmits the UTF-8 payload, signals write EOF via `socket.SHUT_WR`, and exits 0.
3. If the daemon is unreachable or down, `speak.py` prints a clean error message to `sys.stderr` and exits 1.
4. If input is empty or whitespace-only, `speak.py` exits 0 cleanly without sending data across the wire.
5. All legacy CLI arguments (`--ordinal`, `--source-hash`, `--keep-artifacts`) are retained for full backward compatibility with callers such as `autoplay_worker.py` and slash command scripts.

---

## 2. Current State of `plugin/scripts/python/speak.py`

### 2.1 Code Inspection
The existing `plugin/scripts/python/speak.py` (53 lines):
- Imports `from pipeline import PipelineOrchestrator` (line 14).
- Parses `--ordinal` (int, default 1), `--keep-artifacts` (flag), and `--source-hash` (64-hex SHA-256 validation).
- Reads `transcript_text = sys.stdin.read()`.
- Instantiates `PipelineOrchestrator(keep_artifacts=args.keep_artifacts, source_hash=args.source_hash)`.
- Invokes `orchestrator.run(transcript_text=transcript_text, turn_ordinal=args.ordinal)`.

### 2.2 Defects & Test Failures in Existing Architecture
When evaluated against the E2E test suite:
1. **Tier 1 failure**: `test_tier1_r2_speak_cli_transmits_stdin_to_socket` fails with exit code 124 (timeout expired after 2.5s) because `speak.py` boots `PipelineOrchestrator` instead of transmitting over the socket.
2. **Tier 1 failure**: `test_tier1_r3_no_pipeline_orchestrator_imports` flags `speak.py` for importing `PipelineOrchestrator`.
3. **Tier 2 failure**: `test_tier2_r2_empty_stdin_ignored_by_daemon` fails with exit code 4 because `PipelineOrchestrator` treats empty text as an error instead of exiting 0.
4. **Tier 2 failures**: `test_tier2_r2_large_socket_payload_chunking` and `test_tier2_r2_special_characters_and_multiline_payload` time out (exit code 124) attempting to synthesize audio.

Refactoring `speak.py` into a thin client directly resolves all 4 test failures.

---

## 3. Call Sites & Ecosystem Analysis

References to `speak.py` across the codebase were cataloged:
1. **`plugin/scripts/shell/run_speak.sh`**:
   `exec python "$PLUGIN_SCRIPTS_DIR/python/speak.py" "$@"`
   (To be deleted in Milestone M3, but callers currently route through it).
2. **`plugin/scripts/python/autoplay_worker.py`**:
   Invokes `["bash", str(SPEAK), "--source-hash", source_hash]` with `stdin_text=rewrite`.
3. **`plugin/commands/auto-speech-speak.md`**:
   Invokes `run_speak.sh --ordinal ORDINAL --source-hash "$SOURCE_HASH" < "$REWRITE_FILE"`.
4. **`tests/e2e/harness.py` & E2E Test Suite**:
   Invokes `python3 speak.py` directly via `run_speak_cli(...)`, passing `AUTO_SPEECH_DAEMON_SOCK` in the process environment.

Conclusion: Preserving `--ordinal`, `--source-hash`, and `--keep-artifacts` guarantees zero regressions for upstream callers while transitioning to socket IPC.

---

## 4. Thin Client Architecture & Design Specification

### 4.1 Argument Parsing Compatibility
`speak.py` must retain its existing CLI argument schema using standard library `argparse`:
- `--ordinal`: `type=int`, `default=1`. Kept for backward compatibility and logging.
- `--keep-artifacts`: `action="store_true"`. Kept as a no-op compatibility flag.
- `--source-hash`: `type=str`, `default=None`. Validates 64 hexadecimal characters if supplied; if malformed, prints `speak: --source-hash must be 64 hex chars, got {args.source_hash!r}` to `sys.stderr` and exits with code 2.
- `--socket-path`: `type=str`, `default=None` (new optional flag). Enables explicit socket override for testing and manual debugging.

### 4.2 Stdin Streaming & Empty Input Semantics
- `transcript_text = sys.stdin.read()`.
- **Empty / Whitespace Check**:
  ```python
  if not transcript_text.strip():
      return 0
  ```
  If stdin contains only whitespace or is empty:
  - Exit cleanly with code `0`.
  - Do NOT open a socket connection to the daemon.
  - Guarantees `_tts_queue` in `narrator_service.py` is never polluted with empty jobs.

### 4.3 UNIX Domain Socket IPC
- **Socket Path Resolution Order**:
  1. `--socket-path <path>` CLI option (highest precedence).
  2. `AUTO_SPEECH_DAEMON_SOCK` environment variable.
  3. Default path: `/tmp/auto-speech-daemon.sock`.
- **Connection & Transmission Protocol**:
  1. Create socket: `sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)`.
  2. Configure connection timeout: `sock.settimeout(5.0)` to prevent unbounded hangs.
  3. Connect to resolved `socket_path`.
  4. Encode text to UTF-8 bytes: `payload = transcript_text.encode("utf-8")`.
  5. Transmit all bytes: `sock.sendall(payload)`.
  6. Signal write shutdown: `sock.shutdown(socket.SHUT_WR)`. This sends an orderly EOF (FIN) to the server reader without severing the connection prematurely.
  7. Close socket cleanly.

### 4.4 Graceful Error Handling
When the daemon is not running or unreachable:
- Catch `(FileNotFoundError, ConnectionRefusedError)`:
  - Output to `sys.stderr`: `Error: auto-speech daemon is not running.`
  - Exit code: `1`.
- Catch `(socket.timeout, OSError)`:
  - Output to `sys.stderr`: `Error: failed communicating with auto-speech daemon at {socket_path}: {exc}`
  - Exit code: `1`.
- Catch `KeyboardInterrupt`:
  - Exit code: `130` (clean exit without Python traceback).

---

## 5. Proposed Implementation of `speak.py`

```python
"""CLI entry for auto-speech (Thin Client).

Reads audio-friendly text from stdin and forwards it to the auto-speech daemon
via UNIX domain socket (/tmp/auto-speech-daemon.sock).
"""
from __future__ import annotations

import argparse
import os
import socket
import sys

DEFAULT_SOCKET_PATH = "/tmp/auto-speech-daemon.sock"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Speak an audio-friendly transcript via daemon.")
    p.add_argument("--ordinal", type=int, default=1, help="1-indexed N-from-end (compatibility/logging only)")
    p.add_argument("--keep-artifacts", action="store_true", help="preserve tmpdir (compatibility flag)")
    p.add_argument(
        "--source-hash",
        type=str,
        default=None,
        help="64-hex-char SHA-256 hash (compatibility flag)",
    )
    p.add_argument(
        "--socket-path",
        type=str,
        default=None,
        help="override UNIX domain socket path to auto-speech daemon",
    )
    args = p.parse_args(argv)

    if args.source_hash is not None:
        sh = args.source_hash.strip().lower()
        if len(sh) != 64 or any(c not in "0123456789abcdef" for c in sh):
            print(
                f"speak: --source-hash must be 64 hex chars, got {args.source_hash!r}",
                file=sys.stderr,
            )
            return 2
        args.source_hash = sh

    transcript_text = sys.stdin.read()
    if not transcript_text.strip():
        return 0

    socket_path = args.socket_path or os.environ.get("AUTO_SPEECH_DAEMON_SOCK", DEFAULT_SOCKET_PATH)

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5.0)
            sock.connect(socket_path)
            sock.sendall(transcript_text.encode("utf-8"))
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
    except (FileNotFoundError, ConnectionRefusedError):
        print("Error: auto-speech daemon is not running.", file=sys.stderr)
        return 1
    except (socket.timeout, OSError) as exc:
        print(f"Error: failed communicating with auto-speech daemon at {socket_path}: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
```

---

## 6. Verification & Test Strategy

### 6.1 E2E Test Suite Validation
The proposed thin client satisfies:
1. `test_tier1_r2_speak_cli_transmits_stdin_to_socket`:
   `echo "Hello from thin client speak" | python3 speak.py` connects to sandbox socket, transmits UTF-8 payload, exits 0.
2. `test_tier1_r2_speak_cli_fails_gracefully_when_daemon_down`:
   When socket is absent, exits 1 with non-empty error message on stderr.
3. `test_tier1_r2_speak_cli_accepts_backward_compatible_args`:
   Accepts `--ordinal 1 --keep-artifacts --source-hash "a"*64` without argument errors.
4. `test_tier2_r2_empty_stdin_ignored_by_daemon`:
   Piping `"   \n\t   "` exits 0 and does not enqueue to server.
5. `test_tier2_r2_special_characters_and_multiline_payload`:
   Transmits emojis, quotes, backticks, shell characters, and multilingual unicode faithfully.
6. `test_tier2_r2_large_socket_payload_chunking`:
   Streams 128 KB payload through `sock.sendall()` in single burst.

### 6.2 Standalone Unit Test Specification (`tests/test_speak_client.py`)
To isolate client verification from end-to-end daemon boots, a standalone unit test suite `tests/test_speak_client.py` is recommended with test doubles for `sys.stdin` and local mock socket servers:
- `test_empty_stdin_exits_zero`: tests `""` and whitespace exit 0 without opening socket.
- `test_missing_socket_exits_one`: tests `FileNotFoundError` output to stderr.
- `test_connection_refused_exits_one`: tests dead socket file output to stderr.
- `test_invalid_source_hash_exits_two`: tests validation of `--source-hash`.
- `test_payload_delivery`: verifies UTF-8 byte transmission against local `ThreadingUnixStreamServer`.
- `test_env_var_override`: verifies `AUTO_SPEECH_DAEMON_SOCK` directs traffic to target socket.

---

## 7. Migration Considerations

- **Milestone M2 dependencies**:
  `speak.py` thin client refactor can be implemented independently or concurrently with `narrator_service.py` socket server. Its graceful fallback (exit 1 + stderr) guarantees defined failure when daemon is offline.
- **Milestone M3 alignment**:
  Removing `from pipeline import PipelineOrchestrator` in `speak.py` breaks the dependency on `pipeline.py`, unblocking the deletion of `pipeline.py`, `short_path.py`, `mpv_controller.py`, `session_dir.py`, and `run_speak.sh`.
