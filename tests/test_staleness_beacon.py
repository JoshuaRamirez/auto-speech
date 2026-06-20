"""Unit tests for StalenessBeacon.

Covers the per-session vs global beacon path derivation, the re-stat
is_stale() semantics (absent beacon, fresh beacon, advanced beacon), and
the monotonic FRESH→STALE latch.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

import staleness_beacon  # noqa: E402
from staleness_beacon import FRESH, STALE, StalenessBeacon, beacon_path  # noqa: E402


def test_path_derivation() -> None:
    assert str(beacon_path(None)) == staleness_beacon.BEACON_DEFAULT
    assert str(beacon_path("")) == staleness_beacon.BEACON_DEFAULT
    assert str(beacon_path("abc")) == f"{staleness_beacon.BEACON_DEFAULT}.abc"


def _temp_beacon() -> Path:
    d = Path(tempfile.mkdtemp(prefix="auto-speech-beacon-test-"))
    return d / "beacon"


def test_absent_beacon_is_not_stale() -> None:
    p = _temp_beacon()  # not created
    b = StalenessBeacon.__new__(StalenessBeacon)
    # Construct directly with a controlled path.
    StalenessBeacon.__init__(b, start_mtime=1000.0)
    b._path = p
    assert b.is_stale() is False
    assert b.state == FRESH


def test_fresh_beacon_not_stale_advanced_beacon_stale() -> None:
    p = _temp_beacon()
    p.write_text("", encoding="utf-8")
    start = p.stat().st_mtime
    b = StalenessBeacon(start_mtime=start)
    b._path = p
    assert b.is_stale() is False  # equal mtime is not > start
    assert b.state == FRESH
    # Advance the beacon mtime past start.
    future = start + 5
    import os

    os.utime(p, (future, future))
    assert b.is_stale() is True
    assert b.state == STALE


def test_latch_is_monotonic() -> None:
    p = _temp_beacon()
    p.write_text("", encoding="utf-8")
    start = p.stat().st_mtime
    b = StalenessBeacon(start_mtime=start)
    b._path = p
    import os

    os.utime(p, (start + 5, start + 5))
    assert b.is_stale() is True
    assert b.state == STALE
    # Even if the beacon somehow regressed, the latch stays STALE.
    os.utime(p, (start - 5, start - 5))
    # is_stale() re-stats and now returns False, but the recorded state
    # remains STALE (terminal latch — never transitions back).
    assert b.is_stale() is False
    assert b.state == STALE


def main() -> int:
    tests = [
        test_path_derivation,
        test_absent_beacon_is_not_stale,
        test_fresh_beacon_not_stale_advanced_beacon_stale,
        test_latch_is_monotonic,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"staleness_beacon: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
