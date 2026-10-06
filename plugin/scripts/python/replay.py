"""CLI entry for /replay: play the most recent (or N-th) cached entry."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

from cache_store import CacheStore
from daemon_client import DaemonClient
from native_audio_sink import AudioSinkError, NativeAudioSink

EXIT_OK = 0
EXIT_NO_CACHE_ENTRY = 2
EXIT_PLAYBACK_FAIL = 6
EXIT_INTERRUPTED = 130

DEFAULT_SOCKET_PATH = Path("/tmp/auto-speech-daemon.sock")
logger = logging.getLogger(__name__)


def _default_cache_root() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "cache"


def _get_socket_path() -> Path:
    return Path(os.environ.get("AUTO_SPEECH_DAEMON_SOCK", str(DEFAULT_SOCKET_PATH)))


def main(
    argv: list[str] | None = None,
    *,
    sink: Any | None = None,
    client: Any | None = None,
) -> int:
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

    # If sink was explicitly injected, respect caller's choice (e.g. testing)
    if sink is not None:
        try:
            sink.play(wav_path)
            return EXIT_OK
        except AudioSinkError as exc:
            print(f"replay: playback failed: {exc}", file=sys.stderr)
            return EXIT_PLAYBACK_FAIL
        except KeyboardInterrupt:
            sink.interrupt()
            return EXIT_INTERRUPTED

    # Steady-state SAO: Route through daemon socket when client injected or daemon alive
    if client is not None:
        try:
            resp = client.play_cache(entry.source_hash)
            if resp.status in ("ok", "queued"):
                return EXIT_OK
        except KeyboardInterrupt:
            return EXIT_INTERRUPTED
        except Exception:  # daemon miss falls through to local playback
            logger.debug("injected client play_cache failed", exc_info=True)
    elif _get_socket_path().is_socket():
        try:
            c = DaemonClient(socket_path=_get_socket_path())
            resp = c.play_cache(entry.source_hash)
            if resp.status in ("ok", "queued"):
                return EXIT_OK
        except KeyboardInterrupt:
            return EXIT_INTERRUPTED
        except Exception:  # daemon miss falls through to local playback
            logger.debug("daemon play_cache failed", exc_info=True)

    # Offline Fallback
    active_sink = NativeAudioSink()
    try:
        active_sink.play(wav_path)
    except AudioSinkError as exc:
        print(f"replay: playback failed: {exc}", file=sys.stderr)
        return EXIT_PLAYBACK_FAIL
    except KeyboardInterrupt:
        active_sink.interrupt()
        return EXIT_INTERRUPTED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
