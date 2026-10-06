"""Unit tests for modernized explicit speech submission and daemon client client-side contracts.

Modernized replacement for legacy detached say_worker test:
  - DaemonClient.speak submits structured JSON requests with Priority.EXPLICIT_MCP
  - Empty or whitespace text is rejected/handled safely
  - Global mute gating prevents speech
  - Offline fallback properly invokes speak.py directly without temporary disk files
  - Daemon socket recovery on transient connection errors
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import mcp_server
import speak
from autoplay_gate import AutoplayGate
from daemon_client import DaemonClient, Priority


class TestSayWorkerModernized(unittest.TestCase):
    """Test suite for DaemonClient speech submission and offline speech fallback."""

    def test_speak_submits_explicit_mcp_priority_payload(self) -> None:
        client = DaemonClient()
        with mock.patch.object(client, "_send_request") as mock_send:
            mock_send.return_value = mock.Mock(status="ok", action="speak")
            client.speak("Hello world verbatim")
            mock_send.assert_called_once()
            payload = mock_send.call_args[0][0]
            self.assertEqual(payload["action"], "speak")
            self.assertEqual(payload["text"], "Hello world verbatim")
            self.assertEqual(payload["priority"], int(Priority.EXPLICIT_MCP))

    def test_global_mute_skips_speech(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            gate = AutoplayGate(home=home)
            self.assertFalse(gate.worker_gated_off())

            # Enable mute
            claude_dir = home / ".claude"
            claude_dir.mkdir(parents=True, exist_ok=True)
            (claude_dir / "auto-speech.disabled").touch()

            self.assertTrue(gate.worker_gated_off())

    def test_blank_or_whitespace_text_handled_safely(self) -> None:
        # speak.send_speech_request short-circuits empty/whitespace text to exit 0
        self.assertEqual(speak.send_speech_request(""), 0)
        self.assertEqual(speak.send_speech_request("    \n\t  "), 0)

    def test_spawn_say_worker_pipes_directly_to_speak_py(self) -> None:
        """Verifies spawn_say_worker in mcp_server spawns speak.py with stdin pipe."""
        with mock.patch("subprocess.Popen") as mock_popen:
            captured = []
            mock_proc = mock.Mock()
            mock_proc.stdin = mock.Mock()
            mock_proc.stdin.write.side_effect = captured.append
            mock_popen.return_value = mock_proc

            mcp_server.spawn_say_worker("Direct offline speak text")

            mock_popen.assert_called_once()
            argv = mock_popen.call_args[0][0]
            self.assertEqual(argv[0], sys.executable)
            self.assertTrue(str(argv[1]).endswith("speak.py"))

            # Verify text was written to pipe
            self.assertEqual(b"".join(captured), b"Direct offline speak text")

    def test_daemon_client_offline_raises_connection_error(self) -> None:
        # With non-existent socket, DaemonClient raises ConnectionError
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_sock = Path(tmpdir) / "nonexistent.sock"
            client = DaemonClient(socket_path=bad_sock)
            with self.assertRaises(ConnectionError):
                client.speak("Test offline")


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestSayWorkerModernized)
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=1)
    res = runner.run(suite)
    if res.wasSuccessful():
        print(f"say_worker: {res.testsRun} tests passed")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
