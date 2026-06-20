"""Unit tests for auto_speech_log (size-capped logging).

Covers env-driven cap/backup overrides, get_logger idempotency and
mid-run rotation via RotatingFileHandler, and rotate_if_oversize's
pre-spawn cascade (under cap = no-op, over cap = rename + oldest dropped).
All hermetic: temp dirs only, no /tmp, no real daemon.
"""
from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

import auto_speech_log as asl  # noqa: E402


def test_cap_defaults_and_override() -> None:
    with patch.dict("os.environ", {}, clear=False):
        import os

        os.environ.pop("AUTO_SPEECH_LOG_MAX_BYTES", None)
        os.environ.pop("AUTO_SPEECH_LOG_BACKUPS", None)
        assert asl.max_bytes() == asl.DEFAULT_MAX_BYTES
        assert asl.backup_count() == asl.DEFAULT_BACKUPS
    with patch.dict("os.environ", {"AUTO_SPEECH_LOG_MAX_BYTES": "123", "AUTO_SPEECH_LOG_BACKUPS": "2"}):
        assert asl.max_bytes() == 123
        assert asl.backup_count() == 2
    with patch.dict("os.environ", {"AUTO_SPEECH_LOG_MAX_BYTES": "not-an-int"}):
        assert asl.max_bytes() == asl.DEFAULT_MAX_BYTES  # tolerant fallback


def test_get_logger_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "x.log"
        a = asl.get_logger("test.idem", path)
        b = asl.get_logger("test.idem", path)
        assert a is b
        # No duplicate handlers stacked for the same (name, path).
        rot = [h for h in a.handlers if getattr(h, "_auto_speech_id", None) == f"auto-speech-rot:{path}"]
        assert len(rot) == 1


def test_get_logger_rotates_mid_run() -> None:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "daemon.log"
        with patch.dict("os.environ", {"AUTO_SPEECH_LOG_MAX_BYTES": "200", "AUTO_SPEECH_LOG_BACKUPS": "2"}):
            logger = asl.get_logger("test.rotate.midrun", path)
            for i in range(50):
                logger.info("x" * 40 + f" line {i}")
            for h in logger.handlers:
                h.flush()
        # Active file exists and at least one backup was produced.
        assert path.exists()
        assert path.with_name("daemon.log.1").exists()
        # Active file is bounded near the cap (not the full 50-line volume).
        assert path.stat().st_size <= 4000


def test_rotate_if_oversize_noop_under_cap() -> None:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "a.log"
        path.write_text("small", encoding="utf-8")
        assert asl.rotate_if_oversize(path, cap=1024) is False
        assert path.exists()
        assert not path.with_name("a.log.1").exists()


def test_rotate_if_oversize_rotates_over_cap() -> None:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "a.log"
        path.write_text("X" * 100, encoding="utf-8")
        assert asl.rotate_if_oversize(path, cap=50) is True
        assert not path.exists()  # renamed away; caller recreates on append
        assert path.with_name("a.log.1").read_text(encoding="utf-8") == "X" * 100


def test_rotate_if_oversize_cascade_drops_oldest() -> None:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "a.log"
        with patch.dict("os.environ", {"AUTO_SPEECH_LOG_BACKUPS": "2"}):
            # Three rotations with backups=2 ⇒ keep .1 and .2, drop the rest.
            for tag in ("first", "second", "third"):
                path.write_text(tag, encoding="utf-8")
                assert asl.rotate_if_oversize(path, cap=1) is True
            assert path.with_name("a.log.1").read_text() == "third"
            assert path.with_name("a.log.2").read_text() == "second"
            assert not path.with_name("a.log.3").exists()  # "first" dropped


def test_rotate_if_oversize_missing_file() -> None:
    with tempfile.TemporaryDirectory() as d:
        assert asl.rotate_if_oversize(Path(d) / "nope.log", cap=1) is False


def main() -> int:
    tests = [
        test_cap_defaults_and_override,
        test_get_logger_is_idempotent,
        test_get_logger_rotates_mid_run,
        test_rotate_if_oversize_noop_under_cap,
        test_rotate_if_oversize_rotates_over_cap,
        test_rotate_if_oversize_cascade_drops_oldest,
        test_rotate_if_oversize_missing_file,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    # Avoid leaking test loggers/handlers into other test modules.
    logging.Logger.manager.loggerDict.clear()
    print(f"auto_speech_log: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
