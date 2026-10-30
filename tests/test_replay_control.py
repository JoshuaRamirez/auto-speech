"""Unit tests for control.py caller realignment.

The old replay.py (the cache replay behind run_replay.sh) was retired in T40:
replay plays from the speech history (cli.py, mcp_server.py, history_replay.py;
tests/test_replay_from_history.py and tests/test_mcp_replay_from_history.py)."""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
import argparse
import io
import sys
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import control

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


def main() -> int:
    tests = [
        test_control_ensure_session_returns_no_session,
        test_control_subcommands_exit_no_session,
        test_control_seek_argument_validation,
        test_control_seek_without_session_returns_bad_arg_when_empty,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"replay_control: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
