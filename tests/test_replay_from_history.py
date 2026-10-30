"""`auto-speech replay` plays from the speech history, not the old cache (T38, #m1030-1429).

On 2026-10-30 at 14:28 `auto-speech replay --ordinal 1` played a clip made on
2026-10-19 instead of the line spoken a minute earlier: handle_replay took the
newest entry of the old content-addressed cache (config/cache/<hash>/full.wav),
not the speech history the speech player lists. Here a private history holds
two lines and a private cache holds an unrelated clip that looks newer than
both; replay must target the newest history line (or the Nth), through the
narrator as the speech control's play-item does, and never the cache.

Never the live socket or his history: a private narrator socket and stores
under /tmp. No worker runs, so nothing plays; the local player is a mock.
"""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
import io
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

import cli  # noqa: E402
import narrator_service  # noqa: E402
from cache_entry import CacheEntry  # noqa: E402
from cache_store import CacheStore  # noqa: E402
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


class ReplayFixture(unittest.TestCase):
    """A private history with two lines (older, newer) and a private cache
    whose one clip looks newer than both."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="as-replay-", dir="/tmp"))
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

        self.cache_root = self.tmp / "cache"
        cache_src = write_wav(self.tmp / "cache-src.wav")
        entry = CacheEntry(CACHE_HASH, "v1", 1.0, 10, 0.05, "2099-01-01T00:00:00Z", 15.0)
        with patch("sys.stdout", io.StringIO()):  # its "[cache] promoted" line
            self.cache_wav = CacheStore(self.cache_root).promote(CACHE_HASH, cache_src, entry)
        p = patch.object(cli, "_default_cache_root", return_value=self.cache_root)
        p.start()
        self.addCleanup(p.stop)

        # Nothing ever plays on this Mac: the local player is a mock.
        self.local_sink = MagicMock()
        p = patch.object(cli, "NativeAudioSink", return_value=self.local_sink)
        p.start()
        self.addCleanup(p.stop)

    def replay(self, *argv: str, **kw) -> tuple[int, str]:
        err = io.StringIO()
        with patch("sys.stderr", err):
            rc = cli.main(["replay", *argv], **kw)
        return rc, err.getvalue()


class TestReplayPicksTheHistoryLine(ReplayFixture):
    def test_ordinal_one_plays_the_newest_history_line_not_the_cache_clip(self):
        sink = MagicMock()
        rc, err = self.replay(sink=sink)
        self.assertEqual(rc, 0, err)
        sink.play.assert_called_once()
        played = Path(sink.play.call_args.args[0])
        self.assertEqual(played, self.newer.wav)
        self.assertNotEqual(played, self.cache_wav)

    def test_ordinal_two_plays_the_line_before_it(self):
        sink = MagicMock()
        rc, err = self.replay("--ordinal", "2", sink=sink)
        self.assertEqual(rc, 0, err)
        self.assertEqual(Path(sink.play.call_args.args[0]), self.older.wav)

    def test_through_a_client_it_asks_for_the_history_line_by_id(self):
        client = MagicMock()
        client.play_history.return_value = MagicMock(status="queued", message="")
        rc, err = self.replay(client=client)
        self.assertEqual(rc, 0, err)
        client.play_history.assert_called_once_with(self.newer.id)
        client.play_cache.assert_not_called()

    def test_an_ordinal_past_the_history_exits_two_and_plays_nothing(self):
        sink = MagicMock()
        rc, err = self.replay("--ordinal", "3", sink=sink)
        self.assertEqual(rc, 2)
        self.assertIn("holds 2 lines", err)
        sink.play.assert_not_called()


class TestReplayNeverFallsBackToTheCache(ReplayFixture):
    def test_an_empty_history_exits_two_with_a_clear_message(self):
        empty = self.tmp / "empty-history"
        empty.mkdir()
        sink = MagicMock()
        with patch.dict(os.environ, {"AUTO_SPEECH_HISTORY_DIR": str(empty)}):
            rc, err = self.replay(sink=sink)
        self.assertEqual(rc, 2)
        self.assertIn("speech history is empty", err)
        sink.play.assert_not_called()
        self.local_sink.play.assert_not_called()

    def test_a_history_switched_off_exits_two_and_plays_nothing(self):
        self.store.set_enabled(False)
        sink = MagicMock()
        rc, err = self.replay(sink=sink)
        self.assertEqual(rc, 2)
        self.assertIn("speech history is off", err)
        sink.play.assert_not_called()
        self.local_sink.play.assert_not_called()


class TestReplayThroughTheNarrator(ReplayFixture):
    """Over a real Unix socket to a narrator's socket server: the line is
    queued as the speech control's play-item queues it."""

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

    def queued(self) -> list:
        return self.svc._priority_arbiter.waiting()

    def test_replay_queues_the_newest_history_wav_as_a_replay_of_its_line(self):
        rc, err = self.replay()
        self.assertEqual(rc, 0, err)
        [item] = self.queued()
        self.assertEqual(Path(item.payload), self.newer.wav)
        self.assertEqual(item.history_id, self.newer.id, "a replay of a heard line, not a new one")
        self.local_sink.play.assert_not_called()

    def test_the_socket_refuses_an_unknown_history_line(self):
        reply = self.svc.dispatch_json({"action": "play_history", "line_id": "nosuchid"})
        self.assertEqual((reply["status"], reply["error_code"]), ("error", "HISTORY_MISS"))
        self.assertEqual(self.queued(), [])

    def test_an_older_narrator_is_never_sent_play_history_and_is_named(self):
        # An older narrator's status has no `replays_history`; sent the action
        # it does not know, it would speak the request's JSON aloud.
        sent: list[str] = []

        def older(payload: dict) -> dict:
            sent.append(payload.get("action"))
            return {"status": "ok", "daemon_state": "IDLE"}

        with patch.object(self.svc, "dispatch_json", side_effect=older):
            rc, err = self.replay()
        self.assertEqual(rc, 1)
        self.assertIn("auto-speech narrate restart", err)
        self.assertNotIn("play_history", sent)
        self.assertEqual(self.queued(), [])
        self.local_sink.play.assert_not_called()


if __name__ == "__main__":
    unittest.main()
