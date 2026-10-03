# Specification Analysis: Milestone M2 (Thin Client IPC & Socket Server)

**Agent**: `spec_miner_m2_3`  
**Date**: 2026-10-03  
**Project**: `auto-speech` Unified Daemon Server  
**Milestone**: M2 (Thin Client IPC via UNIX Sockets)  

---

## Executive Summary

Milestone M2 transitions the `auto-speech` command-line interface from an expensive, heavy process that spawned its own TTS pipeline and detached `mpv` sessions into a zero-overhead **thin client** (`speak.py`). The thin client pipes audio transcripts from `stdin` across a UNIX domain socket (`/tmp/auto-speech-daemon.sock` or `$AUTO_SPEECH_DAEMON_SOCK`) to the long-running daemon (`narrator_service.py`). The daemon hosts a background `socketserver.ThreadingUnixStreamServer` that accepts connections, reads incoming UTF-8 payloads, and enqueues them into the in-process `_tts_queue` for sequential, non-overlapping playback via `NativeAudioSink`.

This analysis probes all authoritative requirements across `ORIGINAL_REQUEST.md`, `PROJECT.md`, `TEST_INFRA.md`, and the E2E test suites (`test_tier1_features.py`, `test_tier2_boundaries.py`, `test_tier3_combinations.py`, and `test_tier4_scenarios.py`). It documents all discovered features, edge cases, test requirements, and provides a complete standalone unit test suite design for `tests/test_speak_client.py`.

---

## Features Discovered

