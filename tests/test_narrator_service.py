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
import queue
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

import narrator_service


def _bare_service(max_queue: int):
    """A NarratorService with just the fields _enqueue_phase touches,
    constructed via __new__ to skip the config/classifier/threads in
    __init__ (mirrors the other tests in this file)."""
    svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
    svc._max_queue = max_queue
    svc._tts_queue = queue.Queue(maxsize=max_queue)
    svc._dropped_phases = 0
    return svc


class _FakePhase:
    """Minimal stand-in carrying the attributes the drop log reads."""

    def __init__(self, tag: str) -> None:
        self.tag = tag

        class _Cat:
            value = tag

        self.category = _Cat()


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


def test_existing_pid_reclaims_live_non_daemon_pid() -> None:
    # Our own PID is alive but is the TEST runner, not the daemon. Under the
    # PID-identity guard it is reclaimable, so _existing_pid() returns None
    # (this is exactly the PID-reuse case: a live number that isn't us).
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(str(os.getpid()))
        path = Path(f.name)
    try:
        with patch.object(narrator_service, "PID_FILE", path):
            assert narrator_service._existing_pid() is None
    finally:
        path.unlink()


def test_pid_is_our_daemon_true_for_matching_cmdline() -> None:
    # Live pid (our own) + a cmdline that looks like the daemon ⇒ True.
    assert (
        narrator_service._pid_is_our_daemon(
            os.getpid(),
            cmdline_reader=lambda _p: "/usr/bin/python3 /x/y/narrator_service.py",
        )
        is True
    )


def test_pid_is_our_daemon_false_for_recycled_pid() -> None:
    # Live pid, but the cmdline is an unrelated process (pid was recycled).
    # Note "test_narrator_service.py" contains the substring but NOT
    # "/narrator_service.py" — the leading slash is what distinguishes them.
    assert (
        narrator_service._pid_is_our_daemon(
            os.getpid(),
            cmdline_reader=lambda _p: "/usr/bin/python3 /x/tests/test_narrator_service.py",
        )
        is False
    )


def test_pid_is_our_daemon_false_for_dead_pid() -> None:
    # Dead pid: rejected before the cmdline is even consulted.
    assert (
        narrator_service._pid_is_our_daemon(
            999999, cmdline_reader=lambda _p: "/x/narrator_service.py"
        )
        is False
    )


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
    with (
        tempfile.TemporaryDirectory() as home_str,
        patch.object(Path, "home", staticmethod(lambda: Path(home_str))),
    ):
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
                json.dumps(
                    {
                        "event": "PostToolUse",
                        "ts": 1.0,
                        "payload": {
                            "session_id": "not-opted-in",
                            "tool_name": "Bash",
                            "tool_input": {"command": "ls"},
                        },
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "event": "PostToolUse",
                        "ts": 2.0,
                        "payload": {
                            "session_id": "opted-in",
                            "tool_name": "Bash",
                            "tool_input": {"command": "pwd"},
                        },
                    }
                )
                + "\n"
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
                json.dumps(
                    {
                        "event": "PostToolUse",
                        "ts": 1.0,
                        "payload": {
                            "session_id": "sid",
                            "tool_name": "Bash",
                            "tool_input": {"command": "ls"},
                        },
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "event": "Stop",
                        "ts": 2.0,
                        "payload": {
                            "session_id": "sid",
                        },
                    }
                )
                + "\n"
            ).encode("utf-8")
            svc._process_chunk(chunk)

    assert enqueued_phases == [], (
        f"Stop should suppress narration, not enqueue. Got: {enqueued_phases}"
    )
    # Classifier should be empty post-Stop.
    assert svc._classifier.flush() is None


def test_enqueue_under_cap_keeps_all() -> None:
    svc = _bare_service(max_queue=4)
    for i in range(4):
        svc._enqueue_phase(_FakePhase(f"p{i}"))
    assert svc._tts_queue.qsize() == 4
    assert svc._dropped_phases == 0


