"""Unit tests for DedupGuard.

Reproduces the coverage of the retired test_autoplay_dedup.sh: marker
absent, hash mismatch, no mpv, matching hash + live mpv, stale marker
(>120s), and a dead mpv pid.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from dedup_guard import DedupGuard  # noqa: E402

HASH = "abc123"


def _files():
    d = Path(tempfile.mkdtemp(prefix="auto-speech-dedup-test-"))
    return d / "now-playing", d / "mpv.pid"


def _claim_files():
    d = Path(tempfile.mkdtemp(prefix="auto-speech-dedup-test-"))
    return d / "now-playing", d / "mpv.pid", d / "lock"


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


def test_try_claim_first_wins_second_suppressed() -> None:
    """Two same-hash workers reaching the head sequentially: only one claims.

    Unlike already_playing(), try_claim() does NOT require mpv alive, so the
    second worker is suppressed even though the first play's mpv has exited
    (pid file empty) — closing the FIFO duplicate race.
    """
    marker, pid, lock = _claim_files()
    g1 = DedupGuard(marker_path=marker, mpv_pid_path=pid, lock_path=lock)
    g2 = DedupGuard(marker_path=marker, mpv_pid_path=pid, lock_path=lock)
    assert g1.try_claim(HASH) is True
    assert g2.try_claim(HASH) is False


def test_try_claim_different_hash_allowed() -> None:
    marker, pid, lock = _claim_files()
    g = DedupGuard(marker_path=marker, mpv_pid_path=pid, lock_path=lock)
    assert g.try_claim(HASH) is True
    assert g.try_claim("a-different-hash") is True


def test_try_claim_after_age_cap_allows_replay() -> None:
    """A fresh claim of the same hash >120s later is permitted (legit replay)."""
    marker, pid, lock = _claim_files()
    DedupGuard(marker_path=marker, mpv_pid_path=pid, lock_path=lock).try_claim(HASH)
    mtime = marker.stat().st_mtime
    late = DedupGuard(
        marker_path=marker, mpv_pid_path=pid, lock_path=lock, now=lambda: mtime + 200
    )
    assert late.try_claim(HASH) is True


def test_try_claim_concurrent_single_winner() -> None:
    """N threads racing to claim the same hash: exactly one wins."""
    marker, pid, lock = _claim_files()
    results = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        g = DedupGuard(marker_path=marker, mpv_pid_path=pid, lock_path=lock)
        barrier.wait()  # maximize contention on the lock
        results.append(g.try_claim(HASH))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1, results
    assert results.count(False) == 7, results


def main() -> int:
    tests = [
        test_marker_absent,
        test_hash_mismatch,
        test_no_mpv,
        test_matching_hash_live_mpv,
        test_stale_marker_over_120s,
        test_dead_mpv_pid,
        test_write_now_playing,
        test_try_claim_first_wins_second_suppressed,
        test_try_claim_different_hash_allowed,
        test_try_claim_after_age_cap_allows_replay,
        test_try_claim_concurrent_single_winner,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"dedup_guard: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
