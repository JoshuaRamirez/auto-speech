"""Unit tests for daemon_client.py (Milestone M3).

Tests DaemonClient methods, argument schemas, priority levels, retries,
socket resolution, and round-trip communication over UNIX domain sockets.
"""

from __future__ import annotations

import json
import os
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

from config_constants import DEFAULT_SPEED, DEFAULT_VOICE_ID  # noqa: E402
from daemon_client import (  # noqa: E402
    DEFAULT_SOCKET_PATH,
    DaemonClient,
    DaemonResponse,
    Priority,
)


class TestDaemonClientDataStructures(unittest.TestCase):
    """Tests Priority enum and DaemonResponse dataclass."""

    def test_priority_enum_values(self) -> None:
        self.assertEqual(int(Priority.USER_INTERRUPT), 1)
        self.assertEqual(int(Priority.EXPLICIT_MCP), 2)
        self.assertEqual(int(Priority.AUTOPLAY), 3)
        self.assertEqual(int(Priority.TOOL_NARRATION), 4)

    def test_daemon_response_defaults(self) -> None:
        resp = DaemonResponse(status="ok", action="speak")
        self.assertEqual(resp.status, "ok")
        self.assertEqual(resp.action, "speak")
        self.assertFalse(resp.cache_hit)
        self.assertEqual(resp.message, "")
        self.assertIsNone(resp.error_code)
        self.assertEqual(resp.queue_depth, 0)
        self.assertIsNone(resp.raw)


class TestDaemonClientSocketResolution(unittest.TestCase):
    """Tests resolution of socket_path."""

    def test_default_socket_path(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            client = DaemonClient()
            self.assertEqual(client.socket_path, DEFAULT_SOCKET_PATH)

    def test_env_var_override(self) -> None:
        custom_path = "/tmp/custom-auto-speech.sock"
        with mock.patch.dict(os.environ, {"AUTO_SPEECH_DAEMON_SOCK": custom_path}):
            client = DaemonClient()
            self.assertEqual(client.socket_path, Path(custom_path))

    def test_explicit_path_argument(self) -> None:
        custom_path = "/tmp/explicit.sock"
        client = DaemonClient(socket_path=custom_path)
        self.assertEqual(client.socket_path, Path(custom_path))


class TestDaemonClientUnitMethods(unittest.TestCase):
    """Unit tests with mocked socket communication."""

    def test_is_alive_false_when_socket_not_present(self) -> None:
        client = DaemonClient(socket_path="/tmp/nonexistent_socket_test.sock")
        self.assertFalse(client.is_alive())

    @mock.patch.object(DaemonClient, "status")
    def test_is_alive_true_when_status_ok(self, mock_status: mock.MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sock_path = Path(tmpdir) / "test.sock"
            sock_path.touch()
            # Mock is_socket
            with mock.patch.object(Path, "is_socket", return_value=True):
                client = DaemonClient(socket_path=sock_path)
                mock_status.return_value = DaemonResponse(status="ok", action="status")
                self.assertTrue(client.is_alive())

    @mock.patch.object(DaemonClient, "status")
    def test_is_alive_false_on_status_error(self, mock_status: mock.MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sock_path = Path(tmpdir) / "test.sock"
            sock_path.touch()
            with mock.patch.object(Path, "is_socket", return_value=True):
                client = DaemonClient(socket_path=sock_path)
                mock_status.side_effect = ConnectionError("down")
                self.assertFalse(client.is_alive())

    @mock.patch.object(DaemonClient, "_send_request")
    def test_speak_payload_defaults(self, mock_send: mock.MagicMock) -> None:
        client = DaemonClient()
        client.speak("hello world")
        mock_send.assert_called_once_with(
            {
                "action": "speak",
                "text": "hello world",
                "priority": int(Priority.EXPLICIT_MCP),
                "source_hash": None,
                "session_id": None,
                "voice_id": DEFAULT_VOICE_ID,
                "speed": DEFAULT_SPEED,
            }
        )

    @mock.patch.object(DaemonClient, "_send_request")
    def test_speak_payload_custom_args(self, mock_send: mock.MagicMock) -> None:
        client = DaemonClient()
        client.speak(
            "custom text",
            priority=Priority.AUTOPLAY,
            source_hash="a" * 64,
            session_id="sess-123",
            voice_id="af_bella",
            speed=1.5,
        )
        mock_send.assert_called_once_with(
            {
                "action": "speak",
                "text": "custom text",
                "priority": 3,
                "source_hash": "a" * 64,
                "session_id": "sess-123",
                "voice_id": "af_bella",
                "speed": 1.5,
            }
        )

    @mock.patch.object(DaemonClient, "_send_request")
    def test_play_cache_payload(self, mock_send: mock.MagicMock) -> None:
        client = DaemonClient()
        client.play_cache("b" * 64, priority=Priority.EXPLICIT_MCP, session_id="sess-456")
        mock_send.assert_called_once_with(
            {
                "action": "play_cache",
                "source_hash": "b" * 64,
                "priority": 2,
                "session_id": "sess-456",
            }
        )

    @mock.patch.object(DaemonClient, "_send_request")
    def test_interrupt_payload(self, mock_send: mock.MagicMock) -> None:
        client = DaemonClient()
        client.interrupt(session_id="sess-789")
        mock_send.assert_called_once_with(
            {
                "action": "interrupt",
                "session_id": "sess-789",
            }
        )

    @mock.patch.object(DaemonClient, "_send_request")
    def test_status_payload(self, mock_send: mock.MagicMock) -> None:
        client = DaemonClient()
        client.status()
        mock_send.assert_called_once_with({"action": "status"})


class TestDaemonClientLiveRoundTrip(unittest.TestCase):
    """Tests DaemonClient against a live ephemeral UNIX socket server."""

    def test_live_roundtrip_speak(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sock_path = Path(tmpdir) / "daemon.sock"

            received_requests: list[dict] = []

            class TestHandler(socketserver.BaseRequestHandler):
                def handle(self) -> None:
                    data = bytearray()
                    while True:
                        chunk = self.request.recv(4096)
                        if not chunk:
                            break
                        data.extend(chunk)
                        if b"\n" in data:
                            break
                    req = json.loads(data.decode("utf-8").strip())
                    received_requests.append(req)
                    resp = {
                        "status": "ok",
                        "action": req.get("action"),
                        "queue_depth": 1,
                        "cache_hit": False,
                    }
                    self.request.sendall(json.dumps(resp).encode("utf-8") + b"\n")

            server = socketserver.ThreadingUnixStreamServer(str(sock_path), TestHandler)
            srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
            srv_thread.start()
            time.sleep(0.05)

            try:
                client = DaemonClient(socket_path=sock_path)
                resp = client.speak("Test utterance from client")
                self.assertEqual(resp.status, "ok")
                self.assertEqual(resp.action, "speak")
                self.assertEqual(resp.queue_depth, 1)
                self.assertFalse(resp.cache_hit)
                self.assertEqual(len(received_requests), 1)
                self.assertEqual(received_requests[0]["text"], "Test utterance from client")
            finally:
                server.shutdown()
                server.server_close()

    def test_connection_failure_raises_connection_error(self) -> None:
        client = DaemonClient(socket_path="/tmp/nonexistent_test_sock_123.sock", max_retries=1)
        with self.assertRaises(ConnectionError):
            client.speak("Test fail")


if __name__ == "__main__":
    unittest.main()
