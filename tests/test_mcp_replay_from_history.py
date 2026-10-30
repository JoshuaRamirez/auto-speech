"""The MCP tool's replay plays from the speech history, not the old cache (T40).

T38 made `auto-speech replay` play the Nth most recent line of the speech
history; the MCP tool `playback_control` with action `replay` still took the
newest entry of the old content-addressed cache (config/cache/<hash>) and
sent it through `play_cache`. Here a private history holds two lines and a
private cache holds an unrelated clip that looks newer than both; the MCP
replay must target the newest history line (or the Nth), through the
narrator's `play_history` as the CLI does, never the cache, and refuse an
off, empty or short history, or a narrator too old for `play_history`, with
the tool's error shape.

Never the live socket or his history: a private narrator socket and stores
under /tmp. No worker runs, so nothing plays.
"""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import mcp_server  # noqa: E402
import narrator_service  # noqa: E402
from autoplay_gate import AutoplayGate  # noqa: E402
from cache_entry import CacheEntry  # noqa: E402
from cache_store import CacheStore  # noqa: E402
from daemon_client import DaemonClient, DaemonResponse  # noqa: E402
from missed_speech import MissedList  # noqa: E402
from speech_history import HistoryStore, SpeechHistory  # noqa: E402

SPOKEN_AT = time.mktime((2026, 10, 30, 14, 27, 0, 0, 0, -1))
CACHE_HASH = "c" * 64


def write_wav(path: Path, frames: int = 800) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * frames)
    return path