def test_enqueue_over_cap_drops_oldest() -> None:
    svc = _bare_service(max_queue=3)
    for i in range(5):  # 2 over the cap
        svc._enqueue_phase(_FakePhase(f"p{i}"))
    # Never exceeds the bound.
    assert svc._tts_queue.qsize() == 3
    # The two oldest were shed; the survivors are the three newest.
    survivors = [svc._tts_queue.get().tag for _ in range(3)]
    assert survivors == ["p2", "p3", "p4"]
    assert svc._dropped_phases == 2


def test_enqueue_into_zero_free_slots_is_bounded() -> None:
    # A degenerate maxsize=1 still holds exactly one and counts drops.
    svc = _bare_service(max_queue=1)
    for i in range(3):
        svc._enqueue_phase(_FakePhase(f"p{i}"))
    assert svc._tts_queue.qsize() == 1
    assert svc._tts_queue.get().tag == "p2"
    assert svc._dropped_phases == 2


def test_tail_resumes_a_line_split_across_two_reads() -> None:
    """Regression: an event straddling a poll boundary must not be lost.

    The tail advanced its offset to the full file size regardless of where
    the last newline fell. A line still being appended when the poll fired
    was therefore consumed as a fragment (unparseable, dropped), and its
    remainder was read next poll as another fragment (also dropped) — so
    the event vanished with no trace, contradicting the loop's own comment
    that a partial trailing line is picked up next read.
    """
    svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
    svc._stop = __import__("threading").Event()
    svc._idle_shutdown = 1e9  # never idle out during the test
    svc._last_event_ts = time.time()
    seen: list[bytes] = []
    svc._process_chunk = lambda chunk: seen.append(chunk)

    with tempfile.TemporaryDirectory() as d:
        events = Path(d) / "events.jsonl"
        watermark = Path(d) / "watermark"
        line = json.dumps({"event": "PostToolUse", "ts": 1.0, "payload": {}}) + "\n"
        head, tail = line[:20], line[20:]

        # Start empty: with no watermark the tail resumes at end-of-file, so
        # the line must be appended AFTER the loop is running to be seen.
        events.write_text("", encoding="utf-8")

        polls = {"n": 0}
        real_wait = svc._stop.wait

        def append(part: str) -> None:
            with events.open("a", encoding="utf-8") as f:
                f.write(part)

        def wait(_interval):
            polls["n"] += 1
            if polls["n"] == 1:
                append(head)  # writer is mid-append when the next poll fires
            elif polls["n"] == 2:
                append(tail)  # writer completes the line
            else:
                svc._stop.set()
            return real_wait(0)

        svc._stop.wait = wait
        with (
            patch.object(narrator_service, "EVENTS_LOG", events),
            patch.object(narrator_service, "WATERMARK_FILE", watermark),
        ):
            svc._tail_events()

    joined = b"".join(seen)
    assert joined == line.encode("utf-8"), (
        f"event lost or corrupted across the read boundary: {joined!r}"
    )
    for chunk in seen:
        assert chunk.endswith(b"\n"), f"partial line was consumed: {chunk!r}"


def test_config_max_queue_depth_default_and_override() -> None:
    import narrator_config

    # Default when the key is absent: 32.
    with tempfile.TemporaryDirectory() as d:
        cfg_path = Path(d) / "narrator.toml"
        cfg_path.write_text('[narrator]\nprovider = "mock"\n', encoding="utf-8")
        with patch.dict(os.environ, {"AUTO_SPEECH_NARRATOR_CONFIG": str(cfg_path)}):
            assert narrator_config.load_config()["max_queue_depth"] == 32

    # Explicit override is honored and coerced to int.
    with tempfile.TemporaryDirectory() as d:
        cfg_path = Path(d) / "narrator.toml"
        cfg_path.write_text(
            '[narrator]\nprovider = "mock"\nmax_queue_depth = 8\n', encoding="utf-8"
        )
        with patch.dict(os.environ, {"AUTO_SPEECH_NARRATOR_CONFIG": str(cfg_path)}):
            assert narrator_config.load_config()["max_queue_depth"] == 8


