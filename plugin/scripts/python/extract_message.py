"""CLI: print the Nth-most-recent qualifying assistant message's text to stdout.

Usage: python extract_message.py [--ordinal N] [--cwd PATH]
Default ordinal is 1 (most recent).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from message_selector import MessageSelector, NoSuchAssistantTurn
from transcript_locator import TranscriptLocator, TranscriptNotFoundError

EXIT_OK = 0
EXIT_NO_SUCH_TURN = 2
EXIT_NO_TRANSCRIPT = 3


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Extract the Nth-most-recent assistant message text.")
    p.add_argument("--ordinal", type=int, default=1)
    p.add_argument("--cwd", type=Path, default=None)
    p.add_argument(
        "--transcript-path",
        type=Path,
        default=None,
        help=(
            "Explicit session jsonl. When set, skips TranscriptLocator's "
            "newest-mtime heuristic. The autoplay hook threads this from "
            "the Claude Code Stop payload."
        ),
    )
    p.add_argument(
        "--session-id",
        type=str,
        default=None,
        help=(
            "Session id to look up <session_id>.jsonl in the project slug dir. "
            "Less specific than --transcript-path but more specific than "
            "the locator's newest-mtime fallback. Useful from CLI callers "
            "that have a session id but not a full path."
        ),
    )
    p.add_argument(
        "--exclude-regex",
        type=str,
        default=None,
        help=(
            "Skip assistant messages whose text matches this regex. "
            "Used by /auto-speech-speak to avoid re-targeting its own "
            "prior status echo when invoked repeatedly in the same session."
        ),
    )
    args = p.parse_args(argv)

    if args.transcript_path is not None:
        if not args.transcript_path.exists() or not args.transcript_path.is_file():
            print(
                f"error: --transcript-path {args.transcript_path} does not exist",
                file=sys.stderr,
            )
            return EXIT_NO_TRANSCRIPT
        path = args.transcript_path
    else:
        try:
            path = TranscriptLocator().locate(args.cwd, session_id=args.session_id)
        except TranscriptNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_NO_TRANSCRIPT

    try:
        msg = MessageSelector().select(
            path, args.ordinal, exclude_regex=args.exclude_regex
        )
    except NoSuchAssistantTurn as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NO_SUCH_TURN

    # Print ONLY the text to stdout — the slash command will read it.
    sys.stdout.write(msg.text)
    if not msg.text.endswith("\n"):
        sys.stdout.write("\n")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