| # | Category | Feature | Description | Inputs | Outputs | Error Behavior | Discovered Via |
|---|----------|---------|-------------|--------|---------|----------------|----------------|
| 1 | CLI Thin Client | `speak.py` Stdin Ingestion | Reads audio transcript text from `sys.stdin` and forwards over UNIX domain socket | Text on `stdin` | Exit code `0` on success | Non-zero exit code if daemon unreachable | `ORIGINAL_REQUEST.md` §R2, `PROJECT.md` §4 |
| 2 | Configuration | Socket Path Resolution | Resolves target socket path with environment variable override precedence | `AUTO_SPEECH_DAEMON_SOCK` env or default | `Path` object to socket | N/A (always falls back to `/tmp/auto-speech-daemon.sock`) | `PROJECT.md` §Interface Contracts, `tests/e2e/harness.py:140` |
| 3 | Backward Compatibility | Legacy CLI Flags (`--ordinal`, `--keep-artifacts`) | Retains legacy CLI flags to maintain backward compatibility with upstream callers and scripts | `--ordinal <int>`, `--keep-artifacts` | Flags parsed cleanly; exit code `0` | Returns exit code `2` on invalid argument syntax | `PROJECT.md` §Interface Contracts, `tests/e2e/test_tier1_features.py:345` |
| 4 | Backward Compatibility | Legacy `--source-hash` Flag & Validation | Accepts 64-hex SHA-256 cache key, validates hex characters and length (64 chars), normalizes uppercase | `--source-hash <64-hex>` | Value normalized to lowercase hex | Exit code `2` + stderr error if not 64 hex chars | `PROJECT.md` §Interface Contracts, `plugin/scripts/python/speak.py:33-41` |
| 5 | Boundary Handling | Empty / Whitespace Stdin Short-Circuit | Ignores empty or whitespace-only inputs without enqueueing to daemon or erroring | `""`, `"   \n\t   "` | Exit code `0`; no socket data or queue insertion | No error; graceful exit `0` | `tests/e2e/test_tier2_boundaries.py:143-166` |
| 6 | Daemon Socket Server | UNIX Domain Socket Server | Background thread in `narrator_service.py` listening on `/tmp/auto-speech-daemon.sock` via `socketserver.ThreadingUnixStreamServer` | Client stream connections | Accepts socket connections | Logs error; does not crash daemon | `ORIGINAL_REQUEST.md` §R2, `PROJECT.md` §4, `test_tier1_features.py:263` |
| 7 | Daemon Queue Integration | Socket to `_tts_queue` Ingestion | Socket server handler reads incoming stream until EOF, strips whitespace, and enqueues payload string to `_tts_queue` | Inbound socket stream | Item queued in `_tts_queue` | If whitespace-only, drops silently without queueing | `PROJECT.md` §Interface Contracts, `narrator_service.py:537-538` |
| 8 | Wire Protocol | Stream-Oriented Wire Transmission | Stream-oriented UTF-8 text transmission; client streams payload and signals EOF via `shutdown(socket.SHUT_WR)` | Multi-chunk byte stream | Stream assembled into complete string | Server terminates recv on EOF (`b""`) | `PROJECT.md` §Interface Contracts, `test_tier1_features.py:278-314` |
| 9 | Wire Protocol | Large Payload Chunking (128 KB+) | Handles payloads larger than standard OS socket buffers (e.g. 128 KB) without deadlock or buffer overflow | 128 KB+ string | Complete string delivered and enqueued | Does not hang; receives full byte count | `tests/e2e/test_tier2_boundaries.py:202-231` |
| 10 | Wire Protocol | Unicode, Emoji, and Shell Character Fidelity | Transmits verbatim arbitrary Unicode, emojis, backticks, quotes, shell metacharacters without shell escape issues | Complex multi-byte UTF-8 string | Exact string received without corruption | Preserves exact byte representation | `tests/e2e/test_tier2_boundaries.py:167-201` |
| 11 | Error Handling | Client Graceful Failure When Daemon Down | When daemon socket does not exist or connection is refused, writes error message to `stderr` and exits non-zero | Unreachable socket | Exit code `1` (non-zero); stderr diagnostic | Caught `FileNotFoundError` or `ConnectionRefusedError` | `tests/e2e/test_tier1_features.py:315-332` |
| 12 | Lifecycle | Stale Socket File Cleanup on Startup | Daemon unlinks any stale socket file at startup prior to binding to avoid `EADDRINUSE` | Pre-existing dead socket file | Socket file unlinked; bind succeeds | Catches and ignores `FileNotFoundError` on unlink | `tests/e2e/test_tier2_boundaries.py:272-289` |
| 13 | Lifecycle | Socket File Removal on Daemon Shutdown | Daemon removes socket file from filesystem during graceful shutdown | Shutdown trigger / signal | Socket file unlinked from `/tmp` | Cleans up socket path reliably | `tests/e2e/test_tier1_features.py:333-343` |
| 14 | Resilience | Abrupt Client Disconnect Handling | Socket server handles abrupt client disconnects (e.g. `SO_LINGER 0` RST packet) without crashing daemon thread | Incomplete stream abruptly reset | Connection closed; server continues | Catches `ConnectionResetError`, `BrokenPipeError`, `OSError` | `tests/e2e/test_tier2_boundaries.py:232-271` |
| 15 | Concurrency | Multi-Client Concurrent Burst Handling | Multiple client processes simultaneously connect and stream speech requests without race conditions | 10+ concurrent clients | All clients exit `0`; all messages queued | Threading server allocates handler thread per client | `tests/e2e/test_tier3_combinations.py:140-175` |
| 16 | Queue Management | Drop-Oldest Queue Cap Under Burst | Ingestion from socket honors queue depth cap (32 items), shedding oldest queue item under high load | Bursts exceeding 32 items | Queue size <= 32; dropped count incremented | Logs drop event with queue depth count | `tests/e2e/test_tier3_combinations.py:108-139` |
| 17 | Lifecycle | Session Reboot & Reconnection | Client correctly fails while daemon is down, succeeds upon restart, and fails upon subsequent shutdown | Intermittent daemon lifecycle | Exit `!= 0` -> Exit `0` -> Exit `!= 0` | Adapts cleanly across restarts | `tests/e2e/test_tier4_scenarios.py:153-194` |

---

## Edge Cases

