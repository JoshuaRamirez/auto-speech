"""CLI for the pause/resume/seek/restart/end slash commands."""

from __future__ import annotations

import argparse
import sys

EXIT_OK = 0
EXIT_NO_SESSION = 2
EXIT_IPC_FAIL = 3
EXIT_BAD_ARG = 4


def _ensure_session() -> int:
    print(
        "control: no active playback session (unified daemon uses synchronous audio).",
        file=sys.stderr,
    )
    return EXIT_NO_SESSION


def _cmd_pause(_args: argparse.Namespace) -> int:
    return _ensure_session()


def _cmd_resume(_args: argparse.Namespace) -> int:
    return _ensure_session()


def _cmd_restart(_args: argparse.Namespace) -> int:
    return _ensure_session()


def _cmd_end(_args: argparse.Namespace) -> int:
    return _ensure_session()


def _cmd_seek(args: argparse.Namespace) -> int:
    rc = _ensure_session()
    if rc != EXIT_OK:
        return rc
    target = args.target.strip()
    if not target:
        print("control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
        return EXIT_BAD_ARG
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Send a control command to the active mpv playback.")
    sub = p.add_subparsers(dest="subcommand", required=True)
    sub.add_parser("pause").set_defaults(func=_cmd_pause)
    sub.add_parser("resume").set_defaults(func=_cmd_resume)
    sub.add_parser("restart").set_defaults(func=_cmd_restart)
    sub.add_parser("end").set_defaults(func=_cmd_end)
    seek = sub.add_parser("seek")
    seek.add_argument("target", type=str, help="+N, -N, N (absolute seconds), or 'end'")
    seek.set_defaults(func=_cmd_seek)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
