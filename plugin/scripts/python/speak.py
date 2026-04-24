"""CLI entry for auto-speech.

Reads the audio-friendly rewritten text from stdin, runs the pipeline.
The slash command upstream is responsible for:
 - selecting the Nth-most-recent assistant message (via message_selector)
 - rewriting it for audio (via the prompt contract)
 - piping the result to this script on stdin
"""
from __future__ import annotations

import argparse
import sys

from pipeline import PipelineOrchestrator


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Speak an audio-friendly transcript.")
    p.add_argument("--ordinal", type=int, default=1, help="1-indexed N-from-end (logging only)")
    p.add_argument("--keep-artifacts", action="store_true", help="preserve tmpdir on success")
    p.add_argument(
        "--source-hash",
        type=str,
        default=None,
        help=(
            "64-hex-char SHA-256 of (source_text || \\x00 || voice_id:speed). "
            "Enables replay cache: hit plays cached audio; miss-run promotes "
            "full.wav into the cache on success."
        ),
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
    orchestrator = PipelineOrchestrator(
        keep_artifacts=args.keep_artifacts,
        source_hash=args.source_hash,
    )
    return orchestrator.run(transcript_text=transcript_text, turn_ordinal=args.ordinal)


if __name__ == "__main__":
    sys.exit(main())