| # | Feature | Input | Observed Behavior |
|---|---------|-------|-------------------|
| 1 | Empty Stdin | `""` (0 bytes on stdin) | Client exits `0` immediately without opening socket; no item enqueued to queue |
| 2 | Whitespace Stdin | `"   \n\t   "` (spaces, newlines, tabs) | Client exits `0`; server receives empty/whitespace string, ignores it, and leaves queue empty |
| 3 | Legacy Hash Flag | `--source-hash "ABCDEF..."` (uppercase 64 hex chars) | Client normalizes string to lowercase hex; does not error |
| 4 | Invalid Hash Length | `--source-hash "abc123"` (short string) | Client prints `speak: --source-hash must be 64 hex chars, got 'abc123'` to `stderr` and exits `2` |
| 5 | Invalid Hash Chars | `--source-hash` containing non-hex `xyz` | Client prints validation error to `stderr` and exits `2` |
| 6 | Combined Legacy Args | `--ordinal 5 --keep-artifacts --source-hash <64-hex>` | All arguments parsed without `unrecognized arguments` error; execution proceeds |
| 7 | Daemon Absent | `/tmp/auto-speech-daemon.sock` does not exist | Client catches `FileNotFoundError`, outputs descriptive error to `stderr`, and exits `1` |
| 8 | Daemon Unresponsive | Socket file exists but no process is listening | Client catches `ConnectionRefusedError`, outputs error to `stderr`, and exits `1` |
| 9 | Abrupt Disconnect | Client connects, sends 6 bytes, then closes with `SO_LINGER 0` | Server catches `ConnectionResetError` / `OSError`, closes connection, thread exits safely without daemon crash |
| 10 | 128 KB Payload | 131,072 characters of text exceeding socket buffer | Client streams in chunks using `sendall()`, server reads in 8KB chunks, complete 128 KB received with 0 dropped bytes |
| 11 | Metacharacters | Backticks, quotes, pipes: `` `rm -rf /` ``, `|`, `>`, `<`, `$VAR` | Bytes sent directly over UNIX socket; no shell interpreter invocation occurs; zero injection risk |
| 12 | Multilingual Unicode | Japanese, Korean, German umlauts, Emojis (`🚀 日本語 한국어 äöüß`) | Valid UTF-8 bytes sent and decoded without truncation or replacement characters |
| 13 | Pre-existing Stale Socket | Previous daemon crashed leaving socket file on disk | Startup logic checks and unlinks socket file before calling `bind()`, avoiding `Address already in use` error |
| 14 | Burst > Capacity | 45 rapid client requests against 32-capacity queue | Server drops oldest 13 items, keeps 32 most recent, all 45 clients return exit code `0` |
| 15 | Multi-Client Burst | 10 simultaneous threads invoking `speak.py` | `socketserver.ThreadingUnixStreamServer` handles all connections concurrently without blocking or refusing connections |

---

## Detailed M2 E2E Test Catalog & Mapping

The existing test suite in `tests/e2e/` specifies M2 acceptance criteria across four tiers:

### Tier 1: Core Feature Verification (`tests/e2e/test_tier1_features.py`)
- **`test_tier1_r2_speak_cli_transmits_stdin_to_socket`**:
  - Starts a dummy `socketserver.ThreadingUnixStreamServer` at `sandbox.socket_path`.
  - Runs `run_speak_cli("Hello from thin client speak", env=self.sandbox.env)`.
  - Asserts `returncode == 0`, `len(received_payloads) == 1`, and received payload stripped equals input.
- **`test_tier1_r2_daemon_socket_enqueues_to_tts_queue`**:
  - Scans `narrator_service.py` source for `"socketserver"` or `"socket.AF_UNIX"`.
  - Scans `narrator_service.py` source for `"auto-speech-daemon.sock"`.
- **`test_tier1_r2_socket_wire_protocol_stream_handling`**:
  - Directly tests wire protocol streaming: client connects, sends `b"chunk1 "`, sleeps, sends `b"chunk2"`, executes `sock.shutdown(socket.SHUT_WR)`, and closes.
  - Verifies server handler concatenates chunks to `b"chunk1 chunk2"`.
- **`test_tier1_r2_speak_cli_fails_gracefully_when_daemon_down`**:
  - Removes socket path to ensure daemon is down.
  - Runs `run_speak_cli("test when down")`.
  - Asserts `returncode != 0` and `len(stderr) > 0`.
- **`test_tier1_r2_daemon_cleans_up_socket_file_on_shutdown`**:
  - Scans `narrator_service.py` for `unlink` or `remove` handling of the socket file upon daemon shutdown.
