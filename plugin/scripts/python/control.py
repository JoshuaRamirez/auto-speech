"""CLI for the pause/resume/seek/restart/end slash commands."""
from __future__ import annotations

import argparse
import sys

from mpv_ipc import MpvIpc, MpvIpcError
from session_dir import SessionDir


EXIT_OK = 0
EXIT_NO_SESSION = 2
EXIT_IPC_FAIL = 3
EXIT_BAD_ARG = 4


def _ensure_session() -> int:
    if not SessionDir.is_mpv_running():
        print("control: no active playback session.", file=sys.stderr)
        return EXIT_NO_SESSION
    return EXIT_OK


def _send(cmd: list) -> int:
    try:
        reply = MpvIpc.send(cmd, SessionDir.socket_path())
    except MpvIpcError as exc:
        print(f"control: IPC failed: {exc}", file=sys.stderr)
        return EXIT_IPC_FAIL
    err = reply.get("error")
    if err and err != "success":
        print(f"control: mpv error: {err}  reply={reply}", file=sys.stderr)
        return EXIT_IPC_FAIL
    return EXIT_OK


def _cmd_pause(_args: argparse.Namespace) -> int:
    rc = _ensure_session()
    if rc != EXIT_OK:
        return rc
    return _send(["set_property", "pause", True])


def _cmd_resume(_args: argparse.Namespace) -> int:
    rc = _ensure_session()
    if rc != EXIT_OK:
        return rc
    return _send(["set_property", "pause", False])


def _cmd_restart(_args: argparse.Namespace) -> int:
    rc = _ensure_session()
    if rc != EXIT_OK:
        return rc
    return _send(["seek", 0, "absolute"])


def _cmd_end(_args: argparse.Namespace) -> int:
    rc = _ensure_session()
    if rc != EXIT_OK:
        return rc
    rc = _send(["quit"])
    SessionDir.clear()
    return rc


def _cmd_seek(args: argparse.Namespace) -> int:
    rc = _ensure_session()
    if rc != EXIT_OK:
        return rc
    target = args.target.strip()
    if not target:
        print("control: seek requires a target (+N, -N, N, or 'end')", file=sys.stderr)
        return EXIT_BAD_ARG
    if target.lower() == "end":
        try:
            reply = MpvIpc.send(
                ["get_property", "duration"], SessionDir.socket_path()
            )
        except MpvIpcError as exc:
            print(f"control: IPC failed: {exc}", file=sys.stderr)
            return EXIT_IPC_FAIL
        duration = reply.get("data")
        if not isinstance(duration, (int, float)):
            print(f"control: mpv did not report duration: {reply}", file=sys.stderr)
            return EXIT_IPC_FAIL
        # Seek to half-a-second before the end so mpv emits a final moment of audio.
        target_seconds = max(0.0, float(duration) - 0.5)
        return _send(["seek", target_seconds, "absolute"])
    if target.startswith("+") or target.startswith("-"):
        try:
            offset = float(target)
        except ValueError:
            print(f"control: seek relative must be a number, got {target!r}", file=sys.stderr)
            return EXIT_BAD_ARG
        return _send(["seek", offset, "relative"])
    try:
        absolute = float(target)
    except ValueError:
        print(
            f"control: seek absolute must be a number or 'end', got {target!r}",
            file=sys.stderr,
        )
        return EXIT_BAD_ARG
    return _send(["seek", absolute, "absolute"])


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
