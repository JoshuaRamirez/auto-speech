"""Unit tests for replay.py and control.py caller realignment."""

from __future__ import annotations

import argparse
import io
import sys
import tempfile
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import control
import replay
from cache_entry import CacheEntry
from native_audio_sink import AudioSinkError

# --- control.py tests ---


def test_control_ensure_session_returns_no_session() -> None:
    stderr = io.StringIO()
    with mock.patch("sys.stderr", stderr):
        rc = control._ensure_session()
    assert rc == control.EXIT_NO_SESSION
    assert "no active playback session" in stderr.getvalue()


def test_control_subcommands_exit_no_session() -> None:
    for cmd in ["pause", "resume", "restart", "end"]:
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            rc = control.main([cmd])
        assert rc == control.EXIT_NO_SESSION, f"{cmd} returned {rc}"


def test_control_seek_argument_validation() -> None:
    # Empty target should exit with EXIT_BAD_ARG if session were ok,
    # but since session is checked first, it returns EXIT_NO_SESSION.
    stderr = io.StringIO()
    with mock.patch("sys.stderr", stderr):
        rc = control.main(["seek", "+15"])
    assert rc == control.EXIT_NO_SESSION


def test_control_seek_without_session_returns_bad_arg_when_empty() -> None:
    with mock.patch("control._ensure_session", return_value=control.EXIT_OK):
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            rc = control._cmd_seek(argparse.Namespace(target="   "))
        assert rc == control.EXIT_BAD_ARG
        assert "seek requires a target" in stderr.getvalue()


# --- replay.py tests ---


def test_replay_bad_ordinal() -> None:
    stderr = io.StringIO()
    with mock.patch("sys.stderr", stderr):
        rc = replay.main(["--ordinal", "0"])
    assert rc == 2
    assert "--ordinal must be >= 1" in stderr.getvalue()


def test_replay_no_cache_entries() -> None:
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        mock.patch("replay._default_cache_root", return_value=Path(tmpdir)),
        mock.patch("sys.stderr", io.StringIO()) as stderr,
    ):
        rc = replay.main([])
        assert rc == replay.EXIT_NO_CACHE_ENTRY
        assert "no cache entries found" in stderr.getvalue()


def test_replay_ordinal_out_of_range() -> None:
    dummy_wav = Path("/tmp/dummy.wav")
    entry = CacheEntry("a" * 64, "v1", 1.0, 10, 2.0, "2026-10-03T20:00:00Z", 5.0)
    with (
        mock.patch("cache_store.CacheStore.list_by_recency", return_value=[(dummy_wav, entry)]),
        mock.patch("sys.stderr", io.StringIO()) as stderr,
    ):
        rc = replay.main(["--ordinal", "5"])
        assert rc == replay.EXIT_NO_CACHE_ENTRY
        assert "only 1 cache entries" in stderr.getvalue()


def test_replay_plays_audio_via_native_audio_sink() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        dummy_wav = store_root / "test.wav"
        dummy_wav.touch()
        entry = CacheEntry("b" * 64, "v1", 1.0, 10, 2.0, "2026-10-03T20:00:00Z", 5.0)

        with (
            mock.patch(
                "cache_store.CacheStore.list_by_recency",
                return_value=[(dummy_wav, entry)],
            ),
            mock.patch("replay.NativeAudioSink") as mock_sink_cls,
            mock.patch("sys.stderr", io.StringIO()),
        ):
            mock_sink = mock.MagicMock()
            mock_sink_cls.return_value = mock_sink

            rc = replay.main(["--ordinal", "1"])
            assert rc == replay.EXIT_OK
            mock_sink.play.assert_called_once_with(dummy_wav)


def test_replay_handles_audio_sink_error() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        dummy_wav = store_root / "test.wav"
        dummy_wav.touch()
        entry = CacheEntry("c" * 64, "v1", 1.0, 10, 2.0, "2026-10-03T20:00:00Z", 5.0)

        with (
            mock.patch(
                "cache_store.CacheStore.list_by_recency",
                return_value=[(dummy_wav, entry)],
            ),
            mock.patch("replay.NativeAudioSink") as mock_sink_cls,
            mock.patch("sys.stderr", io.StringIO()) as stderr,
        ):
            mock_sink = mock.MagicMock()
            mock_sink.play.side_effect = AudioSinkError("mpv failure")
            mock_sink_cls.return_value = mock_sink

            rc = replay.main(["--ordinal", "1"])
            assert rc == replay.EXIT_PLAYBACK_FAIL
            assert "replay: playback failed" in stderr.getvalue()


def test_replay_handles_keyboard_interrupt() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        dummy_wav = store_root / "test.wav"
        dummy_wav.touch()
        entry = CacheEntry("d" * 64, "v1", 1.0, 10, 2.0, "2026-10-03T20:00:00Z", 5.0)

        with (
            mock.patch(
                "cache_store.CacheStore.list_by_recency",
                return_value=[(dummy_wav, entry)],
            ),
            mock.patch("replay.NativeAudioSink") as mock_sink_cls,
            mock.patch("sys.stderr", io.StringIO()),
        ):
            mock_sink = mock.MagicMock()
            mock_sink.play.side_effect = KeyboardInterrupt()
            mock_sink_cls.return_value = mock_sink

            rc = replay.main(["--ordinal", "1"])
            assert rc == replay.EXIT_INTERRUPTED
            mock_sink.interrupt.assert_called_once()


def main() -> int:
    tests = [
        test_control_ensure_session_returns_no_session,
        test_control_subcommands_exit_no_session,
        test_control_seek_argument_validation,
        test_control_seek_without_session_returns_bad_arg_when_empty,
        test_replay_bad_ordinal,
        test_replay_no_cache_entries,
        test_replay_ordinal_out_of_range,
        test_replay_plays_audio_via_native_audio_sink,
        test_replay_handles_audio_sink_error,
        test_replay_handles_keyboard_interrupt,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"replay_control: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