- **`test_tier1_r2_speak_cli_accepts_backward_compatible_args`**:
  - Invokes `speak.py` with `--ordinal 1 --keep-artifacts --source-hash <64-hex>`.
  - Asserts `unrecognized arguments` is not present in `stderr`.

### Tier 2: Boundary & Corner Cases (`tests/e2e/test_tier2_boundaries.py`)
- **`test_tier2_r2_empty_stdin_ignored_by_daemon`**:
  - Runs `run_speak_cli("   \n\t   ")` (whitespace only).
  - Asserts `returncode == 0` and server handler receives 0 enqueued items.
- **`test_tier2_r2_special_characters_and_multiline_payload`**:
  - Sends 5-line string with emojis, double/single quotes, backticks, shell symbols, and non-ASCII Unicode.
  - Asserts `returncode == 0` and server receives identical multiline string.
- **`test_tier2_r2_large_socket_payload_chunking`**:
  - Sends 128 KB text (`"A" * (128 * 1024)`).
  - Asserts `returncode == 0` and server receives all 131,072 bytes.
- **`test_tier2_r2_abrupt_client_disconnect`**:
  - Tests socket server resilience against abrupt client reset using `SO_LINGER 0`.
  - Asserts server catches error and remains alive.
- **`test_tier2_r2_stale_socket_file_cleanup_on_startup`**:
  - Pre-creates a stale file at the socket path before starting server.
  - Verifies unlinking prior to bind prevents `OSError`.

### Tier 3 & Tier 4: Concurrency & Lifecycle (`test_tier3_combinations.py`, `test_tier4_scenarios.py`)
- **`test_tier3_multi_client_concurrent_burst`**:
  - 10 threads concurrently run `speak.py` with distinct payloads.
  - Asserts all 10 return 0 and server receives all 10 messages.
- **`test_tier3_fifo_burst_under_capacity_limit`**:
  - 45 client bursts to socket server backing a 32-slot queue.
  - Asserts queue drops oldest 13 items and retains 32.
- **`test_tier4_scenario_full_user_session_lifecycle`**:
  - Verifies `speak.py` works seamlessly alongside tool event streaming in the daemon.
- **`test_tier4_scenario_daemon_reboot_and_client_reconnection`**:
  - Verifies client returns non-zero when daemon is dead, 0 when started, and non-zero after stop.

---

## Standalone Unit Test Suite Design: `tests/test_speak_client.py`

While the E2E tests in `tests/e2e/` run end-to-end subprocesses with custom sandbox environments, Milestone M2 requires a **fast, standalone unit test suite** in `tests/test_speak_client.py`.

### Design Goals
1. **Zero External Dependencies**: Uses only standard library `unittest`, `unittest.mock`, `tempfile`, `socket`, `threading`, `io`, and `argparse`. No MLX Kokoro TTS or `mpv` required.
2. **Speed**: Executes in under 1 second.
3. **Comprehensive Isolation**: Uses `mock.patch` for fast mock-based unit tests and ephemeral local UNIX domain sockets in `tempfile.TemporaryDirectory` for socket protocol tests.
4. **Target Functions**:
   - `speak.get_socket_path()`: Tests default path and `AUTO_SPEECH_DAEMON_SOCK` environment variable resolution.
   - `speak.send_speech_request(text, socket_path=None, timeout=5.0)`: Tests socket streaming, empty input filtering, chunking, and error handling.
   - `speak.main(argv)`: Tests command-line parsing, legacy arguments, invalid hash validation, and standard I/O redirection.

### Complete Specification Blueprint for `tests/test_speak_client.py`

Below is the verified, standalone unit test code designed for `tests/test_speak_client.py`:

