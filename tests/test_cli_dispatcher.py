"""Unit and integration tests for the unified auto-speech CLI dispatcher (Milestone M1).

Covers all 12 subcommands (speak, replay, pause, resume, restart, stop, seek,
autoplay, narrate, hook, doctor, update), input handling, global mute,
error codes, and trampoline integration.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Isolation: never touch the live service's files when run standalone; the
# runner (tests/run_all.sh) sets AUTO_SPEECH_TMP_ROOT itself.
os.environ.setdefault("AUTO_SPEECH_TMP_ROOT", tempfile.mkdtemp(prefix="auto-speech-tests-"))

from state_paths import state_path  # noqa: E402

import cli  # noqa: E402
from cache_entry import CacheEntry  # noqa: E402
from daemon_client import DaemonResponse, Priority  # noqa: E402
from health_report import Check, HealthReport, Status  # noqa: E402
from native_audio_sink import AudioSinkError  # noqa: E402


class BaseCliTestCase(unittest.TestCase):
    """Hermetic test harness isolating filesystem and environment."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tmpdir.name) / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.orig_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CONFIG_HOME"] = str(self.home / ".config")

        self.orig_env = dict(os.environ)

        # Ensure no suppress or session leaks
        os.environ.pop("AUTO_SPEECH_SUPPRESS_HOOKS", None)
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.orig_env)
        self.tmpdir.cleanup()


class TestCliHelpAndRouting(BaseCliTestCase):
    """Tests for top-level CLI synopsis, help, version, and routing."""

    def test_top_level_help_exits_zero_and_lists_subcommands(self) -> None:
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            rc = cli.main(["--help"])
        self.assertEqual(rc, 0)
        out = stdout.getvalue()
        for subcmd in [
            "speak",
            "replay",
            "pause",
            "resume",
            "restart",
            "stop",
            "seek",
            "autoplay",
            "narrate",
            "hook",
            "doctor",
            "update",
        ]:
            self.assertIn(subcmd, out)

    def test_top_level_version(self) -> None:
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            rc = cli.main(["--version"])
        self.assertEqual(rc, 0)
        self.assertIn("auto-speech 0.6.0", stdout.getvalue())

    def test_top_level_missing_subcommand_exits_two(self) -> None:
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            rc = cli.main([])
        self.assertEqual(rc, 2)
        self.assertIn("usage: auto-speech", stderr.getvalue())

    def test_top_level_invalid_subcommand_exits_two(self) -> None:
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            rc = cli.main(["foobar"])
        self.assertEqual(rc, 2)

    def test_subcommand_help_for_all_12_commands(self) -> None:
        subcommands = [
            "speak",
            "replay",
            "pause",
            "resume",
            "restart",
            "stop",
            "seek",
            "autoplay",
            "narrate",
            "hook",
            "doctor",
            "update",
        ]
        for subcmd in subcommands:
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                rc = cli.main([subcmd, "--help"])
            self.assertEqual(rc, 0, f"{subcmd} --help failed")
            self.assertTrue(len(stdout.getvalue()) > 0)


