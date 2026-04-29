"""CLI entry: read source text from stdin, print audio-friendly rewrite.

Used by the autoplay worker so the bash side stays simple. Reuses
ClaudeCliRewriter (Phase 14) — same prompt, same flags, same timeout
handling.

Usage:
    python cli_rewrite.py [--timeout 90] < source.txt > rewrite.txt
Exit codes:
    0  success
    1  rewriter error or empty output
    2  claude binary not found
"""
from __future__ import annotations

import argparse
import sys

from claude_cli_rewriter import (
    ClaudeCliRewriteError,
    ClaudeCliRewriter,
    ClaudeCliUnavailable,
    load_default_template,
)


EXIT_OK = 0
EXIT_REWRITE_FAIL = 1
EXIT_NO_CLAUDE = 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="audio-friendly rewrite via claude -p")
    p.add_argument("--timeout", type=float, default=90.0)
    args = p.parse_args(argv)

    src = sys.stdin.read()
    if not src.strip():
        print("cli_rewrite: empty stdin", file=sys.stderr)
        return EXIT_REWRITE_FAIL

    try:
        rewriter = ClaudeCliRewriter(load_default_template())
        out = rewriter.rewrite(src, timeout_seconds=args.timeout)
    except ClaudeCliUnavailable as exc:
        print(f"cli_rewrite: {exc}", file=sys.stderr)
        return EXIT_NO_CLAUDE
    except ClaudeCliRewriteError as exc:
        print(f"cli_rewrite: {exc}", file=sys.stderr)
        return EXIT_REWRITE_FAIL

    sys.stdout.write(out)
    if not out.endswith("\n"):
        sys.stdout.write("\n")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
