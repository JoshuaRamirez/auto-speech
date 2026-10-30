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

from quiet_audio import ensure_quiet  # noqa: E402

ensure_quiet()  # the suite never plays sound unless AUTO_SPEECH_TEST_REAL_AUDIO=1

# Isolation: never touch the live service's files when run standalone; the
# runner (tests/run_all.sh) sets AUTO_SPEECH_TMP_ROOT itself.
os.environ.setdefault("AUTO_SPEECH_TMP_ROOT", tempfile.mkdtemp(prefix="auto-speech-tests-"))

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
            mock_send.assert_called_once_with("test legacy", source_hash=valid_hash)

    def test_source_hash_normalized_to_lowercase(self) -> None:
        """Uppercase 64-hex SHA-256 hash is accepted and normalized."""
        upper_hash = "AB12" * 16
        with (
            mock.patch("speak.send_speech_request", return_value=0) as mock_send,
            mock.patch("sys.stdin", io.StringIO("normalized")),
        ):
            rc = speak.main(["--source-hash", upper_hash])
            self.assertEqual(rc, 0)
            mock_send.assert_called_once_with("normalized", source_hash=upper_hash.lower())

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

    def test_empty_stdin_with_source_hash_plays_cached_wav(self) -> None:
        """When stdin is empty and --source-hash exists in cache, routes to DaemonClient (or injected sink)."""
        valid_hash = "c" * 64
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            mock.patch("sys.stdin", io.StringIO("")),
            mock.patch("speak.DaemonClient") as mock_client_cls,
        ):
            mock_client = mock.MagicMock()
            mock_client.play_cache.return_value = speak.DaemonResponse(status="ok", action="play_cache")
            mock_client_cls.return_value = mock_client

            with mock.patch("pathlib.Path.resolve") as mock_resolve:
                mock_resolve.return_value = Path(tmpdir) / "plugin" / "scripts" / "python" / "speak.py"
                target_wav = Path(tmpdir) / "config" / "cache" / valid_hash[:16] / "full.wav"
                target_wav.parent.mkdir(parents=True, exist_ok=True)
                target_wav.touch()

                rc = speak.main(["--source-hash", valid_hash])
                self.assertEqual(rc, 0)
                mock_client.play_cache.assert_called_once_with(valid_hash)

    def test_empty_stdin_with_injected_sink_plays_cached_wav(self) -> None:
        """When an explicit sink is injected, plays cached full.wav through it."""
        valid_hash = "c" * 64
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            mock.patch("sys.stdin", io.StringIO("")),
        ):
            mock_sink = mock.MagicMock()
            with mock.patch("pathlib.Path.resolve") as mock_resolve:
                mock_resolve.return_value = Path(tmpdir) / "plugin" / "scripts" / "python" / "speak.py"
                target_wav = Path(tmpdir) / "config" / "cache" / valid_hash[:16] / "full.wav"
                target_wav.parent.mkdir(parents=True, exist_ok=True)
                target_wav.touch()

                rc = speak.main(["--source-hash", valid_hash], sink=mock_sink)
                self.assertEqual(rc, 0)
                mock_sink.play.assert_called_once_with(target_wav)

    def test_empty_stdin_with_source_hash_cache_miss_returns_zero(self) -> None:
        """When stdin is empty and --source-hash does not exist in cache, returns 0 without playing."""
        valid_hash = "d" * 64
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            mock.patch("sys.stdin", io.StringIO("")),
        ):
            mock_sink = mock.MagicMock()
            with mock.patch("pathlib.Path.resolve") as mock_resolve:
                mock_resolve.return_value = Path(tmpdir) / "plugin" / "scripts" / "python" / "speak.py"
                rc = speak.main(["--source-hash", valid_hash], sink=mock_sink)
                self.assertEqual(rc, 0)
                mock_sink.play.assert_not_called()

    def test_empty_stdin_with_source_hash_playback_failure_returns_one(self) -> None:
        """When cached playback raises an exception, logs error and returns exit code 1."""
        valid_hash = "e" * 64
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            mock.patch("sys.stdin", io.StringIO("")),
            mock.patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
        ):
            mock_sink = mock.MagicMock()
            mock_sink.play.side_effect = RuntimeError("audio device failure")

            with mock.patch("pathlib.Path.resolve") as mock_resolve:
                mock_resolve.return_value = Path(tmpdir) / "plugin" / "scripts" / "python" / "speak.py"
                target_wav = Path(tmpdir) / "config" / "cache" / valid_hash[:16] / "full.wav"
                target_wav.parent.mkdir(parents=True, exist_ok=True)
                target_wav.touch()

                rc = speak.main(["--source-hash", valid_hash], sink=mock_sink)
                self.assertEqual(rc, 1)
                self.assertIn("cached playback failed", mock_stderr.getvalue())


