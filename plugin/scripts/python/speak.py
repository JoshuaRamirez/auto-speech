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
    args = p.parse_args(argv)

    transcript_text = sys.stdin.read()
    orchestrator = PipelineOrchestrator(keep_artifacts=args.keep_artifacts)
    return orchestrator.run(transcript_text=transcript_text, turn_ordinal=args.ordinal)


if __name__ == "__main__":
    sys.exit(main())
