"""CLI entry for /replay: play the most recent (or N-th) cached entry."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cache_store import CacheStore
from mpv_controller import MpvController, MpvNotInstalledError, MpvStartupError


EXIT_OK = 0
EXIT_NO_CACHE_ENTRY = 2
EXIT_PLAYBACK_FAIL = 6
EXIT_INTERRUPTED = 130


def _default_cache_root() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "cache"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Play the Nth-most-recent cached /speak run (default 1)."
    )
    p.add_argument("--ordinal", type=int, default=1)
    p.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="(accepted for symmetry with speak.py; replay has no tmpdir)",
    )
    args = p.parse_args(argv)
    if args.ordinal < 1:
        print(f"replay: --ordinal must be >= 1, got {args.ordinal}", file=sys.stderr)
        return 2

    store = CacheStore(_default_cache_root())
    entries = store.list_by_recency()
    if not entries:
        print(
            "replay: no cache entries found. Run /speak at least once first.",
            file=sys.stderr,
        )
        return EXIT_NO_CACHE_ENTRY
    if args.ordinal > len(entries):
        print(
            f"replay: only {len(entries)} cache entries; you asked for #{args.ordinal}",
            file=sys.stderr,
        )
        return EXIT_NO_CACHE_ENTRY

    wav_path, entry = entries[args.ordinal - 1]
    print(
        f"[replay] ordinal={args.ordinal}  voice={entry.voice_id}  "
        f"dur={entry.duration_seconds:.1f}s  created={entry.created_at}  "
        f"path={wav_path}",
        file=sys.stderr,
    )

    try:
        MpvController().start(wav_path)
    except (MpvNotInstalledError, MpvStartupError) as exc:
        print(f"replay: mpv start failed: {exc}", file=sys.stderr)
        return EXIT_PLAYBACK_FAIL
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
