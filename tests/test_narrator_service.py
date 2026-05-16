"""Unit tests for narrator_service module-level helpers.

Daemon lifecycle (signal handling, threading, the tail-events loop)
is hard to unit-test without a long-running fixture, so this file
focuses on the pure helpers around it: _existing_pid, the stale-
marker sweep, and the per-event session-marker filter logic inside
_process_chunk (extracted by feeding a small JSONL chunk through
NarratorService._process_chunk after instantiating it with a fake
classifier).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

import narrator_service  # noqa: E402


def test_existing_pid_returns_none_when_pid_file_absent() -> None:
    with patch.object(narrator_service, "PID_FILE", Path("/tmp/auto-speech-test-no-such-pid")):
        Path("/tmp/auto-speech-test-no-such-pid").unlink(missing_ok=True)
        assert narrator_service._existing_pid() is None


def test_existing_pid_returns_none_for_dead_pid() -> None:
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write("999999")  # near-certainly-not-running
        path = Path(f.name)
    try:
        with patch.object(narrator_service, "PID_FILE", path):
            assert narrator_service._existing_pid() is None
    finally:
        path.unlink()


def test_existing_pid_returns_pid_for_live_process() -> None:
    # Use our own PID — definitely alive AND signalable by current user.
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(str(os.getpid()))
        path = Path(f.name)
    try:
        with patch.object(narrator_service, "PID_FILE", path):
            result = narrator_service._existing_pid()
            assert result == os.getpid()
    finally:
        path.unlink()


def test_sweep_removes_old_session_markers_but_not_fresh_ones() -> None:
    with tempfile.TemporaryDirectory() as home_str:
        home = Path(home_str)
        narrate_dir = home / ".claude" / "auto-speech-narrate-sessions"
        autoplay_dir = home / ".claude" / "auto-speech-autoplay-sessions"
        narrate_dir.mkdir(parents=True)
        autoplay_dir.mkdir(parents=True)

        # Fresh markers (mtime = now)
        fresh_n = narrate_dir / "fresh-session-1"
        fresh_a = autoplay_dir / "fresh-session-2"
        fresh_n.touch()
        fresh_a.touch()

        # Stale markers (mtime = 40 days ago)
        stale_n = narrate_dir / "stale-session-3"
        stale_a = autoplay_dir / "stale-session-4"
        stale_n.touch()
        stale_a.touch()
        old_ts = time.time() - (40 * 86400)
        os.utime(stale_n, (old_ts, old_ts))
        os.utime(stale_a, (old_ts, old_ts))

        with patch.object(Path, "home", staticmethod(lambda: home)):
            narrator_service._sweep_stale_session_markers()

        assert fresh_n.exists(), "fresh narrate marker must survive sweep"
        assert fresh_a.exists(), "fresh autoplay marker must survive sweep"
        assert not stale_n.exists(), "stale narrate marker must be swept"
        assert not stale_a.exists(), "stale autoplay marker must be swept"


def test_sweep_tolerates_missing_dirs() -> None:
    # Should not raise when neither dir exists.
    with tempfile.TemporaryDirectory() as home_str:
        with patch.object(Path, "home", staticmethod(lambda: Path(home_str))):
            narrator_service._sweep_stale_session_markers()


def test_process_chunk_filters_events_without_session_marker() -> None:
    """End-to-end through _process_chunk: an event whose session_id has
    no marker on disk should NOT reach the classifier."""
    svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
    svc._config = {"silence_seconds": 8.0}
    from narrator_phase_classifier import PhaseClassifier
    svc._classifier = PhaseClassifier(silence_seconds=8.0)
    svc._last_event_ts = 0.0
    enqueued_phases = []
    svc._maybe_enqueue = lambda p: enqueued_phases.append(p)

    with tempfile.TemporaryDirectory() as home_str:
        home = Path(home_str)
        markers = home / ".claude" / "auto-speech-narrate-sessions"
        markers.mkdir(parents=True)
        (markers / "opted-in").touch()
        # Don't create a marker for "not-opted-in".

        with patch.object(Path, "home", staticmethod(lambda: home)):
            chunk = (
                json.dumps({
                    "event": "PostToolUse",
                    "ts": 1.0,
                    "payload": {
                        "session_id": "not-opted-in",
                        "tool_name": "Bash",
                        "tool_input": {"command": "ls"},
                    },
                }) + "\n" +
                json.dumps({
                    "event": "PostToolUse",
                    "ts": 2.0,
                    "payload": {
                        "session_id": "opted-in",
                        "tool_name": "Bash",
                        "tool_input": {"command": "pwd"},
                    },
                }) + "\n"
            ).encode("utf-8")
            svc._process_chunk(chunk)

    # Only the opted-in event should have reached the classifier.
    final = svc._classifier.flush()
    assert final is not None
    assert len(final.events) == 1
    assert "pwd" in final.events[0].summary


def test_process_chunk_suppresses_stop_event_flush() -> None:
    """Stop events flush the classifier WITHOUT calling _maybe_enqueue —
    end-of-turn belongs to autoplay, not narrator."""
    svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
    svc._config = {"silence_seconds": 8.0}
    from narrator_phase_classifier import PhaseClassifier
    svc._classifier = PhaseClassifier(silence_seconds=8.0)
    svc._last_event_ts = 0.0
    enqueued_phases = []
    svc._maybe_enqueue = lambda p: enqueued_phases.append(p)

    with tempfile.TemporaryDirectory() as home_str:
        home = Path(home_str)
        markers = home / ".claude" / "auto-speech-narrate-sessions"
        markers.mkdir(parents=True)
        (markers / "sid").touch()
        with patch.object(Path, "home", staticmethod(lambda: home)):
            chunk = (
                json.dumps({"event": "PostToolUse", "ts": 1.0, "payload": {
                    "session_id": "sid", "tool_name": "Bash",
                    "tool_input": {"command": "ls"},
                }}) + "\n" +
                json.dumps({"event": "Stop", "ts": 2.0, "payload": {
                    "session_id": "sid",
                }}) + "\n"
            ).encode("utf-8")
            svc._process_chunk(chunk)

    assert enqueued_phases == [], (
        f"Stop should suppress narration, not enqueue. Got: {enqueued_phases}"
    )
    # Classifier should be empty post-Stop.
    assert svc._classifier.flush() is None


def main() -> int:
    tests = [
        test_existing_pid_returns_none_when_pid_file_absent,
        test_existing_pid_returns_none_for_dead_pid,
        test_existing_pid_returns_pid_for_live_process,
        test_sweep_removes_old_session_markers_but_not_fresh_ones,
        test_sweep_tolerates_missing_dirs,
        test_process_chunk_filters_events_without_session_marker,
        test_process_chunk_suppresses_stop_event_flush,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"narrator_service: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
