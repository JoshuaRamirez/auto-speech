"""Unit tests for DedupGuard.

Reproduces the coverage of the retired test_autoplay_dedup.sh: marker
absent, hash mismatch, no mpv, matching hash + live mpv, stale marker
(>120s), and a dead mpv pid.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from dedup_guard import DedupGuard  # noqa: E402

HASH = "abc123"


def _files():
    d = Path(tempfile.mkdtemp(prefix="auto-speech-dedup-test-"))
    return d / "now-playing", d / "mpv.pid"


def _guard(marker, pid_path, now=time.time):
    return DedupGuard(marker_path=marker, mpv_pid_path=pid_path, now=now)


def test_marker_absent() -> None:
    marker, pid = _files()
    assert _guard(marker, pid).already_playing(HASH) is False


def test_hash_mismatch() -> None:
    marker, pid = _files()
    marker.write_text("different-hash", encoding="utf-8")
    pid.write_text(f"{os.getpid()}", encoding="utf-8")
    assert _guard(marker, pid).already_playing(HASH) is False


def test_no_mpv() -> None:
    marker, pid = _files()
    marker.write_text(HASH, encoding="utf-8")
    pid.write_text("", encoding="utf-8")
    assert _guard(marker, pid).already_playing(HASH) is False


def test_matching_hash_live_mpv() -> None:
    marker, pid = _files()
    marker.write_text(HASH, encoding="utf-8")
    pid.write_text(f"{os.getpid()}", encoding="utf-8")  # our own live pid
    assert _guard(marker, pid).already_playing(HASH) is True


def test_stale_marker_over_120s() -> None:
    marker, pid = _files()
    marker.write_text(HASH, encoding="utf-8")
    pid.write_text(f"{os.getpid()}", encoding="utf-8")
    mtime = marker.stat().st_mtime
    # now is 200s after the marker mtime → age 200 ≥ 120 → not playing.
    g = _guard(marker, pid, now=lambda: mtime + 200)
    assert g.already_playing(HASH) is False


def test_dead_mpv_pid() -> None:
    marker, pid = _files()
    marker.write_text(HASH, encoding="utf-8")
    pid.write_text("999999", encoding="utf-8")  # safely non-existent
    assert _guard(marker, pid).already_playing(HASH) is False


def test_write_now_playing() -> None:
    marker, pid = _files()
    g = _guard(marker, pid)
    g.write_now_playing(HASH)
    assert marker.read_text(encoding="utf-8") == HASH


def main() -> int:
    tests = [
        test_marker_absent,
        test_hash_mismatch,
        test_no_mpv,
        test_matching_hash_live_mpv,
        test_stale_marker_over_120s,
        test_dead_mpv_pid,
        test_write_now_playing,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"dedup_guard: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