class _MockSink:
    def __init__(self) -> None:
        self.played: list[Path] = []
        self.interrupted: bool = False

    def play(self, wav_path: Path) -> None:
        self.played.append(Path(wav_path))

    def interrupt(self) -> None:
        self.interrupted = True


class _MockSynthesizer:
    def __init__(self, should_succeed: bool = True, raise_exc: Exception | None = None) -> None:
        self.synthesized: list[tuple[str, Path]] = []
        self.should_succeed = should_succeed
        self.raise_exc = raise_exc

    def synthesize_one(self, text: str, profile, out_path: Path) -> bool:
        if self.raise_exc:
            raise self.raise_exc
        self.synthesized.append((text, out_path))
        if self.should_succeed:
            out_path.write_bytes(b"RIFFdummywavdata")
            return True
        return False


def test_speak_invokes_synthesizer_and_audio_sink() -> None:
    sink = _MockSink()
    synth = _MockSynthesizer(should_succeed=True)
    from voice_profile import VoiceProfile

    profile = VoiceProfile("test_voice", 1.0, 15.0, "2026-10-03T00:00:00Z", 100)

    svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
    svc._config = {}
    svc._sink = sink
    svc._synth = synth
    svc._profile = profile
    svc._engine = None

    svc._speak("Hello from in-process TTS")

    assert len(synth.synthesized) == 1
    assert synth.synthesized[0][0] == "Hello from in-process TTS"
    assert len(sink.played) == 1
    temp_wav_path = synth.synthesized[0][1]
    assert not temp_wav_path.exists(), "temp wav must be unlinked in finally"


def test_speak_skips_unspeakable_content() -> None:
    sink = _MockSink()
    synth = _MockSynthesizer(should_succeed=False)
    from voice_profile import VoiceProfile

    profile = VoiceProfile("test_voice", 1.0, 15.0, "2026-10-03T00:00:00Z", 100)

    svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
    svc._config = {}
    svc._sink = sink
    svc._synth = synth
    svc._profile = profile
    svc._engine = None

    svc._speak("•••")

    assert len(synth.synthesized) == 1
    assert len(sink.played) == 0, "Audio sink must not play if no speakable content"


def test_speak_cleans_up_on_synthesis_fault() -> None:
    from tts_engine import TTSGenerationError

    sink = _MockSink()
    synth = _MockSynthesizer(raise_exc=TTSGenerationError("synthesis crashed"))
    from voice_profile import VoiceProfile

    profile = VoiceProfile("test_voice", 1.0, 15.0, "2026-10-03T00:00:00Z", 100)

    svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
    svc._config = {}
    svc._sink = sink
    svc._synth = synth
    svc._profile = profile
    svc._engine = None

    # Must not raise
    svc._speak("Crashing content")
    assert len(sink.played) == 0


def test_speak_cleans_up_on_playback_failure() -> None:
    class _FailingSink:
        def play(self, wav_path: Path) -> None:
            raise RuntimeError("mpv audio device failure")

        def interrupt(self) -> None:
            pass

    sink = _FailingSink()
    synth = _MockSynthesizer(should_succeed=True)
    from voice_profile import VoiceProfile

    profile = VoiceProfile("test_voice", 1.0, 15.0, "2026-10-03T00:00:00Z", 100)

    svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
    svc._config = {}
    svc._sink = sink
    svc._synth = synth
    svc._profile = profile
    svc._engine = None

    # Must not crash
    svc._speak("Test playback failure")
    temp_wav_path = synth.synthesized[0][1]
    assert not temp_wav_path.exists(), "temp wav must be unlinked on playback error"


