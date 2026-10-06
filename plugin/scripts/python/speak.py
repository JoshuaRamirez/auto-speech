"""CLI entry for auto-speech (Thin Client).

Reads audio-friendly text from stdin and forwards it to the auto-speech daemon
via UNIX domain socket (/tmp/auto-speech-daemon.sock).
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any

from daemon_client import DEFAULT_SOCKET_PATH, DaemonClient, DaemonResponse, Priority  # noqa: F401

logger = logging.getLogger(__name__)


def get_socket_path() -> Path:
    """Resolve UNIX domain socket path with environment variable override."""
    return Path(os.environ.get("AUTO_SPEECH_DAEMON_SOCK", str(DEFAULT_SOCKET_PATH)))


def send_speech_request(
    text: str,
    socket_path: Path | str | None = None,
    timeout: float = 5.0,
) -> int:
    """Transmit text payload to the daemon socket.

    Returns:
        0 on success or if input is empty/whitespace-only.
        1 on socket connection/communication failure.
    """
    if not text.strip():
        return 0

    if socket_path is None:
        socket_path = get_socket_path()

    if not Path(socket_path).is_socket():
        start_script = Path(__file__).resolve().parents[1] / "shell" / "narrator_service_start.sh"
        if start_script.is_file():
            try:
                import subprocess

                subprocess.run(
                    ["bash", str(start_script)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                for _ in range(25):
                    if Path(socket_path).is_socket():
                        break
                    time.sleep(0.04)
            except Exception:  # daemon autostart is best-effort
                logger.debug("daemon autostart failed", exc_info=True)

    max_attempts = 5
    retry_delay = 0.02

    for attempt in range(max_attempts):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            sock.connect(str(socket_path))
            sock.sendall(text.encode("utf-8"))
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            return 0
        except FileNotFoundError:
            print(f"Error: cannot connect to auto-speech daemon at {socket_path}", file=sys.stderr)
            return 1
        except (ConnectionRefusedError, ConnectionResetError):
            if attempt < max_attempts - 1:
                time.sleep(retry_delay * (attempt + 1))
                continue
            print(f"Error: cannot connect to auto-speech daemon at {socket_path}", file=sys.stderr)
            return 1
        except (TimeoutError, OSError) as exc:
            print(
                f"Error: cannot connect to auto-speech daemon at {socket_path}: {exc}", file=sys.stderr
            )
            return 1
        finally:
            sock.close()

    return 1


def main(
    argv: list[str] | None = None,
    *,
    client: DaemonClient | None = None,
    sink: Any | None = None,
) -> int:
    p = argparse.ArgumentParser(description="Speak an audio-friendly transcript via daemon.")
    p.add_argument("--ordinal", type=int, default=1, help="1-indexed N-from-end (logging only)")
    p.add_argument(
        "--keep-artifacts", action="store_true", help="preserve tmpdir (compatibility flag)"
    )
    p.add_argument(
        "--source-hash",
        type=str,
        default=None,
        help="64-hex-char SHA-256 hash (compatibility flag)",
    )
    p.add_argument(
        "--socket-path",
        type=str,
        default=None,
        help="override UNIX domain socket path to auto-speech daemon",
    )
    args = p.parse_args(argv)

    if args.source_hash is not None:
        sh = args.source_hash.strip().lower()
        if len(sh) != 64 or any(c not in "0123456789abcdef" for c in sh):
            print(
                f"speak: --source-hash must be 64 hex chars, got {args.source_hash!r}",
                file=sys.stderr,
            )
            return 2
        args.source_hash = sh

    transcript_text = sys.stdin.read()
    if not transcript_text.strip():
        if args.source_hash:
            if client is not None:
                try:
                    resp = client.play_cache(args.source_hash)
                    if resp.status in ("ok", "queued"):
                        return 0
                except Exception:  # cache play falls through to the wav file
                    logger.debug("injected client play_cache failed", exc_info=True)
            elif sink is None:
                try:
                    c = DaemonClient(socket_path=args.socket_path)
                    if c.is_alive():
                        resp = c.play_cache(args.source_hash)
                        if resp.status in ("ok", "queued"):
                            return 0
                except Exception:  # cache play falls through to the wav file
                    logger.debug("daemon play_cache failed", exc_info=True)

            cache_root = Path(__file__).resolve().parents[3] / "config" / "cache"
            cache_wav = cache_root / args.source_hash[:16] / "full.wav"
            if cache_wav.is_file():
                if sink is not None:
                    active_sink = sink
                else:
                    from native_audio_sink import NativeAudioSink

                    active_sink = NativeAudioSink()

                try:
                    active_sink.play(cache_wav)
                    return 0
                except Exception as exc:  # noqa: BLE001 — playback failure is reported, not raised
                    print(f"speak: cached playback failed: {exc}", file=sys.stderr)
                    return 1
        return 0

    if args.socket_path is not None:
        return send_speech_request(transcript_text, socket_path=args.socket_path)
    return send_speech_request(transcript_text)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
