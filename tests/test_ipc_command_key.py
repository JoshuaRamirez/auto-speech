#!/usr/bin/env python3
"""A JSON line whose "command" names a known control is answered, never spoken.

The case: on 2026-10-18 17:50 a status probe sent {"command":"status"} to the
live socket. The server only knew "action", so the line fell through to Wire 2
(legacy text) and the daemon spoke it aloud at 17:51:00. The Desk's call
(17:52): a 'command' key works like 'action'.

Hermetic: a private socket under a temp dir; a recording dispatcher stands in
for the daemon, so nothing is synthesised or played.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parent.parent / "plugin" / "scripts" / "python"
sys.path.insert(0, str(PLUGIN_ROOT))

os.environ.setdefault("AUTO_SPEECH_TMP_ROOT", tempfile.mkdtemp(prefix="auto-speech-ipc-command-key-"))

from unix_ipc_server import CONTROLS, _DaemonSocketServer, control_payload  # noqa: E402


class _RecordingDispatcher:
    """Records what the server routes where; answers controls like the daemon would."""

    def __init__(self) -> None:
        self.controls: list[dict[str, Any]] = []
        self.spoken: list[str] = []

    def dispatch_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.controls.append(payload)
        return {"status": "ok", "action": payload.get("action"), "daemon_state": "IDLE"}

    def dispatch_legacy_text(self, text: str) -> None:
        self.spoken.append(text)


def _send(sock_path: Path, line: bytes) -> bytes:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(3.0)
    try:
        s.connect(str(sock_path))
        s.sendall(line)
        s.shutdown(socket.SHUT_WR)
        out = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            out += chunk
        return out
    finally:
        s.close()


class TestControlPayload(unittest.TestCase):
    def test_action_and_command_name_the_same_control(self) -> None:
        for control in CONTROLS:
            self.assertEqual(control_payload({"action": control})["action"], control)
            self.assertEqual(control_payload({"command": control})["action"], control)

    def test_the_17_51_line_is_a_status_request(self) -> None:
        payload = control_payload(json.loads('{"command":"status"}'))
        self.assertEqual(payload, {"command": "status", "action": "status"})

    def test_action_wins_when_both_keys_are_present(self) -> None:
        self.assertEqual(control_payload({"action": "status", "command": "speak"})["action"], "status")

    def test_the_callers_dict_is_left_alone(self) -> None:
        original = {"command": "status"}
        control_payload(original)
        self.assertEqual(original, {"command": "status"})

    def test_unknown_or_non_string_controls_are_not_requests(self) -> None:
        for parsed in (
            {"command": "dance"},
            {"action": "dance"},
            {"command": 3},
            {"text": "hello"},
            ["status"],
            "status",
            None,
        ):
            self.assertIsNone(control_payload(parsed), parsed)


class TestServerAnswersCommandKey(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="auto-speech-ipc-command-key-")
        self.sock_path = Path(self.temp_dir.name) / "daemon.sock"
        self.dispatcher = _RecordingDispatcher()
        self.server = _DaemonSocketServer(self.sock_path, dispatcher=self.dispatcher)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        time.sleep(0.05)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)
        self.temp_dir.cleanup()

    def test_command_status_is_answered_and_never_spoken(self) -> None:
        reply = _send(self.sock_path, b'{"command":"status"}\n')
        self.assertEqual(json.loads(reply.decode("utf-8").strip())["action"], "status")
        self.assertEqual(self.dispatcher.spoken, [])
        self.assertEqual([p["action"] for p in self.dispatcher.controls], ["status"])

    def test_command_status_without_a_newline_is_still_answered(self) -> None:
        reply = _send(self.sock_path, b'{"command":"status"}')
        self.assertEqual(json.loads(reply.decode("utf-8").strip())["action"], "status")
        self.assertEqual(self.dispatcher.spoken, [])

    def test_action_status_still_works_as_before(self) -> None:
        reply = _send(self.sock_path, b'{"action":"status"}\n')
        self.assertEqual(json.loads(reply.decode("utf-8").strip())["action"], "status")
        self.assertEqual(self.dispatcher.spoken, [])

    def test_a_json_looking_line_that_names_no_control_is_still_spoken(self) -> None:
        reply = _send(self.sock_path, b'{"command":"dance"}\n')
        self.assertEqual(reply, b"OK\n")
        self.assertEqual(self.dispatcher.controls, [])
        self.assertEqual(self.dispatcher.spoken, ['{"command":"dance"}'])


class TestEveryHandledActionIsAControl(unittest.TestCase):
    """2026-10-30 10:18: `auto-speech queue` sent {"action":"queue"}; the daemon
    handled "queue" but the socket did not list it, so the line fell through
    to Wire 2 and was spoken aloud. Every action dispatch_json handles must be
    one the socket routes to it."""

    def test_every_action_dispatch_json_handles_is_in_controls(self) -> None:
        import re
        source = (PLUGIN_ROOT / "narrator_service.py").read_text(encoding="utf-8")
        start = source.index("def dispatch_json(")
        end = source.index("\n    def ", start + 1)
        handled = set(re.findall(r'action == "([a-z_]+)"', source[start:end]))
        self.assertIn("speak", handled, "the reading of dispatch_json found its actions")
        self.assertEqual(sorted(handled - set(CONTROLS)), [], "handled by the daemon but spoken by the socket")

    def test_a_queue_request_is_answered_never_spoken(self) -> None:
        self.assertIsNotNone(control_payload({"action": "queue"}))


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    count = result.testsRun
    if result.wasSuccessful():
        print(f"ipc_command_key: {count} tests passed")
        return 0
    print(f"ipc_command_key: {len(result.failures) + len(result.errors)} of {count} failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