def test_process_chunk_user_prompt_submit_interrupts_audio_sink() -> None:
    sink = _MockSink()
    svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
    svc._config = {}
    svc._sink = sink
    svc._classifier = narrator_service.PhaseClassifier(silence_seconds=0.5, max_events_per_phase=1)
    svc._phases_this_turn = 3
    svc._tts_queue = queue.Queue()
    svc._last_event_ts = 0.0

    with tempfile.TemporaryDirectory() as home_str:
        home = Path(home_str)
        session_id = "test-session-123"
        marker = home / ".claude" / "auto-speech-narrate-sessions" / session_id
        marker.parent.mkdir(parents=True)
        marker.touch()

        chunk = (
            json.dumps(
                {
                    "event": "UserPromptSubmit",
                    "ts": time.time(),
                    "payload": {"session_id": session_id, "conversation_history": "User: hi"},
                }
            )
            + "\n"
        ).encode("utf-8")

        with patch.object(Path, "home", staticmethod(lambda: home)):
            svc._process_chunk(chunk)

    assert sink.interrupted is True, "UserPromptSubmit must call sink.interrupt()"
    assert svc._phases_this_turn == 0


def test_tts_worker_recovers_from_item_error() -> None:
    sink = _MockSink()
    synth = _MockSynthesizer(should_succeed=True)
    from voice_profile import VoiceProfile

    profile = VoiceProfile("test_voice", 1.0, 15.0, "2026-10-03T00:00:00Z", 100)

    svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
    svc._config = {}
    svc._sink = sink
    svc._synth = synth
    svc._profile = profile
    svc._engine = None
    svc._tts_queue = queue.Queue()
    svc._update_depth = lambda d: None

    svc._tts_queue.put("Valid narration")
    svc._tts_queue.put(None)

    svc._tts_worker()

    assert len(synth.synthesized) == 1
    assert synth.synthesized[0][0] == "Valid narration"
    assert svc._tts_queue.empty()


def test_socket_server_startup_unlinks_stale_socket_and_cleans_up_on_stop() -> None:
    with tempfile.TemporaryDirectory() as d:
        sock_path = Path(d) / "daemon.sock"
        sock_path.touch()
        assert sock_path.exists()

        svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
        svc._socket_path = sock_path
        svc._socket_server = None
        svc._socket_thread = None
        svc._queue_lock = threading.Lock()
        svc._atexit_registered = False

        svc._start_socket_server()
        try:
            assert sock_path.exists()
            assert svc._socket_thread is not None and svc._socket_thread.is_alive()
        finally:
            svc._stop_socket_server()

        assert not sock_path.exists()
        assert svc._socket_server is None
        assert svc._socket_thread is None


def test_socket_server_enqueues_text_to_tts_queue() -> None:
    with tempfile.TemporaryDirectory() as d:
        sock_path = Path(d) / "daemon.sock"
        svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
        svc._socket_path = sock_path
        svc._socket_server = None
        svc._socket_thread = None
        svc._queue_lock = threading.Lock()
        svc._atexit_registered = False
        svc._max_queue = 10
        svc._tts_queue = queue.Queue(maxsize=10)
        svc._dropped_phases = 0
        svc._last_event_ts = 0.0
        svc._update_depth = lambda _d: None

        svc._start_socket_server()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(str(sock_path))
                client.sendall(b"Hello from socket client")
                client.shutdown(socket.SHUT_WR)
                resp = client.recv(1024)
                assert resp == b"OK\n"

            item = svc._tts_queue.get(timeout=2.0)
            assert item == "Hello from socket client"
            assert svc._last_event_ts > 0.0
        finally:
            svc._stop_socket_server()


