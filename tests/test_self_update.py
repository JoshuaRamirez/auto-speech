"""Unit tests for self_update (the sync-decision engine).

Covers hash determinism, needs_sync across the four cases (no stamp, match,
mismatch, missing lock), record_sync round-trip, and the CLI exit codes the
bootstrap shell relies on. Hermetic: temp files only.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

import self_update as su


def _tmp(content: bytes = b"lock-v1") -> Path:
    d = Path(tempfile.mkdtemp(prefix="auto-speech-su-"))
    lock = d / "uv.lock"
    lock.write_bytes(content)
    return d


def test_lock_hash_deterministic_and_content_sensitive() -> None:
    d = _tmp(b"abc")
    h1 = su.lock_hash(d / "uv.lock")
    h2 = su.lock_hash(d / "uv.lock")
    assert h1 == h2
    (d / "uv.lock").write_bytes(b"abcd")
    assert su.lock_hash(d / "uv.lock") != h1


def test_needs_sync_when_no_stamp() -> None:
    d = _tmp()
    assert su.needs_sync(d / "uv.lock", d / ".synced") is True


def test_needs_sync_false_after_record() -> None:
    d = _tmp()
    su.record_sync(d / "uv.lock", d / ".synced")
    assert su.needs_sync(d / "uv.lock", d / ".synced") is False


def test_needs_sync_true_after_lock_changes() -> None:
    d = _tmp()
    su.record_sync(d / "uv.lock", d / ".synced")
    (d / "uv.lock").write_bytes(b"lock-v2")
    assert su.needs_sync(d / "uv.lock", d / ".synced") is True


def test_needs_sync_false_when_lock_missing() -> None:
    d = _tmp()
    (d / "uv.lock").unlink()
    assert su.needs_sync(d / "uv.lock", d / ".synced") is False


def test_cli_check_exit_codes() -> None:
    d = _tmp()
    lock, stamp = str(d / "uv.lock"), str(d / ".synced")
    assert su.main(["x", "check", lock, stamp]) == 0  # no stamp → needs sync
    su.record_sync(d / "uv.lock", d / ".synced")
    assert su.main(["x", "check", lock, stamp]) == 1  # up to date


def test_cli_record_writes_stamp() -> None:
    d = _tmp()
    rc = su.main(["x", "record", str(d / "uv.lock"), str(d / ".synced")])
    assert rc == 0
    assert su.recorded_hash(d / ".synced") == su.lock_hash(d / "uv.lock")


def test_cli_usage_error() -> None:
    assert su.main(["x"]) == 2


def main() -> int:
    tests = [
        test_lock_hash_deterministic_and_content_sensitive,
        test_needs_sync_when_no_stamp,
        test_needs_sync_false_after_record,
        test_needs_sync_true_after_lock_changes,
        test_needs_sync_false_when_lock_missing,
        test_cli_check_exit_codes,
        test_cli_record_writes_stamp,
        test_cli_usage_error,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"self_update: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
