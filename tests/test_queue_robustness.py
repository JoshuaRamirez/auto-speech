"""Integration and robustness tests for auto-speech single central queue.

Verifies:
  1. Multiple sequential messages are queued in FIFO order without drops.
  2. Active speech playback is NEVER interrupted or cut off by incoming messages.
  3. AutoplayWorker does not bail due to staleness when new Stop events arrive after coalesce.
  4. Explicit MCP speak and autoplay requests are queued sequentially.
"""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
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

from quiet_audio import ensure_quiet  # noqa: E402

ensure_quiet()  # the suite never plays sound unless AUTO_SPEECH_TEST_REAL_AUDIO=1

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


def test_autoplay_worker_speaks_even_when_coalesce_beacon_is_immediately_stale() -> None:
    """When a worker experiences staleness during coalesce, it must still extract and speak the turn."""
    spoken = []

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
            return 0, "Turn message arriving during coalesce burst"
        if str(prog).endswith("compute_hash.sh"):
            return 0, "c0a1e5ce" + "0" * 56 + "\n"
        if str(prog).endswith("speak.py"):
            spoken.append(stdin_text)
            return 0, None
        raise AssertionError(f"unexpected argv: {argv}")

    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-coalesce-stale-"))
    orig_root = awmod._PROJECT_ROOT
    awmod._PROJECT_ROOT = tmp

    try:
        # Beacon is ALWAYS stale (e.g. Stop event burst fired right away)
        stale_beacon = MagicMock()
        stale_beacon.is_stale.return_value = True

        w = AutoplayWorker(
            beacon_mtime=50.0,
            transcript_path="/fake/transcript.jsonl",
            session_id="session-burst-test",
            config={"coalesce_seconds": 0.01, "narration_wait_max": 90, "queue_wait_max": 600, "min_len": 10, "mode": "raw", "bypass_llm": True},
            gate=awmod.AutoplayGate(home=tmp),
            beacon=stale_beacon,
            dedup=DedupGuard(marker_path=tmp / "marker", mpv_pid_path=tmp / "mpv.pid", lock_path=tmp / "lock"),
            runner=mock_runner,
            sleep=lambda s: None,
        )
        assert w.run() == 0
        assert w.machine.state == DONE
        assert len(spoken) == 1
        assert "Turn message arriving during coalesce burst" in spoken[0]
    finally:
        awmod._PROJECT_ROOT = orig_root


def test_autoplay_worker_uses_real_in_process_fifo() -> None:
    """AutoplayWorker using the real InProcessFifo (not stubbed) completes cleanly."""
    spoken = []

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
            return 0, "Real InProcessFifo message turn that is long enough"
        if str(prog).endswith("compute_hash.sh"):
            return 0, "1" * 64 + "\n"
        if str(prog).endswith("speak.py"):
            spoken.append(stdin_text)
            return 0, None
        raise AssertionError(f"unexpected argv: {argv}")

    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-real-fifo-"))
    orig_root = awmod._PROJECT_ROOT
    awmod._PROJECT_ROOT = tmp

    try:
        w = AutoplayWorker(
            beacon_mtime=100.0,
            transcript_path="/fake/transcript.jsonl",
            session_id="session-fifo-test",
            config={"coalesce_seconds": 0, "narration_wait_max": 90, "queue_wait_max": 600, "min_len": 10, "mode": "raw", "bypass_llm": True},
            gate=awmod.AutoplayGate(home=tmp),
            beacon=MagicMock(is_stale=lambda: False),
            fifo=awmod.InProcessFifo(),  # REAL InProcessFifo!
            dedup=DedupGuard(marker_path=tmp / "marker", mpv_pid_path=tmp / "mpv.pid", lock_path=tmp / "lock"),
            runner=mock_runner,
            sleep=lambda s: None,
        )
        assert w.run() == 0
        assert w.machine.state == DONE
    finally:
        awmod._PROJECT_ROOT = orig_root

    assert len(spoken) == 1
    assert "Real InProcessFifo message" in spoken[0]