def test_socket_server_ignores_empty_or_whitespace_payload() -> None:
    with tempfile.TemporaryDirectory() as d:
        sock_path = Path(d) / "daemon.sock"
        svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
        svc._socket_path = sock_path
        svc._socket_server = None
        svc._socket_thread = None
        svc._queue_lock = threading.Lock()
        svc._atexit_registered = False
        svc._max_queue = 10
        svc._tts_queue = queue.Queue(maxsize=10)
        svc._dropped_phases = 0
        svc._last_event_ts = 0.0
        svc._update_depth = lambda _d: None

        svc._start_socket_server()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(str(sock_path))
                client.sendall(b"   \r\n\t   ")
                client.shutdown(socket.SHUT_WR)

            time.sleep(0.05)
            assert svc._tts_queue.empty()
        finally:
            svc._stop_socket_server()


def test_socket_server_abrupt_disconnect_does_not_crash() -> None:
    with tempfile.TemporaryDirectory() as d:
        sock_path = Path(d) / "daemon.sock"
        svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
        svc._socket_path = sock_path
        svc._socket_server = None
        svc._socket_thread = None
        svc._queue_lock = threading.Lock()
        svc._atexit_registered = False
        svc._max_queue = 10
        svc._tts_queue = queue.Queue(maxsize=10)
        svc._dropped_phases = 0
        svc._last_event_ts = 0.0
        svc._update_depth = lambda _d: None

        svc._start_socket_server()
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(str(sock_path))
            sock.sendall(b"abrupt")
            linger = (
                b"\x01\x00\x00\x00\x00\x00\x00\x00"
                if sys.platform == "darwin"
                else b"\x01\x00\x00\x00"
            )
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
            sock.close()

            time.sleep(0.05)
            assert svc._socket_thread is not None and svc._socket_thread.is_alive()
        finally:
            svc._stop_socket_server()


def test_enqueue_text_drops_oldest_under_backpressure() -> None:
    svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
    svc._max_queue = 2
    svc._tts_queue = queue.Queue(maxsize=2)
    svc._dropped_phases = 0
    svc._queue_lock = threading.Lock()
    svc._last_event_ts = 0.0
    svc._update_depth = lambda _d: None

    svc.enqueue_text("msg1")
    svc.enqueue_text("msg2")
    svc.enqueue_text("msg3")

    assert svc._tts_queue.qsize() == 2
    assert svc._dropped_phases == 1
    survivors = [svc._tts_queue.get_nowait(), svc._tts_queue.get_nowait()]
    assert survivors == ["msg2", "msg3"]


def main() -> int:
    tests = [
        test_existing_pid_returns_none_when_pid_file_absent,
        test_existing_pid_returns_none_for_dead_pid,
        test_existing_pid_reclaims_live_non_daemon_pid,
        test_pid_is_our_daemon_true_for_matching_cmdline,
        test_pid_is_our_daemon_false_for_recycled_pid,
        test_pid_is_our_daemon_false_for_dead_pid,
        test_sweep_removes_old_session_markers_but_not_fresh_ones,
        test_sweep_tolerates_missing_dirs,
        test_process_chunk_filters_events_without_session_marker,
        test_process_chunk_suppresses_stop_event_flush,
        test_enqueue_under_cap_keeps_all,
        test_enqueue_over_cap_drops_oldest,
        test_enqueue_into_zero_free_slots_is_bounded,
        test_tail_resumes_a_line_split_across_two_reads,
        test_config_max_queue_depth_default_and_override,
        test_speak_invokes_synthesizer_and_audio_sink,
        test_speak_skips_unspeakable_content,
        test_speak_cleans_up_on_synthesis_fault,
        test_speak_cleans_up_on_playback_failure,
        test_process_chunk_user_prompt_submit_interrupts_audio_sink,
        test_tts_worker_recovers_from_item_error,
        test_socket_server_startup_unlinks_stale_socket_and_cleans_up_on_stop,
        test_socket_server_enqueues_text_to_tts_queue,
        test_socket_server_ignores_empty_or_whitespace_payload,
        test_socket_server_abrupt_disconnect_does_not_crash,
        test_enqueue_text_drops_oldest_under_backpressure,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"narrator_service: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
