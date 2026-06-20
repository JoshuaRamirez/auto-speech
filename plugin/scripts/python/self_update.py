"""Self-update decision: is the venv stale with respect to the committed lock?

Pure standard library so it runs under the SYSTEM python — before any venv
exists — which is what lets the bootstrap decide whether to `uv sync` on a
fresh machine. The decision is a content-hash comparison: hash uv.lock and
compare it to a stamp written after the last successful sync. A sync then
runs exactly when dependencies changed, not on every session.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path


def lock_hash(lock_path: Path) -> str:
    return hashlib.sha256(Path(lock_path).read_bytes()).hexdigest()


def recorded_hash(stamp_path: Path) -> str | None:
    try:
        return Path(stamp_path).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def needs_sync(lock_path: Path, stamp_path: Path) -> bool:
    """True when the lock has no matching stamp — i.e. never synced, or the
    lock changed since the last successful sync. A missing lock is treated as
    'nothing to sync' (False) rather than an error."""
    try:
        current = lock_hash(lock_path)
    except OSError:
        return False
    return recorded_hash(stamp_path) != current


def record_sync(lock_path: Path, stamp_path: Path) -> None:
    """Stamp the current lock hash after a successful sync."""
    Path(stamp_path).write_text(lock_hash(lock_path), encoding="utf-8")


def main(argv: list[str]) -> int:
    # CLI for the shell bootstrap:
    #   check  <lock> <stamp>  → exit 0 if a sync is needed, 1 if up to date
    #   record <lock> <stamp>  → write the current lock hash to the stamp
    if len(argv) == 4 and argv[1] == "check":
        return 0 if needs_sync(Path(argv[2]), Path(argv[3])) else 1
    if len(argv) == 4 and argv[1] == "record":
        record_sync(Path(argv[2]), Path(argv[3]))
        return 0
    print("usage: self_update.py {check|record} <lock> <stamp>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