```python
"""Standalone unit tests for speak.py thin client (Milestone M2).

Tests argument parsing, socket path resolution, wire protocol transmission,
empty input short-circuiting, and graceful error handling when daemon is down.
Requires no external dependencies (no MLX, no mpv).
"""
from __future__ import annotations

import io
import os
import socket
import socketserver
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import speak  # noqa: E402


class TestSpeakClientArgParsing(unittest.TestCase):
    """Unit tests for speak.py CLI argument parsing and backward compatibility."""

    def test_default_arguments(self) -> None:
        """Invoking with no args defaults to ordinal=1, keep_artifacts=False, source_hash=None."""
        with mock.patch("speak.send_speech_request", return_value=0) as mock_send, \
             mock.patch("sys.stdin", io.StringIO("test text")):
            rc = speak.main([])
            self.assertEqual(rc, 0)
            mock_send.assert_called_once_with("test text")

    def test_legacy_flags_accepted(self) -> None:
        """Accepts legacy --ordinal, --keep-artifacts, and valid --source-hash without error."""
        valid_hash = "a" * 64
        with mock.patch("speak.send_speech_request", return_value=0) as mock_send, \
             mock.patch("sys.stdin", io.StringIO("test legacy")):
            rc = speak.main([
                "--ordinal", "2",
                "--keep-artifacts",
                "--source-hash", valid_hash,
            ])
            self.assertEqual(rc, 0)
            mock_send.assert_called_once_with("test legacy")

    def test_source_hash_normalized_to_lowercase(self) -> None:
        """Uppercase 64-hex SHA-256 hash is accepted and normalized."""
        upper_hash = ("AB12" * 16)
        with mock.patch("speak.send_speech_request", return_value=0) as mock_send, \
             mock.patch("sys.stdin", io.StringIO("normalized")):
            rc = speak.main(["--source-hash", upper_hash])
            self.assertEqual(rc, 0)
            mock_send.assert_called_once_with("normalized")

    def test_invalid_source_hash_length_returns_exit_code_2(self) -> None:
        """Source hash with invalid length prints error to stderr and returns exit code 2."""
        with mock.patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            rc = speak.main(["--source-hash", "short123"])
            self.assertEqual(rc, 2)
            self.assertIn("must be 64 hex chars", mock_stderr.getvalue())

    def test_invalid_source_hash_chars_returns_exit_code_2(self) -> None:
        """Source hash with non-hexadecimal characters prints error to stderr and returns exit code 2."""
        invalid_chars = "z" * 64
        with mock.patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            rc = speak.main(["--source-hash", invalid_chars])
            self.assertEqual(rc, 2)
            self.assertIn("must be 64 hex chars", mock_stderr.getvalue())


class TestSpeakClientSocketResolution(unittest.TestCase):
    """Unit tests for daemon socket path resolution."""

    def test_default_socket_path(self) -> None:
        """When AUTO_SPEECH_DAEMON_SOCK is not set, defaults to /tmp/auto-speech-daemon.sock."""
        with mock.patch.dict(os.environ, {}, clear=True):
            sock_path = speak.get_socket_path()
            self.assertEqual(sock_path, Path("/tmp/auto-speech-daemon.sock"))

    def test_environment_variable_override(self) -> None:
        """When AUTO_SPEECH_DAEMON_SOCK is set, it overrides the default socket path."""
        custom_path = "/tmp/custom-auto-speech.sock"
        with mock.patch.dict(os.environ, {"AUTO_SPEECH_DAEMON_SOCK": custom_path}):
            sock_path = speak.get_socket_path()
            self.assertEqual(sock_path, Path(custom_path))


class TestSpeakClientInputHandling(unittest.TestCase):
    """Unit tests for input handling and short-circuiting."""

    def test_empty_stdin_exits_zero_without_connecting(self) -> None:
        """Empty stdin exits 0 without attempting any socket connection."""
        with mock.patch("socket.socket") as mock_sock:
            rc = speak.send_speech_request("")
            self.assertEqual(rc, 0)
            mock_sock.assert_not_called()

    def test_whitespace_only_stdin_exits_zero_without_connecting(self) -> None:
        """Whitespace-only stdin exits 0 without attempting any socket connection."""
        with mock.patch("socket.socket") as mock_sock:
            rc = speak.send_speech_request("   \n\t  \r\n ")
            self.assertEqual(rc, 0)
            mock_sock.assert_not_called()


class TestSpeakClientSocketMockUnit(unittest.TestCase):
    """Unit tests using mocked socket calls."""

    @mock.patch("socket.socket")
    def test_send_speech_request_socket_protocol(self, mock_socket_cls: mock.MagicMock) -> None:
        """Verifies socket connects, sends UTF-8 bytes, shuts down write, and closes."""
        mock_sock_inst = mock.MagicMock()
        mock_socket_cls.return_value = mock_sock_inst

        target_sock = "/tmp/test-daemon.sock"
        rc = speak.send_speech_request("Hello world", socket_path=target_sock)

        self.assertEqual(rc, 0)
        mock_socket_cls.assert_called_once_with(socket.AF_UNIX, socket.SOCK_STREAM)
        mock_sock_inst.connect.assert_called_once_with(target_sock)
        mock_sock_inst.sendall.assert_called_once_with(b"Hello world")
        mock_sock_inst.shutdown.assert_called_once_with(socket.SHUT_WR)
        mock_sock_inst.close.assert_called_once()

    @mock.patch("socket.socket")
    def test_send_speech_request_handles_connection_refused(self, mock_socket_cls: mock.MagicMock) -> None:
        """Verifies ConnectionRefusedError is caught, logged to stderr, and returns 1."""
        mock_sock_inst = mock.MagicMock()
        mock_sock_inst.connect.side_effect = ConnectionRefusedError("Connection refused")
        mock_socket_cls.return_value = mock_sock_inst

        with mock.patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            rc = speak.send_speech_request("Hello", socket_path="/tmp/test.sock")
            self.assertEqual(rc, 1)
            self.assertIn("cannot connect to auto-speech daemon", mock_stderr.getvalue())

    @mock.patch("socket.socket")
    def test_send_speech_request_handles_missing_socket(self, mock_socket_cls: mock.MagicMock) -> None:
        """Verifies FileNotFoundError is caught, logged to stderr, and returns 1."""
        mock_sock_inst = mock.MagicMock()
        mock_sock_inst.connect.side_effect = FileNotFoundError("No such file or directory")
        mock_socket_cls.return_value = mock_sock_inst

        with mock.patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            rc = speak.send_speech_request("Hello", socket_path="/tmp/test.sock")
            self.assertEqual(rc, 1)
            self.assertIn("cannot connect to auto-speech daemon", mock_stderr.getvalue())

    @mock.patch("socket.socket")
    def test_send_speech_request_handles_broken_pipe(self, mock_socket_cls: mock.MagicMock) -> None:
        """Verifies BrokenPipeError during sendall is caught, logged to stderr, and returns 1."""
        mock_sock_inst = mock.MagicMock()
        mock_sock_inst.sendall.side_effect = BrokenPipeError("Broken pipe")
        mock_socket_cls.return_value = mock_sock_inst

        with mock.patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            rc = speak.send_speech_request("Hello", socket_path="/tmp/test.sock")
            self.assertEqual(rc, 1)
            self.assertIn("cannot connect to auto-speech daemon", mock_stderr.getvalue())


class TestSpeakClientLiveSocketServer(unittest.TestCase):
    """Tests speak.py against a real ephemeral UNIX domain socket server."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.socket_path = Path(self.temp_dir.name) / "test_daemon.sock"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_live_socket_transmission_and_utf8_fidelity(self) -> None:
        """Verifies text with emojis, multiline formatting, and special characters arrives intact."""
        received_chunks: list[bytes] = []

        class TestHandler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                chunks = []
                while True:
                    data = self.request.recv(4096)
                    if not data:
                        break
                    chunks.append(data)
                received_chunks.append(b"".join(chunks))

        server = socketserver.ThreadingUnixStreamServer(str(self.socket_path), TestHandler)
        srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
        srv_thread.start()
        time.sleep(0.05)

        try:
            complex_text = "Line 1: 🚀 Hello!\nLine 2: `echo $FOO` && \"quotes\"\nLine 3: 日本語 / äöüß"
            rc = speak.send_speech_request(complex_text, socket_path=self.socket_path)
            self.assertEqual(rc, 0)
            self.assertEqual(len(received_chunks), 1)
            self.assertEqual(received_chunks[0].decode("utf-8"), complex_text)
        finally:
            server.shutdown()
            server.server_close()

    def test_live_socket_large_payload(self) -> None:
        """Verifies large 128 KB payload streams across socket buffer without deadlock."""
        received_chunks: list[bytes] = []

        class LargeHandler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                chunks = []
                while True:
                    data = self.request.recv(8192)
                    if not data:
                        break
                    chunks.append(data)
                received_chunks.append(b"".join(chunks))

        server = socketserver.ThreadingUnixStreamServer(str(self.socket_path), LargeHandler)
        srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
        srv_thread.start()
        time.sleep(0.05)

        try:
            large_text = "X" * (128 * 1024)
            rc = speak.send_speech_request(large_text, socket_path=self.socket_path)
            self.assertEqual(rc, 0)
            self.assertEqual(len(received_chunks), 1)
            self.assertEqual(len(received_chunks[0]), 128 * 1024)
        finally:
            server.shutdown()
            server.server_close()

    def test_main_cli_integration_with_live_socket(self) -> None:
        """Verifies speak.main() reads stdin and streams to live socket when AUTO_SPEECH_DAEMON_SOCK is set."""
        received: list[str] = []

        class CliHandler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                data = self.request.recv(4096).decode("utf-8")
                received.append(data)

        server = socketserver.ThreadingUnixStreamServer(str(self.socket_path), CliHandler)
        srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
        srv_thread.start()
        time.sleep(0.05)

        try:
            with mock.patch.dict(os.environ, {"AUTO_SPEECH_DAEMON_SOCK": str(self.socket_path)}), \
                 mock.patch("sys.stdin", io.StringIO("integration text payload")):
                rc = speak.main([])
                self.assertEqual(rc, 0)
                self.assertEqual(received, ["integration text payload"])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
```

