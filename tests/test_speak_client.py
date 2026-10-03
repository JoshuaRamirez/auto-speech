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
        with (
            mock.patch("speak.send_speech_request", return_value=0) as mock_send,
            mock.patch("sys.stdin", io.StringIO("test text")),
        ):
            rc = speak.main([])
            self.assertEqual(rc, 0)
            mock_send.assert_called_once_with("test text")

    def test_legacy_flags_accepted(self) -> None:
        """Accepts legacy --ordinal, --keep-artifacts, and valid --source-hash without error."""
        valid_hash = "a" * 64
        with (
            mock.patch("speak.send_speech_request", return_value=0) as mock_send,
            mock.patch("sys.stdin", io.StringIO("test legacy")),
        ):
            rc = speak.main(
                [
                    "--ordinal",
                    "2",
                    "--keep-artifacts",
                    "--source-hash",
                    valid_hash,
                ]
            )
            self.assertEqual(rc, 0)
            mock_send.assert_called_once_with("test legacy")

    def test_source_hash_normalized_to_lowercase(self) -> None:
        """Uppercase 64-hex SHA-256 hash is accepted and normalized."""
        upper_hash = "AB12" * 16
        with (
            mock.patch("speak.send_speech_request", return_value=0) as mock_send,
            mock.patch("sys.stdin", io.StringIO("normalized")),
        ):
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

    def test_socket_path_argument_forwarded(self) -> None:
        """Explicit --socket-path is forwarded to send_speech_request."""
        with (
            mock.patch("speak.send_speech_request", return_value=0) as mock_send,
            mock.patch("sys.stdin", io.StringIO("custom sock")),
        ):
            rc = speak.main(["--socket-path", "/tmp/custom.sock"])
            self.assertEqual(rc, 0)
            mock_send.assert_called_once_with("custom sock", socket_path="/tmp/custom.sock")

    def test_empty_stdin_in_main_returns_zero(self) -> None:
        """When stdin is whitespace or empty, main returns 0 without calling send_speech_request."""
        with (
            mock.patch("speak.send_speech_request") as mock_send,
            mock.patch("sys.stdin", io.StringIO("   \n\t  ")),
        ):
            rc = speak.main([])
            self.assertEqual(rc, 0)
            mock_send.assert_not_called()


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
    def test_send_speech_request_handles_connection_refused(
        self, mock_socket_cls: mock.MagicMock
    ) -> None:
        """Verifies ConnectionRefusedError is caught, logged to stderr, and returns 1."""
        mock_sock_inst = mock.MagicMock()
        mock_sock_inst.connect.side_effect = ConnectionRefusedError("Connection refused")
        mock_socket_cls.return_value = mock_sock_inst

        with mock.patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            rc = speak.send_speech_request("Hello", socket_path="/tmp/test.sock")
            self.assertEqual(rc, 1)
            self.assertIn("cannot connect to auto-speech daemon", mock_stderr.getvalue())

    @mock.patch("socket.socket")
    def test_send_speech_request_retries_transient_connection_refused(
        self, mock_socket_cls: mock.MagicMock
    ) -> None:
        """Verifies ConnectionRefusedError triggers retry and succeeds on subsequent attempt."""
        sock1 = mock.MagicMock()
        sock1.connect.side_effect = ConnectionRefusedError("Connection refused")
        sock2 = mock.MagicMock()

        mock_socket_cls.side_effect = [sock1, sock2]

        rc = speak.send_speech_request("Retry test", socket_path="/tmp/test.sock")
        self.assertEqual(rc, 0)
        self.assertEqual(mock_socket_cls.call_count, 2)
        sock1.close.assert_called_once()
        sock2.connect.assert_called_once_with("/tmp/test.sock")
        sock2.sendall.assert_called_once_with(b"Retry test")
        sock2.close.assert_called_once()

    @mock.patch("socket.socket")
    def test_send_speech_request_handles_missing_socket(
        self, mock_socket_cls: mock.MagicMock
    ) -> None:
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
        recv_event = threading.Event()

        class TestHandler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                chunks = []
                while True:
                    data = self.request.recv(4096)
                    if not data:
                        break
                    chunks.append(data)
                received_chunks.append(b"".join(chunks))
                recv_event.set()

        server = socketserver.ThreadingUnixStreamServer(str(self.socket_path), TestHandler)
        srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
        srv_thread.start()
        time.sleep(0.05)

        try:
            complex_text = (
                'Line 1: 🚀 Hello!\nLine 2: `echo $FOO` && "quotes"\nLine 3: 日本語 / äöüß'
            )
            rc = speak.send_speech_request(complex_text, socket_path=self.socket_path)
            self.assertEqual(rc, 0)
            self.assertTrue(recv_event.wait(timeout=2.0))
            self.assertEqual(len(received_chunks), 1)
            self.assertEqual(received_chunks[0].decode("utf-8"), complex_text)
        finally:
            server.shutdown()
            server.server_close()

    def test_live_socket_large_payload(self) -> None:
        """Verifies large 128 KB payload streams across socket buffer without deadlock."""
        received_chunks: list[bytes] = []
        recv_event = threading.Event()

        class LargeHandler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                chunks = []
                while True:
                    data = self.request.recv(8192)
                    if not data:
                        break
                    chunks.append(data)
                received_chunks.append(b"".join(chunks))
                recv_event.set()

        server = socketserver.ThreadingUnixStreamServer(str(self.socket_path), LargeHandler)
        srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
        srv_thread.start()
        time.sleep(0.05)

        try:
            large_text = "X" * (128 * 1024)
            rc = speak.send_speech_request(large_text, socket_path=self.socket_path)
            self.assertEqual(rc, 0)
            self.assertTrue(recv_event.wait(timeout=2.0))
            self.assertEqual(len(received_chunks), 1)
            self.assertEqual(len(received_chunks[0]), 128 * 1024)
        finally:
            server.shutdown()
            server.server_close()

    def test_main_cli_integration_with_live_socket(self) -> None:
        """Verifies speak.main() reads stdin and streams to live socket when AUTO_SPEECH_DAEMON_SOCK is set."""
        received: list[str] = []
        recv_event = threading.Event()

        class CliHandler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                chunks = []
                while True:
                    data = self.request.recv(4096)
                    if not data:
                        break
                    chunks.append(data)
                if chunks:
                    received.append(b"".join(chunks).decode("utf-8"))
                recv_event.set()

        server = socketserver.ThreadingUnixStreamServer(str(self.socket_path), CliHandler)
        srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
        srv_thread.start()
        time.sleep(0.05)

        try:
            with (
                mock.patch.dict(os.environ, {"AUTO_SPEECH_DAEMON_SOCK": str(self.socket_path)}),
                mock.patch("sys.stdin", io.StringIO("integration text payload")),
            ):
                rc = speak.main([])
                self.assertEqual(rc, 0)
                self.assertTrue(recv_event.wait(timeout=2.0))
                self.assertEqual(received, ["integration text payload"])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