def test_tts_worker_plays_queued_items_sequentially_without_interruption() -> None:
    """The narrator TTS worker must play queued items sequentially to completion without interruption."""
    svc = NarratorService.__new__(NarratorService)
    svc._tts_queue = queue.Queue(maxsize=10)
    svc._priority_arbiter = PriorityArbiter(maxsize=10)
    svc._state_lock = threading.Lock()
    svc._engine_state = "IDLE"
    svc._active_priority = None
    svc._active_item = None
    svc._interrupted = False
    svc._preempted = False
    svc._dropped_phases = 0
    svc._update_depth = lambda _d: None
    svc._profile = MagicMock(voice_id="test", speed=1.0)
    svc._ensure_tts_initialized = lambda: None

    playback_log = []

    class MockSink:
        def __init__(self):
            self.interrupt_calls = 0

        def play(self, wav_path):
            playback_log.append(("start", str(wav_path)))
            time.sleep(0.05)  # Simulate non-instant playback
            playback_log.append(("finish", str(wav_path)))

        def interrupt(self):
            self.interrupt_calls += 1

    sink = MockSink()
    svc._sink = sink

    # Mock synthesize to create a dummy WAV file
    class MockSynth:
        def synthesize_one(self, text, profile, wav_path):
            wav_path.write_bytes(b"RIFFdummywav")
            return True

    svc._synth = MockSynth()
    svc._tts_executor = None

    # Queue two speech items
    svc._tts_queue.put(QueueItem(priority=Priority.EXPLICIT_MCP, payload="Message One"))
    svc._tts_queue.put(QueueItem(priority=Priority.AUTOPLAY, payload="Message Two"))
    svc._tts_queue.put(None)  # Sentinel to stop worker

    # Run the worker directly
    svc._tts_worker()

    assert sink.interrupt_calls == 0, "sink.interrupt must NEVER be called during sequential playback"
    # Verify playback happened strictly in order: start 1 -> finish 1 -> start 2 -> finish 2
    assert len(playback_log) == 4
    assert playback_log[0][0] == "start"
    assert playback_log[1][0] == "finish"
    assert playback_log[2][0] == "start"
    assert playback_log[3][0] == "finish"
    assert playback_log[0][1] == playback_log[1][1]
    assert playback_log[2][1] == playback_log[3][1]
    assert playback_log[0][1] != playback_log[2][1]


def test_dedup_guard_history_tracks_multiple_distinct_messages() -> None:
    """DedupGuard allows multiple distinct consecutive turns while suppressing duplicate triggers."""
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-dedup-hist-"))
    marker = tmp / "marker"
    pid = tmp / "mpv.pid"
    lock = tmp / "lock"
    g = DedupGuard(marker_path=marker, mpv_pid_path=pid, lock_path=lock)

    hash_a = "a" * 64
    hash_b = "b" * 64
    hash_c = "c" * 64

    # Turn 1: Hash A claims
    assert g.try_claim(hash_a) is True
    # Immediate duplicate hook for Hash A is suppressed
    assert g.try_claim(hash_a) is False

    # Turn 2: Hash B claims immediately after
    assert g.try_claim(hash_b) is True
    # Immediate duplicate hook for Hash B is suppressed
    assert g.try_claim(hash_b) is False

    # CRITICAL: Duplicate hook for Hash A arriving now must STILL be suppressed!
    # (In the old single-marker implementation, Hash B overwrote the marker, allowing duplicate A)
    assert g.try_claim(hash_a) is False

    # Turn 3: Hash C claims
    assert g.try_claim(hash_c) is True


def main() -> int:
    tests = [
        test_active_speech_never_interrupted_by_new_messages,
        test_autoplay_worker_queues_consecutive_turns_without_staleness_drop,
        test_autoplay_worker_speaks_even_when_coalesce_beacon_is_immediately_stale,
        test_autoplay_worker_uses_real_in_process_fifo,
        test_tts_worker_plays_queued_items_sequentially_without_interruption,
        test_dedup_guard_history_tracks_multiple_distinct_messages,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"queue_robustness: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