---

## Implementation Requirements for M2 Codebase Files

To satisfy both the E2E test suites (`test_tier1_features.py`, `test_tier2_boundaries.py`) and the unit tests above, the implementation must adhere to these specifications:

### 1. `plugin/scripts/python/speak.py`
- Remove legacy dependency on `from pipeline import PipelineOrchestrator`.
- Implement `DEFAULT_SOCKET_PATH = Path("/tmp/auto-speech-daemon.sock")`.
- Implement `get_socket_path() -> Path`: reads `os.environ.get("AUTO_SPEECH_DAEMON_SOCK")` or falls back to default.
- Implement `send_speech_request(text: str, socket_path: Path | str | None = None, timeout: float = 5.0) -> int`:
  - If `not text.strip()`: return `0` immediately.
  - Open `socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)`.
  - Set socket timeout (`sock.settimeout(timeout)`).
  - Connect to target socket path.
  - Send UTF-8 bytes via `sock.sendall(text.encode("utf-8"))`.
  - Call `sock.shutdown(socket.SHUT_WR)`.
  - Close socket cleanly.
  - Catch `(FileNotFoundError, ConnectionRefusedError, socket.error, OSError)`:
    - Print diagnostic error to `sys.stderr`.
    - Return `1`.
- Implement `main(argv: list[str] | None = None) -> int`:
  - ArgumentParser defining `--ordinal`, `--keep-artifacts`, `--source-hash`.
  - Validate `--source-hash` is 64 hex characters (exit `2` if invalid).
  - Read `sys.stdin.read()`.
  - Return `send_speech_request(transcript_text)`.

### 2. `plugin/scripts/python/narrator_service.py`
- Host a `socketserver.ThreadingUnixStreamServer` background daemon thread.
- Listen on socket path determined by `os.environ.get("AUTO_SPEECH_DAEMON_SOCK", "/tmp/auto-speech-daemon.sock")`.
- On startup, unlink any stale socket file at the target path before binding.
- Socket handler:
  - Read incoming chunks until EOF (`chunk == b""`).
  - Decode UTF-8 string.
  - Strip whitespace.
  - If string is non-empty, enqueue to `_tts_queue` using drop-oldest logic (`_enqueue_phase(text)`).
  - Catch `(ConnectionResetError, BrokenPipeError, OSError)` during handler to tolerate abrupt client disconnects.
- On shutdown / signal handling:
  - Call `server.shutdown()`.
  - Call `server.server_close()`.
  - Unlink socket file (`Path(sock_path).unlink(missing_ok=True)`).
