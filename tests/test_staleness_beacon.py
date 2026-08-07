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


def test_hook_captured_mtime_is_not_stale() -> None:
    """Regression: the hook→worker mtime handoff must round-trip.

    Every other test seeds start_mtime from Path.stat().st_mtime directly,
    which hides how the value actually reaches the worker: autoplay_hook.sh
    captures it with `stat` and passes it as a decimal STRING on argv. When
    that capture truncated to whole seconds (`stat -f %m`), the true
    sub-second mtime was always greater, so EVERY worker declared itself
    superseded by its own beacon and bailed — autoplay went silent with no
    error anywhere. This asserts a freshly-stamped beacon reads as FRESH
    through the real capture path.
    """
    import subprocess

    p = _temp_beacon()
    p.write_text("", encoding="utf-8")

    captured = subprocess.run(
        f'stat -f %Fm "{p}" 2>/dev/null || stat -c %.9Y "{p}" 2>/dev/null || echo 0',
        shell=True,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert captured != "0", "no usable stat flavour found for the beacon capture"
    # The capture must carry sub-second precision, not whole seconds.
    assert "." in captured, f"beacon mtime capture lost precision: {captured!r}"

    b = StalenessBeacon(start_mtime=float(captured))
    b._path = p
    assert b.is_stale() is False, (
        f"fresh beacon read as stale: captured={captured} "
        f"actual={p.stat().st_mtime!r}"
    )
    assert b.state == FRESH


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
        test_hook_captured_mtime_is_not_stale,
        test_latch_is_monotonic,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"staleness_beacon: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
