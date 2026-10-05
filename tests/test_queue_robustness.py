"""Integration and robustness tests for auto-speech single central queue.

Verifies:
  1. Multiple sequential messages are queued in FIFO order without drops.
  2. Active speech playback is NEVER interrupted or cut off by incoming messages.
  3. AutoplayWorker does not bail due to staleness when new Stop events arrive after coalesce.
  4. Explicit MCP speak and autoplay requests are queued sequentially.
"""

from __future__ import annotations

import queue
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import autoplay_worker as awmod
from autoplay_worker import AutoplayWorker
from dedup_guard import DedupGuard
from narrator_service import NarratorService
from priority_arbiter import Priority, PriorityArbiter, QueueItem
from worker_lifecycle import DONE


def test_active_speech_never_interrupted_by_new_messages() -> None:
    """Active playback must not be interrupted when new speak requests arrive."""
    svc = NarratorService.__new__(NarratorService)
    svc._tts_queue = queue.Queue(maxsize=10)
    svc._priority_arbiter = PriorityArbiter(maxsize=10)
    svc._engine_state = "PLAYING"
    svc._active_priority = Priority.AUTOPLAY
    svc._state_lock = threading.Lock()
    svc._last_event_ts = time.time()
    svc._update_depth = lambda _d: None
    sink = MagicMock()
    svc._sink = sink

    # Message 1 arrives via explicit MCP speak
    resp1 = svc.dispatch_json({
        "action": "speak",
        "text": "first queued message",
        "priority": int(Priority.EXPLICIT_MCP),
    })
    assert resp1["status"] in ("ok", "queued")
    sink.interrupt.assert_not_called()

    # Message 2 arrives via autoplay
    resp2 = svc.dispatch_json({
        "action": "speak",
        "text": "second queued message",
        "priority": int(Priority.AUTOPLAY),
    })
    assert resp2["status"] in ("ok", "queued")
    sink.interrupt.assert_not_called()

    # Both items must be in the queue in arrival order
    assert svc._tts_queue.qsize() == 2
    item1 = svc._tts_queue.get_nowait()
    item2 = svc._tts_queue.get_nowait()
    assert getattr(item1, "payload", item1) == "first queued message"
    assert getattr(item2, "payload", item2) == "second queued message"


def test_autoplay_worker_queues_consecutive_turns_without_staleness_drop() -> None:
    """Consecutive turns in the same session must both resolve and speak without dropping."""
    spoken_messages = []

    def mock_runner(argv, stdin_text=None, stdout_path=None):
        prog = (
            argv[1]
            if (
                argv[0] in ("bash", sys.executable, "python", "python3")
                or Path(argv[0]).name.startswith("python")
            )
            else argv[0]
        )
        if str(prog).endswith("run_extract.sh"):
            # Return current message
            return 0, f"Turn content {len(spoken_messages) + 1} with enough length"
        if str(prog).endswith("compute_hash.sh"):
            h = f"{len(spoken_messages) + 1:064x}"
            return 0, h + "\n"
        if str(prog).endswith("speak.py"):
            spoken_messages.append((argv[argv.index("--source-hash") + 1], stdin_text))
            return 0, None
        raise AssertionError(f"unexpected argv: {argv}")

    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-robust-test-"))
    orig_root = awmod._PROJECT_ROOT
    awmod._PROJECT_ROOT = tmp

    class _ControllableBeacon:
        def __init__(self):
            self.stale_count = 0

        def is_stale(self):
            # Never stale during coalesce, simulate beacon advance afterwards
            self.stale_count += 1
            return self.stale_count > 1

    try:
        # Worker 1 runs Turn 1
        w1 = AutoplayWorker(
            beacon_mtime=100.0,
            transcript_path="/fake/transcript.jsonl",
            session_id="session-test",
            config={"coalesce_seconds": 0, "narration_wait_max": 90, "queue_wait_max": 600, "min_len": 10, "mode": "raw", "bypass_llm": True},
            gate=awmod.AutoplayGate(home=tmp),
            beacon=_ControllableBeacon(),
            dedup=DedupGuard(marker_path=tmp / "marker", mpv_pid_path=tmp / "mpv.pid", lock_path=tmp / "lock"),
            runner=mock_runner,
            sleep=lambda s: None,
        )
        assert w1.run() == 0
        assert w1.machine.state == DONE

        # Worker 2 runs Turn 2 (with a newer beacon mtime)
        w2 = AutoplayWorker(
            beacon_mtime=200.0,
            transcript_path="/fake/transcript.jsonl",
            session_id="session-test",
            config={"coalesce_seconds": 0, "narration_wait_max": 90, "queue_wait_max": 600, "min_len": 10, "mode": "raw", "bypass_llm": True},
            gate=awmod.AutoplayGate(home=tmp),
            beacon=_ControllableBeacon(),
            dedup=DedupGuard(marker_path=tmp / "marker", mpv_pid_path=tmp / "mpv.pid", lock_path=tmp / "lock"),
            runner=mock_runner,
            sleep=lambda s: None,
        )
        assert w2.run() == 0
        assert w2.machine.state == DONE
    finally:
        awmod._PROJECT_ROOT = orig_root

    # Both turns were spoken/queued! Neither was dropped!
    assert len(spoken_messages) == 2
    assert "Turn content 1" in spoken_messages[0][1]
    assert "Turn content 2" in spoken_messages[1][1]
