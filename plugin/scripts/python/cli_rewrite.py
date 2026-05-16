"""CLI entry: read source text from stdin, print spoken rewrite/summary.

Used by the autoplay worker so the bash side stays simple. Reuses
ClaudeCliRewriter (Phase 14) — same flags, same timeout handling.

The prompt template is chosen by autoplay_config:
  mode=verbatim                  → audio_rewrite_prompt.txt (lossless)
  mode=summary, size=small       → audio_summary_small_prompt.txt
  mode=summary, size=medium      → audio_summary_medium_prompt.txt  (default)
  mode=summary, size=large       → audio_summary_large_prompt.txt

Usage:
    python cli_rewrite.py [--timeout 90] [--mode MODE] [--size SIZE] < source.txt > rewrite.txt
Exit codes:
    0  success
    1  rewriter error or empty output
    2  claude binary not found
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from autoplay_config import VALID_MODES, VALID_SIZES, load_config
from claude_cli_rewriter import (
    ClaudeCliRewriteError,
    ClaudeCliRewriter,
    ClaudeCliUnavailable,
    load_default_template,
)


EXIT_OK = 0
EXIT_REWRITE_FAIL = 1
EXIT_NO_CLAUDE = 2


def _load_template(prompt_path: str) -> str:
    p = Path(prompt_path)
    if p.is_file():
        return p.read_text(encoding="utf-8")
    # Fall back to the legacy verbatim template — at least we still speak.
    print(
        f"cli_rewrite: prompt template missing at {p}; falling back to verbatim",
        file=sys.stderr,
    )
    return load_default_template()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="spoken rewrite/summary via claude -p")
    p.add_argument("--timeout", type=float, default=90.0)
    p.add_argument(
        "--mode",
        choices=VALID_MODES,
        default=None,
        help="override the mode from config (verbatim | summary)",
    )
    p.add_argument(
        "--size",
        choices=VALID_SIZES,
        default=None,
        help="override the summary_size from config (small | medium | large)",
    )
    args = p.parse_args(argv)

    src = sys.stdin.read()
    if not src.strip():
        print("cli_rewrite: empty stdin", file=sys.stderr)
        return EXIT_REWRITE_FAIL

    cfg = load_config()
    if args.mode is not None:
        cfg["mode"] = args.mode
    if args.size is not None:
        cfg["summary_size"] = args.size
    # If either was overridden, re-resolve the prompt path.
    if args.mode is not None or args.size is not None:
        from autoplay_config import _resolve_prompt  # local re-import for resolver
        cfg["prompt_path"] = str(_resolve_prompt(cfg["mode"], cfg["summary_size"]))

    template = _load_template(cfg["prompt_path"])

    print(
        f"cli_rewrite: mode={cfg['mode']} size={cfg['summary_size']} "
        f"prompt={Path(cfg['prompt_path']).name}",
        file=sys.stderr,
    )

    try:
        rewriter = ClaudeCliRewriter(template)
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