class TestCliSpeak(BaseCliTestCase):
    """Tests for `auto-speech speak`."""

    def test_speak_positional_arguments_success(self) -> None:
        mock_client = mock.MagicMock()
        mock_client.speak.return_value = DaemonResponse(status="ok", action="speak")
        rc = cli.main(["speak", "hello", "world"], client=mock_client)
        self.assertEqual(rc, 0)
        mock_client.speak.assert_called_once()
        args, kwargs = mock_client.speak.call_args
        self.assertEqual(args[0], "hello world")

    def test_speak_multiple_positional_tokens_joined(self) -> None:
        mock_client = mock.MagicMock()
        mock_client.speak.return_value = DaemonResponse(status="ok", action="speak")
        rc = cli.main(["speak", "token1", "token2", "token3"], client=mock_client)
        self.assertEqual(rc, 0)
        args, _ = mock_client.speak.call_args
        self.assertEqual(args[0], "token1 token2 token3")

    def test_speak_piped_stdin_success(self) -> None:
        mock_client = mock.MagicMock()
        mock_client.speak.return_value = DaemonResponse(status="ok", action="speak")
        with mock.patch("sys.stdin", io.StringIO("text from stdin")):
            rc = cli.main(["speak"], client=mock_client)
        self.assertEqual(rc, 0)
        args, _ = mock_client.speak.call_args
        self.assertEqual(args[0], "text from stdin")

    def test_speak_positional_precedence_over_stdin(self) -> None:
        mock_client = mock.MagicMock()
        mock_client.speak.return_value = DaemonResponse(status="ok", action="speak")
        with mock.patch("sys.stdin", io.StringIO("stdin text")):
            rc = cli.main(["speak", "positional", "text"], client=mock_client)
        self.assertEqual(rc, 0)
        args, _ = mock_client.speak.call_args
        self.assertEqual(args[0], "positional text")

    def test_speak_empty_input_exits_two(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch("sys.stdin", io.StringIO("")),
            mock.patch("sys.stderr", stderr),
        ):
            rc = cli.main(["speak"])
        self.assertEqual(rc, 2)
        self.assertIn("speak: no text provided on command line or stdin", stderr.getvalue())

    def test_speak_whitespace_only_stdin_exits_two(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch("sys.stdin", io.StringIO("   \n\t  ")),
            mock.patch("sys.stderr", stderr),
        ):
            rc = cli.main(["speak"])
        self.assertEqual(rc, 2)
        self.assertIn("speak: no text provided on command line or stdin", stderr.getvalue())

    def test_speak_source_hash_cache_playback(self) -> None:
        valid_hash = "a" * 64
        mock_client = mock.MagicMock()
        mock_client.play_cache.return_value = DaemonResponse(status="ok", action="play_cache")
        with mock.patch("sys.stdin", io.StringIO("")):
            rc = cli.main(["speak", "--source-hash", valid_hash], client=mock_client)
        self.assertEqual(rc, 0)
        mock_client.play_cache.assert_called_once_with(valid_hash)

    def test_speak_tty_source_hash_triggers_cache_without_reading_stdin(self) -> None:
        """On an interactive terminal (TTY), --source-hash triggers cache lookup without blocking on stdin."""
        valid_hash = "a" * 64
        mock_client = mock.MagicMock()
        mock_client.play_cache.return_value = DaemonResponse(status="ok", action="play_cache")
        mock_stdin = mock.MagicMock()
        mock_stdin.isatty.return_value = True
        mock_stdin.read.side_effect = AssertionError("sys.stdin.read() must not be called on TTY when --source-hash is present")

        with mock.patch("sys.stdin", mock_stdin):
            rc = cli.main(["speak", "--source-hash", valid_hash], client=mock_client)

        self.assertEqual(rc, 0)
        mock_client.play_cache.assert_called_once_with(valid_hash)
        mock_stdin.read.assert_not_called()

    def test_speak_tty_no_words_no_hash_exits_two_without_reading_stdin(self) -> None:
        """On an interactive terminal (TTY), missing input exits fast with code 2 without blocking on stdin."""
        mock_stdin = mock.MagicMock()
        mock_stdin.isatty.return_value = True
        mock_stdin.read.side_effect = AssertionError("sys.stdin.read() must not be called on TTY without input")
        stderr = io.StringIO()

        with (
            mock.patch("sys.stdin", mock_stdin),
            mock.patch("sys.stderr", stderr),
        ):
            rc = cli.main(["speak"])

        self.assertEqual(rc, 2)
        mock_stdin.read.assert_not_called()
        self.assertIn("speak: no text provided on command line or stdin", stderr.getvalue())

    def test_speak_piped_non_tty_with_text_and_source_hash_uses_piped_text(self) -> None:
        """When stdin is piped (non-TTY) with text, stdin text is spoken and passed with source_hash."""
        valid_hash = "a" * 64
        mock_client = mock.MagicMock()
        mock_client.speak.return_value = DaemonResponse(status="ok", action="speak")

        with mock.patch("sys.stdin", io.StringIO("piped text overrides empty")):
            rc = cli.main(["speak", "--source-hash", valid_hash], client=mock_client)

        self.assertEqual(rc, 0)
        mock_client.speak.assert_called_once()
        args, kwargs = mock_client.speak.call_args
        self.assertEqual(args[0], "piped text overrides empty")
        self.assertEqual(kwargs.get("source_hash"), valid_hash)
        mock_client.play_cache.assert_not_called()

    def test_speak_piped_non_tty_empty_with_source_hash_triggers_cache(self) -> None:
        """When stdin is piped (non-TTY) and empty, --source-hash proceeds to cache playback."""
        valid_hash = "a" * 64
        mock_client = mock.MagicMock()
        mock_client.play_cache.return_value = DaemonResponse(status="ok", action="play_cache")

        with mock.patch("sys.stdin", io.StringIO("   \n\t  ")):
            rc = cli.main(["speak", "--source-hash", valid_hash], client=mock_client)

        self.assertEqual(rc, 0)
        mock_client.play_cache.assert_called_once_with(valid_hash)
        mock_client.speak.assert_not_called()

    def test_speak_invalid_source_hash_exits_two(self) -> None:
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            rc = cli.main(["speak", "--source-hash", "invalid_short_hash"])
        self.assertEqual(rc, 2)
        self.assertIn("--source-hash must be 64 hex chars", stderr.getvalue())

    def test_speak_global_mute_clean_exit_zero(self) -> None:
        disabled_marker = self.home / ".claude" / "auto-speech.disabled"
        disabled_marker.parent.mkdir(parents=True, exist_ok=True)
        disabled_marker.touch()

        mock_client = mock.MagicMock()
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            rc = cli.main(["speak", "hello"], client=mock_client)
        self.assertEqual(rc, 0)
        mock_client.speak.assert_not_called()
        self.assertIn("globally muted", stderr.getvalue())

    def test_speak_priority_and_session_forwarding(self) -> None:
        mock_client = mock.MagicMock()
        mock_client.speak.return_value = DaemonResponse(status="ok", action="speak")
        rc = cli.main(
            ["speak", "hello", "--priority", "1", "--session-id", "sess-1"],
            client=mock_client,
        )
        self.assertEqual(rc, 0)
        mock_client.speak.assert_called_once_with(
            "hello",
            priority=Priority.USER_INTERRUPT,
            source_hash=None,
            session_id="sess-1",
            voice_id="system",
            speed=1.0,
        )

    def test_speak_uses_the_shell_session_when_no_flag_is_given(self) -> None:
        """Every path carries its session so the daemon can name the desk only
        when the speaker changes (his 19:06)."""
        mock_client = mock.MagicMock()
        mock_client.speak.return_value = DaemonResponse(status="ok", action="speak")
        os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-env"
        rc = cli.main(["speak", "hello"], client=mock_client)
        self.assertEqual(rc, 0)
        self.assertEqual(mock_client.speak.call_args.kwargs["session_id"], "sess-env")
        mock_client.speak.reset_mock()
        rc = cli.main(["speak", "hello", "--session-id", "sess-flag"], client=mock_client)
        self.assertEqual(mock_client.speak.call_args.kwargs["session_id"], "sess-flag")

    def test_speak_daemon_unreachable_exits_one(self) -> None:
        mock_client = mock.MagicMock()
        mock_client.speak.side_effect = ConnectionError("daemon offline")
        stderr = io.StringIO()
        with (
            mock.patch("cli.send_speech_request", return_value=1),
            mock.patch("sys.stderr", stderr),
        ):
            rc = cli.main(["speak", "hello"], client=mock_client)
        self.assertEqual(rc, 1)
        self.assertIn("speech request failed", stderr.getvalue())


