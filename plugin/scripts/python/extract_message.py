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
    args = p.parse_args(argv)

    try:
        path = TranscriptLocator().locate(args.cwd)
    except TranscriptNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NO_TRANSCRIPT

    try:
        msg = MessageSelector().select(path, args.ordinal)
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
