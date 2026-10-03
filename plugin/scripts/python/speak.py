"""CLI entry for auto-speech (Thin Client).

Reads audio-friendly text from stdin and forwards it to the auto-speech daemon
via UNIX domain socket (/tmp/auto-speech-daemon.sock).
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

DEFAULT_SOCKET_PATH = Path("/tmp/auto-speech-daemon.sock")


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

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(str(socket_path))
        sock.sendall(text.encode("utf-8"))
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
    except (FileNotFoundError, ConnectionRefusedError):
        print(f"Error: cannot connect to auto-speech daemon at {socket_path}", file=sys.stderr)
        return 1
    except (socket.timeout, OSError) as exc:
        print(
            f"Error: cannot connect to auto-speech daemon at {socket_path}: {exc}", file=sys.stderr
        )
        return 1
    finally:
        sock.close()

    return 0


def main(argv: list[str] | None = None) -> int:
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
        return 0

    if args.socket_path is not None:
        return send_speech_request(transcript_text, socket_path=args.socket_path)
    return send_speech_request(transcript_text)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