class TestCliReplay(BaseCliTestCase):
    """Tests for `auto-speech replay`."""

    def test_replay_default_ordinal_one_success(self) -> None:
        dummy_wav = Path("/tmp/dummy.wav")
        entry = CacheEntry("a" * 64, "v1", 1.0, 10, 2.0, "2026-10-06T12:00:00Z", 5.0)
        mock_client = mock.MagicMock()
        mock_client.play_cache.return_value = DaemonResponse(status="ok", action="play_cache")

        with (
            mock.patch("cache_store.CacheStore.list_by_recency", return_value=[(dummy_wav, entry)]),
            mock.patch("sys.stderr", io.StringIO()),
        ):
            rc = cli.main(["replay"], client=mock_client)
        self.assertEqual(rc, 0)
        mock_client.play_cache.assert_called_once_with("a" * 64)

    def test_replay_explicit_ordinal_success(self) -> None:
        entry1 = CacheEntry("a" * 64, "v1", 1.0, 10, 2.0, "2026-10-06T12:00:00Z", 5.0)
        entry2 = CacheEntry("b" * 64, "v2", 1.0, 10, 2.0, "2026-10-06T11:00:00Z", 5.0)
        mock_client = mock.MagicMock()
        mock_client.play_cache.return_value = DaemonResponse(status="ok", action="play_cache")

        with (
            mock.patch(
                "cache_store.CacheStore.list_by_recency",
                return_value=[(Path("/tmp/1.wav"), entry1), (Path("/tmp/2.wav"), entry2)],
            ),
            mock.patch("sys.stderr", io.StringIO()),
        ):
            rc = cli.main(["replay", "--ordinal", "2"], client=mock_client)
        self.assertEqual(rc, 0)
        mock_client.play_cache.assert_called_once_with("b" * 64)

    def test_replay_zero_or_negative_ordinal_exits_two(self) -> None:
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            rc = cli.main(["replay", "--ordinal", "0"])
        self.assertEqual(rc, 2)
        self.assertIn("--ordinal must be >= 1", stderr.getvalue())

    def test_replay_empty_cache_exits_two(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch("cache_store.CacheStore.list_by_recency", return_value=[]),
            mock.patch("sys.stderr", stderr),
        ):
            rc = cli.main(["replay"])
        self.assertEqual(rc, 2)
        self.assertIn("no cache entries found", stderr.getvalue())

    def test_replay_ordinal_out_of_bounds_exits_two(self) -> None:
        entry = CacheEntry("a" * 64, "v1", 1.0, 10, 2.0, "2026-10-06T12:00:00Z", 5.0)
        stderr = io.StringIO()
        with (
            mock.patch("cache_store.CacheStore.list_by_recency", return_value=[(Path("/tmp/1.wav"), entry)]),
            mock.patch("sys.stderr", stderr),
        ):
            rc = cli.main(["replay", "--ordinal", "5"])
        self.assertEqual(rc, 2)
        self.assertIn("only 1 cache entries", stderr.getvalue())

    def test_replay_audio_sink_error_exits_six(self) -> None:
        entry = CacheEntry("a" * 64, "v1", 1.0, 10, 2.0, "2026-10-06T12:00:00Z", 5.0)
        mock_sink = mock.MagicMock()
        mock_sink.play.side_effect = AudioSinkError("mpv failure")
        stderr = io.StringIO()
        with (
            mock.patch("cache_store.CacheStore.list_by_recency", return_value=[(Path("/tmp/1.wav"), entry)]),
            mock.patch("sys.stderr", stderr),
        ):
            rc = cli.main(["replay"], sink=mock_sink)
        self.assertEqual(rc, 6)
        self.assertIn("playback failed", stderr.getvalue())

    def test_replay_keyboard_interrupt_exits_130(self) -> None:
        entry = CacheEntry("a" * 64, "v1", 1.0, 10, 2.0, "2026-10-06T12:00:00Z", 5.0)
        mock_sink = mock.MagicMock()
        mock_sink.play.side_effect = KeyboardInterrupt()
        with (
            mock.patch("cache_store.CacheStore.list_by_recency", return_value=[(Path("/tmp/1.wav"), entry)]),
            mock.patch("sys.stderr", io.StringIO()),
        ):
            rc = cli.main(["replay"], sink=mock_sink)
        self.assertEqual(rc, 130)
        mock_sink.interrupt.assert_called_once()

    def test_replay_global_mute_clean_exit_zero(self) -> None:
        disabled_marker = self.home / ".claude" / "auto-speech.disabled"
        disabled_marker.parent.mkdir(parents=True, exist_ok=True)
        disabled_marker.touch()

        entry = CacheEntry("a" * 64, "v1", 1.0, 10, 2.0, "2026-10-06T12:00:00Z", 5.0)
        mock_sink = mock.MagicMock()
        stderr = io.StringIO()
        with (
            mock.patch("cache_store.CacheStore.list_by_recency", return_value=[(Path("/tmp/1.wav"), entry)]),
            mock.patch("sys.stderr", stderr),
        ):
            rc = cli.main(["replay"], sink=mock_sink)
        self.assertEqual(rc, 0)
        mock_sink.play.assert_not_called()
        self.assertIn("globally muted", stderr.getvalue())


class TestCliTransportControls(BaseCliTestCase):
    """Tests for pause, resume, restart, stop, seek."""

    def test_pause_no_session_exits_two(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch("cli._has_active_session", return_value=False),
            mock.patch("sys.stderr", stderr),
        ):
            rc = cli.main(["pause"])
        self.assertEqual(rc, 2)
        self.assertIn("no active playback session", stderr.getvalue())

    def test_resume_no_session_exits_two(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch("cli._has_active_session", return_value=False),
            mock.patch("sys.stderr", stderr),
        ):
            rc = cli.main(["resume"])
        self.assertEqual(rc, 2)
        self.assertIn("no active playback session", stderr.getvalue())

    def test_stop_no_session_exits_two(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch("cli._has_active_session", return_value=False),
            mock.patch("sys.stderr", stderr),
        ):
            rc = cli.main(["stop"])
        self.assertEqual(rc, 2)
        self.assertIn("no active playback session", stderr.getvalue())

    def test_restart_no_session_exits_two(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch("cli._has_active_session", return_value=False),
            mock.patch("sys.stderr", stderr),
        ):
            rc = cli.main(["restart"])
        self.assertEqual(rc, 2)
        self.assertIn("no active playback session", stderr.getvalue())

    def test_seek_no_session_valid_target_exits_two(self) -> None:
        """When no session is active, valid targets must exit 2 with diagnostic."""
        for target in ["+15", "-10", "30", "end"]:
            with self.subTest(target=target):
                stderr = io.StringIO()
                with mock.patch("sys.stderr", stderr):
                    rc = cli.main(["seek", target])
                self.assertEqual(rc, 2)
                self.assertIn("no active playback session", stderr.getvalue())

    def test_seek_missing_target_exits_two(self) -> None:
        """Missing required positional target exits 2 via argparse."""
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            rc = cli.main(["seek"])
        self.assertEqual(rc, 2)

    def test_seek_empty_whitespace_target_unmocked_exits_four(self) -> None:
        """Empty or whitespace targets must exit 4 without mocking session presence."""
        for target in ["", "   ", "\t"]:
            with self.subTest(target=target):
                stderr = io.StringIO()
                with mock.patch("sys.stderr", stderr):
                    rc = cli.main(["seek", target])
                self.assertEqual(rc, 4)
                self.assertIn("seek requires a target", stderr.getvalue())

    def test_seek_invalid_target_unmocked_exits_four(self) -> None:
        """Un-mocked execution with invalid target syntax must exit 4, NOT 2."""
        invalid_targets = ["foo", "invalid", "+abc", "+", "-", "nan", "+inf"]
        for target in invalid_targets:
            with self.subTest(target=target):
                stderr = io.StringIO()
                with mock.patch("sys.stderr", stderr):
                    rc = cli.main(["seek", target])
                self.assertEqual(
                    rc,
                    4,
                    f"seek target {target!r} returned rc={rc}, expected 4 (EXIT_BAD_ARG)",
                )
                self.assertIn("seek requires a target", stderr.getvalue())

    def test_seek_invalid_target_even_when_session_inactive_mocked_exits_four(self) -> None:
        """Even if _has_active_session explicitly returns False, invalid target still exits 4."""
        stderr = io.StringIO()
        with (
            mock.patch("cli._has_active_session", return_value=False),
            mock.patch("sys.stderr", stderr),
        ):
            rc = cli.main(["seek", "foo"])
        self.assertEqual(rc, 4)
        self.assertIn("seek requires a target", stderr.getvalue())

    def test_transport_active_session_pause_resume_stop_restart(self) -> None:
        with (
            mock.patch("cli._has_active_session", return_value=True),
            mock.patch("cli._is_mpv_socket_alive", return_value=True),
            mock.patch("mpv_ipc.MpvIpc.send", return_value={"error": "success"}),
        ):
            out_pause = io.StringIO()
            with mock.patch("sys.stdout", out_pause):
                self.assertEqual(cli.main(["pause"]), 0)
            self.assertEqual(out_pause.getvalue().strip(), "paused")

            out_resume = io.StringIO()
            with mock.patch("sys.stdout", out_resume):
                self.assertEqual(cli.main(["resume"]), 0)
            self.assertEqual(out_resume.getvalue().strip(), "resumed")

            out_restart = io.StringIO()
            with mock.patch("sys.stdout", out_restart):
                self.assertEqual(cli.main(["restart"]), 0)
            self.assertEqual(out_restart.getvalue().strip(), "restarted")

            out_stop = io.StringIO()
            with mock.patch("sys.stdout", out_stop):
                self.assertEqual(cli.main(["stop"]), 0)
            self.assertEqual(out_stop.getvalue().strip(), "stopped")

    def test_transport_active_session_seek_success(self) -> None:
        with (
            mock.patch("cli._has_active_session", return_value=True),
            mock.patch("cli._is_mpv_socket_alive", return_value=True),
            mock.patch("mpv_ipc.MpvIpc.send", return_value={"error": "success"}) as mock_send,
        ):
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                self.assertEqual(cli.main(["seek", "+10"]), 0)
            self.assertEqual(out.getvalue().strip(), "seek ok")
            mock_send.assert_called_with(["seek", 10.0, "relative"], cli.MPV_SOCK_PATH)

            with mock.patch("sys.stdout", io.StringIO()):
                self.assertEqual(cli.main(["seek", "end"]), 0)
            mock_send.assert_called_with(["seek", 999999, "absolute"], cli.MPV_SOCK_PATH)