class McpReplayFixture(unittest.TestCase):
    """A private history with two lines (older, newer), found by the
    environment as the live MCP server finds it, and a private cache whose
    one clip looks newer than both."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="as-mcp-replay-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = patch.dict(os.environ, {
            "AUTO_SPEECH_HISTORY_DIR": str(self.tmp / "history"),
            "AUTO_SPEECH_HISTORY_CONFIG": str(self.tmp / "history.toml"),
            "AUTO_SPEECH_DAEMON_SOCK": str(self.tmp / "d.sock"),
        })
        env.start()
        self.addCleanup(env.stop)

        self.store = HistoryStore(self.tmp / "history.toml")
        self.history = SpeechHistory(self.tmp / "history", store=self.store)
        src = write_wav(self.tmp / "src.wav")
        self.older = self.history.export(src, text="The older line.", spoken_at=SPOKEN_AT)
        self.newer = self.history.export(src, text="The line spoken a minute ago.", spoken_at=SPOKEN_AT + 60)
        assert self.older is not None and self.newer is not None

        # The old code read the cache at mcp_server._default_cache_root(); point
        # it at a private cache with a clip that looks newer than both lines.
        self.cache_root = self.tmp / "cache"
        entry = CacheEntry(CACHE_HASH, "v1", 1.0, 10, 0.05, "2099-01-01T00:00:00Z", 15.0)
        with patch("sys.stdout", io.StringIO()):  # its "[cache] promoted" line
            CacheStore(self.cache_root).promote(CACHE_HASH, write_wav(self.tmp / "cache-src.wav"), entry)
        p = patch.object(mcp_server, "_default_cache_root", return_value=self.cache_root, create=True)
        p.start()
        self.addCleanup(p.stop)

        self.home = self.tmp / "home"
        (self.home / ".claude").mkdir(parents=True)

    def server(self, client) -> mcp_server.McpServer:
        """The server as it runs live: no history handed in, so it finds
        the (private) history from the environment."""
        return mcp_server.McpServer(
            spawner=lambda _text: None, gate=AutoplayGate(home=self.home), home=self.home, client=client,
        )

    def replay(self, client, **arguments) -> tuple[bool, str]:
        line = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "playback_control", "arguments": {"action": "replay", **arguments}},
        })
        result = self.server(client).handle_line(line)["result"]
        return result["isError"], result["content"][0]["text"]

    @staticmethod
    def queued_client() -> MagicMock:
        client = MagicMock()
        client.play_history.return_value = DaemonResponse(status="queued", action="play_history")
        client.play_cache.return_value = DaemonResponse(status="ok", action="play_cache")
        return client


class TestMcpReplayPicksTheHistoryLine(McpReplayFixture):
    def test_ordinal_one_asks_for_the_newest_history_line_not_the_cache_clip(self):
        client = self.queued_client()
        is_error, text = self.replay(client)
        self.assertFalse(is_error, text)
        client.play_history.assert_called_once_with(self.newer.id)
        client.play_cache.assert_not_called()
        self.assertIn("speech history line #1", text)

    def test_ordinal_two_asks_for_the_line_before_it(self):
        client = self.queued_client()
        is_error, text = self.replay(client, ordinal=2)
        self.assertFalse(is_error, text)
        client.play_history.assert_called_once_with(self.older.id)
        client.play_cache.assert_not_called()

    def test_an_ordinal_past_the_history_is_an_error_and_sends_nothing(self):
        client = self.queued_client()
        is_error, text = self.replay(client, ordinal=3)
        self.assertTrue(is_error)
        self.assertIn("holds 2 lines", text)
        client.play_history.assert_not_called()
        client.play_cache.assert_not_called()


class TestMcpReplayNeverFallsBackToTheCache(McpReplayFixture):
    def test_an_empty_history_is_an_error_with_a_clear_message(self):
        empty = self.tmp / "empty-history"
        empty.mkdir()
        client = self.queued_client()
        with patch.dict(os.environ, {"AUTO_SPEECH_HISTORY_DIR": str(empty)}):
            is_error, text = self.replay(client)
        self.assertTrue(is_error)
        self.assertIn("speech history is empty", text)
        client.play_history.assert_not_called()
        client.play_cache.assert_not_called()

    def test_a_history_switched_off_is_an_error_and_sends_nothing(self):
        self.store.set_enabled(False)
        client = self.queued_client()
        is_error, text = self.replay(client)
        self.assertTrue(is_error)
        self.assertIn("speech history is off", text)
        client.play_history.assert_not_called()
        client.play_cache.assert_not_called()


class TestMcpReplayThroughTheNarrator(McpReplayFixture):
    """Over a real Unix socket to a private narrator's socket server: the
    line is queued as the speech control's play-item queues it."""

    def setUp(self) -> None:
        super().setUp()
        for name, value in (("STOPPED_FILE", self.tmp / "stopped.json"), ("_log", lambda *_: None)):
            p = patch.object(narrator_service, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.svc = narrator_service.NarratorService(
            socket_path=self.tmp / "d.sock", sink=MagicMock(), synth=MagicMock(),
            config=narrator_service.load_config(), sound_yield_gate=MagicMock(),
            missed=MissedList(self.tmp / "missed.json"), speaker=MagicMock(),
            history=self.history, control=MagicMock(),
        )
        self.svc.__dict__["_cache_instance"] = CacheStore(self.cache_root)  # the narrator's cache: private too
        self.svc._speaker_status = lambda: None  # a stand-in speaker: nothing to name in its status
        self.svc._start_socket_server()
        self.addCleanup(self.svc._stop_socket_server)
        self.client = DaemonClient(socket_path=self.tmp / "d.sock", max_retries=0)

    def queued(self) -> list:
        return self.svc._priority_arbiter.waiting()

    def test_replay_queues_the_newest_history_wav_as_a_replay_of_its_line(self):
        is_error, text = self.replay(self.client)
        self.assertFalse(is_error, text)
        [item] = self.queued()
        self.assertEqual(Path(item.payload), self.newer.wav)
        self.assertEqual(item.history_id, self.newer.id, "a replay of a heard line, not a new one")

    def test_an_older_narrator_is_never_sent_play_history_and_is_named(self):
        # An older narrator's status has no `replays_history`; sent the action
        # it does not know, it would speak the request's JSON aloud.
        sent: list[str] = []

        def older(payload: dict) -> dict:
            sent.append(payload.get("action"))
            return {"status": "ok", "daemon_state": "IDLE"}

        with patch.object(self.svc, "dispatch_json", side_effect=older):
            is_error, text = self.replay(self.client)
        self.assertTrue(is_error)
        self.assertIn("auto-speech narrate restart", text)
        self.assertNotIn("play_history", sent)
        self.assertNotIn("play_cache", sent)
        self.assertEqual(self.queued(), [])


if __name__ == "__main__":
    unittest.main()