class TestSpeakClientSocketResolution(unittest.TestCase):
    """Unit tests for daemon socket path resolution."""

    def test_default_socket_path(self) -> None:
        """When AUTO_SPEECH_DAEMON_SOCK is not set, defaults to the state
        directory's auto-speech-daemon.sock (AUTO_SPEECH_TMP_ROOT, /tmp live)."""
        expected = Path(speak.DEFAULT_SOCKET_PATH)  # resolved at import under AUTO_SPEECH_TMP_ROOT
        with mock.patch.dict(os.environ, {}, clear=True):
            sock_path = speak.get_socket_path()
        self.assertEqual(sock_path, expected)
        self.assertEqual(sock_path.name, "auto-speech-daemon.sock")
        self.assertEqual(sock_path.parent, Path(os.environ["AUTO_SPEECH_TMP_ROOT"]))

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


class TestSpeakClientStructuredWire1(unittest.TestCase):
    """Unit tests verifying DaemonClient Wire 1 structured routing in speak.py."""

    @mock.patch("speak.DaemonClient")
    def test_send_speech_request_forwards_source_hash_via_daemon_client(
        self, mock_client_cls: mock.MagicMock
    ) -> None:
        """When source_hash is provided, send_speech_request prefers DaemonClient structured speak."""
        mock_client = mock.MagicMock()
        mock_client.speak.return_value = speak.DaemonResponse(status="ok", action="speak")
        mock_client_cls.return_value = mock_client

        valid_hash = "f" * 64
        rc = speak.send_speech_request(
            "Text with hash",
            source_hash=valid_hash,
            priority=speak.Priority.EXPLICIT_MCP,
        )
        self.assertEqual(rc, 0)
        mock_client.speak.assert_called_once_with(
            "Text with hash",
            source_hash=valid_hash,
            priority=speak.Priority.EXPLICIT_MCP,
        )

    @mock.patch("speak.DaemonClient")
    @mock.patch("socket.socket")
    def test_send_speech_request_falls_back_to_raw_socket_if_daemon_client_fails(
        self, mock_socket_cls: mock.MagicMock, mock_client_cls: mock.MagicMock
    ) -> None:
        """If DaemonClient structured speak raises an exception, falls back to raw socket transmission."""
        mock_client = mock.MagicMock()
        mock_client.speak.side_effect = RuntimeError("DaemonClient failed")
        mock_client_cls.return_value = mock_client

        mock_sock_inst = mock.MagicMock()
        mock_socket_cls.return_value = mock_sock_inst

        target_sock = "/tmp/test-daemon.sock"
        valid_hash = "e" * 64
        rc = speak.send_speech_request(
            "Fallback text",
            socket_path=target_sock,
            source_hash=valid_hash,
        )
        self.assertEqual(rc, 0)
        mock_sock_inst.connect.assert_called_once_with(target_sock)
        mock_sock_inst.sendall.assert_called_once_with(b"Fallback text")

    def test_speak_main_never_imports_or_instantiates_native_audio_sink(self) -> None:
        """speak.main must route through DaemonClient and never instantiate NativeAudioSink directly."""
        valid_hash = "d" * 64
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            mock.patch("sys.stdin", io.StringIO("")),
            mock.patch("speak.DaemonClient") as mock_client_cls,
            mock.patch.dict("sys.modules", {"native_audio_sink": mock.MagicMock()}),
        ):
            mock_client = mock.MagicMock()
            mock_client.play_cache.return_value = speak.DaemonResponse(status="queued", action="play_cache")
            mock_client_cls.return_value = mock_client

            with mock.patch("pathlib.Path.resolve") as mock_resolve:
                mock_resolve.return_value = Path(tmpdir) / "plugin" / "scripts" / "python" / "speak.py"
                target_wav = Path(tmpdir) / "config" / "cache" / valid_hash[:16] / "full.wav"
                target_wav.parent.mkdir(parents=True, exist_ok=True)
                target_wav.touch()

                rc = speak.main(["--source-hash", valid_hash])
                self.assertEqual(rc, 0)
                mock_client.play_cache.assert_called_once_with(valid_hash)
                import native_audio_sink
                native_audio_sink.NativeAudioSink.assert_not_called()

    def test_send_speech_request_forwards_session_id_and_priority(self) -> None:
        """send_speech_request forwards session_id and priority via DaemonClient."""
        with mock.patch("speak.DaemonClient") as mock_client_cls:
            mock_client = mock.MagicMock()
            mock_client.speak.return_value = speak.DaemonResponse(status="ok", action="speak")
            mock_client_cls.return_value = mock_client

            rc = speak.send_speech_request(
                "Priority text",
                session_id="sess-xyz",
                priority=speak.Priority.AUTOPLAY,
            )
            self.assertEqual(rc, 0)
            mock_client.speak.assert_called_once_with(
                "Priority text",
                source_hash=None,
                priority=speak.Priority.AUTOPLAY,
                session_id="sess-xyz",
            )

    def test_speak_main_forwards_session_id_and_priority_to_send(self) -> None:
        """speak.main forwards --session-id and --priority to send_speech_request."""
        with (
            mock.patch("speak.send_speech_request", return_value=0) as mock_send,
            mock.patch("sys.stdin", io.StringIO("cli text")),
        ):
            rc = speak.main(["--session-id", "sess-123", "--priority", "1"])
            self.assertEqual(rc, 0)
            mock_send.assert_called_once_with("cli text", session_id="sess-123", priority=1)

    def test_empty_stdin_with_source_hash_and_priority_calls_play_cache(self) -> None:
        """When empty stdin and --source-hash with --priority is provided, play_cache is invoked with priority."""
        valid_hash = "e" * 64
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            mock.patch("sys.stdin", io.StringIO("")),
        ):
            mock_client = mock.MagicMock()
            mock_client.play_cache.return_value = speak.DaemonResponse(status="ok", action="play_cache")
            with mock.patch("pathlib.Path.resolve") as mock_resolve:
                mock_resolve.return_value = Path(tmpdir) / "plugin" / "scripts" / "python" / "speak.py"
                target_wav = Path(tmpdir) / "config" / "cache" / valid_hash[:16] / "full.wav"
                target_wav.parent.mkdir(parents=True, exist_ok=True)
                target_wav.touch()

                rc = speak.main(
                    ["--source-hash", valid_hash, "--priority", "3", "--session-id", "sess-abc"],
                    client=mock_client,
                )
                self.assertEqual(rc, 0)
                mock_client.play_cache.assert_called_once_with(
                    valid_hash,
                    session_id="sess-abc",
                    priority=3,
                )


def setUpModule() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from narrator_reaper import guard_start

    guard_start()


def tearDownModule() -> None:
    """Every narrator a test here started (the client's auto-start runs the
    start script) is stopped, even after a failure, and none may survive
    (#m1030-1044-b20b)."""
    from narrator_reaper import guard_end_or_fail

    guard_end_or_fail()


if __name__ == "__main__":
    unittest.main()