class TestCliAutoplay(BaseCliTestCase):
    """Tests for `auto-speech autoplay`."""

    def test_autoplay_on_with_explicit_session_id(self) -> None:
        rc = cli.main(["autoplay", "on", "--session-id", "sess-1"])
        self.assertEqual(rc, 0)
        marker = self.home / ".claude" / "auto-speech-autoplay-enabled" / "sess-1"
        self.assertTrue(marker.is_file())

    def test_autoplay_on_with_env_session_id(self) -> None:
        os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-env"
        rc = cli.main(["autoplay", "on"])
        self.assertEqual(rc, 0)
        marker = self.home / ".claude" / "auto-speech-autoplay-enabled" / "sess-env"
        self.assertTrue(marker.is_file())

    def test_autoplay_on_missing_session_id_fails(self) -> None:
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            rc = cli.main(["autoplay", "on"])
        self.assertEqual(rc, 1)
        self.assertIn("no session ID provided to enroll", stderr.getvalue())

    def test_autoplay_on_idempotent(self) -> None:
        self.assertEqual(cli.main(["autoplay", "on", "--session-id", "sess-1"]), 0)
        self.assertEqual(cli.main(["autoplay", "on", "--session-id", "sess-1"]), 0)

    def test_autoplay_off_with_session_id(self) -> None:
        marker = self.home / ".claude" / "auto-speech-autoplay-enabled" / "sess-1"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()

        rc = cli.main(["autoplay", "off", "--session-id", "sess-1"])
        self.assertEqual(rc, 0)
        self.assertFalse(marker.exists())

    def test_autoplay_off_idempotent(self) -> None:
        rc = cli.main(["autoplay", "off", "--session-id", "sess-absent"])
        self.assertEqual(rc, 0)

    def test_autoplay_status_output(self) -> None:
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            rc = cli.main(["autoplay", "status"])
        self.assertEqual(rc, 0)
        self.assertIn("autoplay:", stdout.getvalue())

    def test_autoplay_mode_show(self) -> None:
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            rc = cli.main(["autoplay", "mode"])
        self.assertEqual(rc, 0)
        self.assertIn("mode=", stdout.getvalue())

    def test_autoplay_mode_set_valid_modes(self) -> None:
        rc = cli.main(["autoplay", "mode", "medium"])
        self.assertEqual(rc, 0)
        cfg_file = self.home / ".config" / "auto-speech" / "autoplay.toml"
        self.assertTrue(cfg_file.is_file())
        content = cfg_file.read_text()
        self.assertIn('mode = "summary"', content)
        self.assertIn('summary_size = "medium"', content)

    def test_autoplay_mode_set_invalid_mode_fails(self) -> None:
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            rc = cli.main(["autoplay", "mode", "ultra"])
        self.assertEqual(rc, 2)
        self.assertIn("unknown value ultra", stderr.getvalue())

    def test_autoplay_scope_show(self) -> None:
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            rc = cli.main(["autoplay", "scope"])
        self.assertEqual(rc, 0)
        self.assertIn("autoplay scope:", stdout.getvalue())

    def test_autoplay_scope_set_all(self) -> None:
        solo_marker = self.home / ".claude" / "auto-speech-autoplay-solo"
        solo_marker.parent.mkdir(parents=True, exist_ok=True)
        solo_marker.write_text("old-session")

        rc = cli.main(["autoplay", "scope", "all"])
        self.assertEqual(rc, 0)
        self.assertFalse(solo_marker.exists())

    def test_autoplay_scope_set_solo_with_session(self) -> None:
        rc = cli.main(["autoplay", "scope", "solo", "--session-id", "sess-1"])
        self.assertEqual(rc, 0)
        solo_marker = self.home / ".claude" / "auto-speech-autoplay-solo"
        self.assertTrue(solo_marker.is_file())
        self.assertEqual(solo_marker.read_text().strip(), "sess-1")

    def test_autoplay_scope_set_solo_missing_session_fails(self) -> None:
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            rc = cli.main(["autoplay", "scope", "solo"])
        self.assertEqual(rc, 1)
        self.assertIn("scope solo requires a session id", stderr.getvalue())

    def test_autoplay_scope_set_invalid_scope_fails(self) -> None:
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            rc = cli.main(["autoplay", "scope", "custom"])
        self.assertEqual(rc, 2)
        self.assertIn("unknown value custom", stderr.getvalue())


class TestCliNarrate(BaseCliTestCase):
    """Tests for `auto-speech narrate`."""

    def test_narrate_on_with_session_id_creates_marker_and_starts_daemon(self) -> None:
        with mock.patch("daemon_client.DaemonClient.ensure_daemon_running") as mock_ensure:
            rc = cli.main(["narrate", "on", "--session-id", "sess-1"])
        self.assertEqual(rc, 0)
        marker = self.home / ".claude" / "auto-speech-narrate-sessions" / "sess-1"
        self.assertTrue(marker.is_file())
        mock_ensure.assert_called_once()

    def test_narrate_on_missing_session_id_fails(self) -> None:
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            rc = cli.main(["narrate", "on"])
        self.assertEqual(rc, 1)
        self.assertIn("session ID not set", stderr.getvalue())

    def test_narrate_off_removes_marker(self) -> None:
        marker = self.home / ".claude" / "auto-speech-narrate-sessions" / "sess-1"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()

        rc = cli.main(["narrate", "off", "--session-id", "sess-1"])
        self.assertEqual(rc, 0)
        self.assertFalse(marker.exists())

    def test_narrate_status_output(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch("daemon_client.DaemonClient.is_alive", return_value=True),
            mock.patch("sys.stdout", stdout),
        ):
            rc = cli.main(["narrate", "status", "--session-id", "sess-1"])
        self.assertEqual(rc, 0)
        self.assertIn("session: sess-1", stdout.getvalue())
        self.assertIn("daemon alive: True", stdout.getvalue())

    def test_narrate_stop_terminates_daemon(self) -> None:
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="narrator daemon: stopped (pid=123)")
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                rc = cli.main(["narrate", "stop"])
            self.assertEqual(rc, 0)
            self.assertIn("narrator daemon: stopped", out.getvalue())

    def test_narrate_config_show(self) -> None:
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            rc = cli.main(["narrate", "config", "show"])
        self.assertEqual(rc, 0)

    def test_narrate_config_path(self) -> None:
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            rc = cli.main(["narrate", "config", "path"])
        self.assertEqual(rc, 0)

    def test_narrate_config_edit(self) -> None:
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            rc = cli.main(["narrate", "config", "edit"])
        self.assertEqual(rc, 0)

    def test_narrate_config_invalid_action_fails(self) -> None:
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            rc = cli.main(["narrate", "config", "delete"])
        self.assertEqual(rc, 2)
        self.assertIn("unknown action delete", stderr.getvalue())


class TestCliYield(BaseCliTestCase):
    """`auto-speech yield …` — the sound-yield switch and exception list.
    HOME is sandboxed, so the config file lands in the temp home."""

    def _cfg(self) -> Path:
        return self.home / ".config" / "auto-speech" / "sound-yield.toml"

    def _run(self, *argv: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            rc = cli.main(list(argv))
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_yield_status_defaults(self) -> None:
        rc, out, _ = self._run("yield", "status")
        self.assertEqual(rc, 0)
        self.assertIn("sound yield: ON — speech holds while another app plays sound", out)
        self.assertIn("exceptions (apps whose sound does not hold speech): none", out)
        self.assertIn("longest hold: 180 s, then set aside as missed (auto-speech missed)", out)
        self.assertIn("config: defaults (no config file yet)", out)
        self.assertFalse(self._cfg().exists())

    def test_yield_without_action_shows_status(self) -> None:
        rc, out, _ = self._run("yield")
        self.assertEqual(rc, 0)
        self.assertIn("sound yield: ON", out)

    def test_yield_off_and_on_write_the_config(self) -> None:
        rc, out, _ = self._run("yield", "off")
        self.assertEqual(rc, 0)
        self.assertIn("sound yield: OFF — speech plays over other apps' sound", out)
        self.assertIn("enabled = false", self._cfg().read_text())
        rc, out, _ = self._run("yield", "on")
        self.assertEqual(rc, 0)
        self.assertIn("sound yield: ON", out)
        self.assertIn("enabled = true", self._cfg().read_text())

    def test_yield_add_remove_clear(self) -> None:
        rc, out, _ = self._run("yield", "add", "Music", "com.spotify.client", "music")
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("added: Music, com.spotify.client"))
        self.assertIn('exceptions = ["Music", "com.spotify.client"]', self._cfg().read_text())
        rc, out, _ = self._run("yield", "add", "COM.SPOTIFY.CLIENT")
        self.assertEqual(rc, 0)
        self.assertIn("added: nothing new", out)
        rc, out, _ = self._run("yield", "remove", "MUSIC", "nope")
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("removed: Music"))
        rc, out, _ = self._run("yield", "clear")
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("exceptions cleared"))
        self.assertIn("exceptions = []", self._cfg().read_text())

    def test_yield_status_names_the_rule_and_the_players(self) -> None:
        rc, out, _ = self._run("yield", "status")
        self.assertEqual(rc, 0)
        self.assertIn("holds for: players only", out)
        self.assertIn("players (apps whose sound holds speech): com.apple.Music", out)

    def test_yield_player_add_remove_reset_and_hold_for(self) -> None:
        rc, out, _ = self._run("yield", "player-add", "com.google.antigravity", "Music")
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("players added: com.google.antigravity"), out)
        self.assertIn('"com.google.antigravity"', self._cfg().read_text())
        rc, out, _ = self._run("yield", "player-remove", "Chrome", "nope")
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("players removed: com.google.Chrome"), out)
        rc, out, _ = self._run("yield", "player-reset")
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("players reset to the defaults"), out)
        self.assertIn('"com.google.Chrome"', self._cfg().read_text())
        rc, out, _ = self._run("yield", "hold-for", "any")
        self.assertEqual(rc, 0)
        self.assertIn("holds for: any app", out)
        self.assertIn('hold_for = "any"', self._cfg().read_text())
        rc, _, err = self._run("yield", "hold-for", "sometimes")
        self.assertEqual(rc, cli.EXIT_INVALID_INPUT)
        self.assertIn("players or any", err)

    def test_yield_player_add_without_apps_is_a_usage_error(self) -> None:
        rc, _, err = self._run("yield", "player-add")
        self.assertEqual(rc, 2)
        self.assertIn("required: apps", err)

    def test_yield_add_without_apps_is_a_usage_error(self) -> None:
        rc, _, err = self._run("yield", "add")
        self.assertEqual(rc, 2)
        self.assertIn("required: apps", err)

    def test_yield_unknown_action_is_a_usage_error(self) -> None:
        rc, _, err = self._run("yield", "mute")
        self.assertEqual(rc, 2)

    def test_sound_yield_alias_routes_the_same(self) -> None:
        rc, out, _ = self._run("sound-yield", "off")
        self.assertEqual(rc, 0)
        self.assertIn("sound yield: OFF", out)

    def test_yield_check_uses_the_gate(self) -> None:
        from sound_yield import MODE_PROCESS_LIST, Assessment, AudioProcess, SoundYieldSettings

        music = AudioProcess(pid=501, bundle_id="com.apple.Music", name="Music")
        a = Assessment(hold=True, mode=MODE_PROCESS_LIST, settings=SoundYieldSettings(), blockers=[music])
        gate = mock.MagicMock()
        gate.assess.return_value = a
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            args = cli.build_parser().parse_args(["yield", "check"])
            rc = cli.handle_yield(args, gate=gate)
        self.assertEqual(rc, 0)
        self.assertIn("playing: Music (com.apple.Music) (pid 501) — holds speech", stdout.getvalue())
        self.assertIn("speech would HOLD (up to 180 s, then set aside as missed)", stdout.getvalue())

    def test_yield_config_shape_matches_mcp_and_file(self) -> None:
        import tomllib

        self._run("yield", "off")
        self._run("yield", "add", "Music")
        data = tomllib.loads(self._cfg().read_text())["sound_yield"]
        self.assertEqual(set(data), {"enabled", "hold_for", "players", "exceptions", "max_wait_seconds", "poll_seconds"})
        self.assertEqual(data["enabled"], False)
        self.assertEqual(data["exceptions"], ["Music"])


class TestCliSpeaker(BaseCliTestCase):
    """`auto-speech speaker …`: the desk is named only when the speaker
    changes. HOME is sandboxed (the config lands in the temp home); the agent
    notes are a temp dir named by AUTO_SPEECH_AGENTS_DIR."""

    def setUp(self) -> None:
        super().setUp()
        os.environ.pop("AUTO_SPEECH_SPEAKER_CONFIG", None)
        agents = self.home / "Agents"
        agents.mkdir()
        (agents / "Desk.md").write_text(
            '---\nname: "Master Desk"\nstate: working\nsession: sess-master\nsection: "[[Desk/AspenESS|AspenESS]]"\n---\n',
            encoding="utf-8",
        )
        (agents / "Personal - Finance.md").write_text(
            '---\nstate: done\nsession: sess-personal\nsection: "[[Personal]]"\n---\n', encoding="utf-8"
        )
        os.environ["AUTO_SPEECH_AGENTS_DIR"] = str(agents)
        from speaker import SpeakerStore, default_config_path

        self.cfg = default_config_path()
        self.assertTrue(str(self.cfg).startswith(str(self.home)))
        self.store = SpeakerStore(self.cfg)

    def _run(self, *argv: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            rc = cli.main(list(argv))
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_status_is_the_default(self) -> None:
        rc, out, _ = self._run("speaker")
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("speaker names: on\nquiet gap: 15 min"), out)
        self.assertIn("desks known: 1", out)

    def test_off_on_and_gap(self) -> None:
        rc, out, _ = self._run("speaker", "off")
        self.assertEqual(rc, 0)
        self.assertIn("speaker names: off", out)
        self.assertFalse(self.store.load().enabled)
        rc, out, _ = self._run("speaker", "on")
        self.assertIn("speaker names: on", out)
        rc, out, _ = self._run("speaker", "gap", "300")
        self.assertEqual(rc, 0)
        self.assertIn("quiet gap: 5 min", out)
        self.assertEqual(self.store.load().quiet_gap_seconds, 300.0)
        rc, _, err = self._run("speaker", "gap", "-1")
        self.assertEqual(rc, cli.EXIT_INVALID_INPUT)
        self.assertIn(">= 0", err)

    def test_name_forget_and_who(self) -> None:
        rc, out, _ = self._run("speaker", "name", "sess-x", "Side", "Desk")
        self.assertEqual(rc, 0)
        self.assertIn("'Side Desk'", out)
        self.assertEqual(dict(self.store.load().names), {"sess-x": "Side Desk"})
        rc, out, _ = self._run("speaker", "who", "sess-x")
        self.assertIn("Side Desk", out)
        rc, out, _ = self._run("speaker", "who", "sess-master")
        self.assertIn("Master Desk", out)
        rc, out, _ = self._run("speaker", "who", "sess-personal")
        self.assertIn("Another desk", out, "a personal note is never read into the map")
        rc, out, _ = self._run("speaker", "forget", "sess-x")
        self.assertEqual(rc, 0)
        self.assertEqual(self.store.load().names, ())

    def test_who_defaults_to_the_shell_session(self) -> None:
        rc, _, err = self._run("speaker", "who")
        self.assertEqual(rc, cli.EXIT_INVALID_INPUT)
        os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-master"
        rc, out, _ = self._run("speaker", "who")
        self.assertEqual(rc, 0)
        self.assertIn("Master Desk", out)


class TestCliMissed(BaseCliTestCase):
    """`auto-speech missed [--play|--clear]` — the lines held or set aside
    while another app played sound. HOME is sandboxed, so the list lands in
    the temp home (~/.local/state/auto-speech/missed.json)."""

    def setUp(self) -> None:
        super().setUp()
        os.environ.pop("AUTO_SPEECH_MISSED_FILE", None)
        from missed_speech import MissedList, default_missed_path

        self.list_path = default_missed_path()
        self.assertTrue(str(self.list_path).startswith(str(self.home)))
        self.missed = MissedList(self.list_path)

    def _run(self, *argv: str, client=None) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            rc = cli.main(list(argv), client=client)
        return rc, stdout.getvalue(), stderr.getvalue()

    def _seed(self) -> None:
        from missed_speech import REASON_LONGEST_WAIT, MissedLine

        self.missed.miss(
            MissedLine(id="aa11", text="The build is green.", queued_at=1_700_000_000.0, voice_id="af_bella", speed=1.1),
            reason=REASON_LONGEST_WAIT,
            waited_seconds=180.0,
            held_for=("ChatGPT (pid 9)",),
        )
        self.missed.hold(MissedLine(id="bb22", text="Second line.", queued_at=1_700_000_100.0), ("ChatGPT (pid 9)",))

    def test_missed_lists_nothing_when_empty(self) -> None:
        rc, out, _ = self._run("missed")
        self.assertEqual(rc, 0)
        self.assertIn("missed speech: nothing missed, nothing held", out)
        self.assertFalse(self.list_path.exists())  # a read never creates the file

    def test_missed_lists_held_and_missed_lines_oldest_first(self) -> None:
        self._seed()
        rc, out, _ = self._run("missed")
        self.assertEqual(rc, 0)
        self.assertIn("missed speech: 2 lines you have not heard (1 held, 1 missed), oldest first", out)
        self.assertLess(out.index("The build is green."), out.index("Second line."))
        self.assertIn("missed: longest wait (180 s) while ChatGPT (pid 9) played", out)
        self.assertIn("held ", out)
        self.assertIn("replay the missed lines, oldest first: auto-speech missed --play", out)
        self.assertIn("1 held line will play by themselves when the sound stops", out)

    def test_missed_play_replays_missed_lines_through_the_daemon_without_a_hold(self) -> None:
        self._seed()
        client = mock.MagicMock()
        rc, out, _ = self._run("missed", "--play", client=client)
        self.assertEqual(rc, 0)
        self.assertIn("missed speech: replaying 1 line, oldest first, over any other sound", out)
        self.assertIn("each leaves the list once it has played", out)
        client.speak.assert_called_once()
        _, kwargs = client.speak.call_args
        self.assertEqual(client.speak.call_args[0][0], "The build is green.")
        self.assertEqual(kwargs["missed_id"], "aa11")
        self.assertIs(kwargs["hold"], False)
        self.assertEqual(kwargs["voice_id"], "af_bella")
        self.assertEqual(kwargs["speed"], 1.1)
        client.play_cache.assert_not_called()  # the held line stays with the daemon
        # The list is the daemon's to clear once the line has played.
        self.assertEqual([m.id for m in self.missed.load()], ["aa11", "bb22"])

    def test_missed_play_with_nothing_missed_says_so(self) -> None:
        client = mock.MagicMock()
        rc, out, _ = self._run("missed", "--play", client=client)
        self.assertEqual(rc, 0)
        self.assertIn("missed speech: nothing to replay", out)
        client.speak.assert_not_called()

    def test_missed_play_reports_a_daemon_that_cannot_be_reached(self) -> None:
        self._seed()
        client = mock.MagicMock()
        client.speak.side_effect = ConnectionError("no socket")
        rc, _, err = self._run("missed", "--play", client=client)
        self.assertEqual(rc, cli.EXIT_FAILURE)
        self.assertIn("could not reach the auto-speech daemon", err)

    def test_missed_clear_forgets_everything(self) -> None:
        self._seed()
        rc, out, _ = self._run("missed", "--clear")
        self.assertEqual(rc, 0)
        self.assertIn("missed speech: cleared 2 lines", out)
        self.assertEqual(self.missed.load(), [])

    def test_missed_play_and_clear_are_exclusive(self) -> None:
        rc, _, err = self._run("missed", "--play", "--clear")
        self.assertNotEqual(rc, 0)
        self.assertIn("not allowed with", err)


class TestCliHook(BaseCliTestCase):
    """Tests for `auto-speech hook`."""

    def test_hook_stop_claude_code_payload(self) -> None:
        payload = json.dumps({"session_id": "sess-1", "transcript_path": "/tmp/test.jsonl"})
        # Enroll session
        marker = self.home / ".claude" / "auto-speech-autoplay-enabled" / "sess-1"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()

        with (
            mock.patch("sys.stdin", io.StringIO(payload)),
            mock.patch("daemon_client.DaemonClient.ensure_daemon_running"),
            mock.patch("subprocess.Popen") as mock_popen,
        ):
            rc = cli.main(["hook", "stop"])
        self.assertEqual(rc, 0)
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        self.assertEqual(cmd[0], "bash")
        self.assertTrue(cmd[1].endswith("autoplay_worker.sh"))
        self.assertEqual(cmd[3], "/tmp/test.jsonl")
        self.assertEqual(cmd[4], "sess-1")

    def test_hook_stop_antigravity_payload(self) -> None:
        payload = json.dumps({"conversationId": "conv-1", "transcriptPath": "/tmp/test.jsonl"})
        # Enroll session
        marker = self.home / ".claude" / "auto-speech-autoplay-enabled" / "conv-1"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()

        with (
            mock.patch("sys.stdin", io.StringIO(payload)),
            mock.patch("daemon_client.DaemonClient.ensure_daemon_running"),
            mock.patch("subprocess.Popen") as mock_popen,
        ):
            rc = cli.main(["hook", "stop"])
        self.assertEqual(rc, 0)
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        self.assertEqual(cmd[0], "bash")
        self.assertTrue(cmd[1].endswith("autoplay_worker.sh"))
        self.assertEqual(cmd[3], "/tmp/test.jsonl")
        self.assertEqual(cmd[4], "conv-1")

    def test_hook_stop_muted_bails_early(self) -> None:
        disabled_marker = self.home / ".claude" / "auto-speech.disabled"
        disabled_marker.parent.mkdir(parents=True, exist_ok=True)
        disabled_marker.touch()

        with mock.patch("subprocess.Popen") as mock_popen:
            rc = cli.main(["hook", "stop"])
        self.assertEqual(rc, 0)
        mock_popen.assert_not_called()

    def test_hook_stop_suppressed_bails_early(self) -> None:
        os.environ["AUTO_SPEECH_SUPPRESS_HOOKS"] = "1"
        with mock.patch("subprocess.Popen") as mock_popen:
            rc = cli.main(["hook", "stop"])
        self.assertEqual(rc, 0)
        mock_popen.assert_not_called()

    def test_hook_stop_malformed_json_bails_cleanly(self) -> None:
        with (
            mock.patch("sys.stdin", io.StringIO("not valid json")),
            mock.patch("subprocess.Popen") as mock_popen,
        ):
            rc = cli.main(["hook", "stop"])
        self.assertEqual(rc, 0)
        mock_popen.assert_not_called()

    def test_hook_stop_non_dict_json_payloads_bail_cleanly(self) -> None:
        """Non-dict valid JSON primitives and arrays must not cause AttributeError."""
        non_dict_payloads = [
            "[1, 2, 3]",
            "42",
            '"a string"',
            "null",
            "true",
            "false",
            "3.14",
        ]
        for raw in non_dict_payloads:
            with (
                self.subTest(payload=raw),
                mock.patch("sys.stdin", io.StringIO(raw)),
                mock.patch("subprocess.Popen") as mock_popen,
            ):
                rc = cli.main(["hook", "stop"])
                self.assertEqual(rc, 0)
                mock_popen.assert_not_called()

    def test_hook_stop_missing_keys_bails_cleanly(self) -> None:
        """Dict payloads with missing or non-string keys must bail cleanly."""
        payloads = [
            "{}",
            '{"unrelated_key": "val"}',
            '{"session_id": null}',
            '{"session_id": ""}',
            '{"session_id": 12345}',
        ]
        for raw in payloads:
            with (
                self.subTest(payload=raw),
                mock.patch("sys.stdin", io.StringIO(raw)),
                mock.patch("subprocess.Popen") as mock_popen,
            ):
                rc = cli.main(["hook", "stop"])
                self.assertEqual(rc, 0)
                mock_popen.assert_not_called()

    def test_hook_narrator_tool_event_appended(self) -> None:
        payload = json.dumps({"session_id": "sess-1", "hook_event_name": "PreToolUse"})
        # Enroll session
        marker = self.home / ".claude" / "auto-speech-narrate-sessions" / "sess-1"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()

        events_file = state_path("auto-speech-narrator-events.jsonl")
        events_file.unlink(missing_ok=True)

        with (
            mock.patch("sys.stdin", io.StringIO(payload)),
            mock.patch("daemon_client.DaemonClient.ensure_daemon_running"),
        ):
            rc = cli.main(["hook", "narrator"])
        self.assertEqual(rc, 0)
        self.assertTrue(events_file.is_file())
        lines = events_file.read_text().strip().splitlines()
        self.assertGreater(len(lines), 0)
        record = json.loads(lines[-1])
        self.assertEqual(record["event"], "PreToolUse")
        events_file.unlink(missing_ok=True)

    def test_hook_narrator_antigravity_event(self) -> None:
        payload = json.dumps({"conversationId": "conv-1", "hook_event_name": "PostToolUse"})
        marker = self.home / ".claude" / "auto-speech-narrate-sessions" / "conv-1"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()

        events_file = state_path("auto-speech-narrator-events.jsonl")
        events_file.unlink(missing_ok=True)

        with (
            mock.patch("sys.stdin", io.StringIO(payload)),
            mock.patch("daemon_client.DaemonClient.ensure_daemon_running"),
        ):
            rc = cli.main(["hook", "narrator"])
        self.assertEqual(rc, 0)
        self.assertTrue(events_file.is_file())
        events_file.unlink(missing_ok=True)

    def test_hook_narrator_unenrolled_session_bails_early(self) -> None:
        payload = json.dumps({"session_id": "unenrolled", "hook_event_name": "PreToolUse"})
        events_file = state_path("auto-speech-narrator-events.jsonl")
        events_file.unlink(missing_ok=True)

        with mock.patch("sys.stdin", io.StringIO(payload)):
            rc = cli.main(["hook", "narrator"])
        self.assertEqual(rc, 0)
        self.assertFalse(events_file.is_file())

    def test_hook_narrator_non_dict_json_payloads_bail_cleanly(self) -> None:
        """Narrator hook must safely handle non-dict JSON and exit 0."""
        events_file = state_path("auto-speech-narrator-events.jsonl")
        events_file.unlink(missing_ok=True)

        non_dict_payloads = [
            "[1, 2, 3]",
            "42",
            '"a string"',
            "null",
            "true",
            "false",
            "3.14",
            "not valid json at all",
        ]
        for raw in non_dict_payloads:
            with (
                self.subTest(payload=raw),
                mock.patch("sys.stdin", io.StringIO(raw)),
                mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": ""}, clear=False),
            ):
                rc = cli.main(["hook", "narrator"])
                self.assertEqual(rc, 0)
        self.assertFalse(events_file.is_file())

    def test_hook_narrator_missing_keys_bails_cleanly(self) -> None:
        """Narrator hook with empty dict or missing session_id must exit 0."""
        events_file = state_path("auto-speech-narrator-events.jsonl")
        events_file.unlink(missing_ok=True)

        payloads = [
            "{}",
            '{"unrelated": 123}',
            '{"session_id": null}',
            '{"session_id": ""}',
        ]
        for raw in payloads:
            with (
                self.subTest(payload=raw),
                mock.patch("sys.stdin", io.StringIO(raw)),
                mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": ""}, clear=False),
            ):
                rc = cli.main(["hook", "narrator"])
                self.assertEqual(rc, 0)
        self.assertFalse(events_file.is_file())

    def test_hook_bootstrap_checks_and_syncs(self) -> None:
        with (
            mock.patch("cli.needs_sync", return_value=True),
            mock.patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            rc = cli.main(["hook", "bootstrap"])
        self.assertEqual(rc, 0)
        mock_run.assert_called_once()

    def test_hook_bootstrap_forced(self) -> None:
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            rc = cli.main(["hook", "bootstrap", "--force"])
        self.assertEqual(rc, 0)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertIn("--force", cmd)


class TestCliDoctorAndUpdate(BaseCliTestCase):
    """Tests for doctor and update subcommands."""

    def test_doctor_healthy_system_text_output(self) -> None:
        report = HealthReport(
            checks=[Check(name="binaries", status=Status.OK, detail="all ok")],
        )
        stdout = io.StringIO()
        with (
            mock.patch("doctor.Doctor.run", return_value=report),
            mock.patch("sys.stdout", stdout),
        ):
            rc = cli.main(["doctor"])
        self.assertEqual(rc, 0)
        self.assertIn("HEALTHY", stdout.getvalue())

    def test_doctor_json_output_flag(self) -> None:
        report = HealthReport(
            checks=[Check(name="binaries", status=Status.OK, detail="all ok")],
        )
        stdout = io.StringIO()
        with (
            mock.patch("doctor.Doctor.run", return_value=report),
            mock.patch("sys.stdout", stdout),
        ):
            rc = cli.main(["doctor", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(stdout.getvalue())
        self.assertTrue(data["healthy"])

    def test_doctor_unhealthy_probe_exits_one(self) -> None:
        report = HealthReport(
            checks=[Check(name="disk", status=Status.FAIL, detail="disk full")],
        )
        with (
            mock.patch("doctor.Doctor.run", return_value=report),
            mock.patch("sys.stdout", io.StringIO()),
        ):
            rc = cli.main(["doctor"])
        self.assertEqual(rc, 1)

    def test_update_calls_bootstrap(self) -> None:
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                rc = cli.main(["update"])
        self.assertEqual(rc, 0)
        self.assertIn("dependencies updated and in sync", out.getvalue())

    def test_update_forced_flag(self) -> None:
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess([], 0)
            rc = cli.main(["update", "--force"])
        self.assertEqual(rc, 0)
        cmd = mock_run.call_args[0][0]
        self.assertIn("--force", cmd)


class TestCliTrampolineIntegration(unittest.TestCase):
    """Subprocess integration test for bin/auto-speech."""

    def test_bin_trampoline_help_invocation(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        bin_script = repo_root / "bin" / "auto-speech"
        self.assertTrue(bin_script.is_file(), "bin/auto-speech missing")
        self.assertTrue(os.access(bin_script, os.X_OK), "bin/auto-speech not executable")

        res = subprocess.run(
            [str(bin_script), "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("Unified CLI dispatcher for auto-speech", res.stdout)
        self.assertIn("speak", res.stdout)
        self.assertIn("update", res.stdout)

    def test_bin_hook_non_dict_stdin_exit_zero(self) -> None:
        """Integration test: trampoline exits 0 on non-dict stdin without traceback."""
        repo_root = Path(__file__).resolve().parents[1]
        bin_script = repo_root / "bin" / "auto-speech"
        for subcmd in ["stop", "narrator"]:
            for payload in ["[1, 2, 3]", "42", "null", '"foo"', "true"]:
                res = subprocess.run(
                    [str(bin_script), "hook", subcmd],
                    input=payload,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(
                    res.returncode,
                    0,
                    f"hook {subcmd} failed with exit {res.returncode} for input {payload}: {res.stderr}",
                )
                self.assertNotIn("Traceback", res.stderr)


if __name__ == "__main__":
    unittest.main()
