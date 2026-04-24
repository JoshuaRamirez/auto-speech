"""TranscriptLocator: resolve the JSONL path for the current session."""
from __future__ import annotations

import os
import sys
from pathlib import Path


class TranscriptNotFoundError(FileNotFoundError):
    """Raised when no JSONL can be located for the current session."""


class TranscriptLocator:
    """Locate the Claude Code session JSONL for a given cwd.

    Strategy (in order):
      1. If CLAUDE_SESSION_ID env var is set and the file exists, use it.
      2. Otherwise, return the most-recently-modified .jsonl in the slug dir.
      3. Else raise.

    Slug rule: "/Users/joe/foo".replace("/", "-") → "-Users-joe-foo".
    """

    PROJECTS_ROOT = Path.home() / ".claude" / "projects"

    def locate(self, cwd: Path | None = None) -> Path:
        cwd = (cwd or Path.cwd()).resolve()
        slug = str(cwd).replace("/", "-")
        slug_dir = self.PROJECTS_ROOT / slug
        print(f"[locator] slug_dir={slug_dir}", file=sys.stderr)

        if not slug_dir.exists() or not slug_dir.is_dir():
            raise TranscriptNotFoundError(
                f"no project directory at {slug_dir}; run Claude Code in this cwd first"
            )

        sid = os.environ.get("CLAUDE_SESSION_ID")
        if sid:
            candidate = slug_dir / f"{sid}.jsonl"
            if candidate.exists():
                print(f"[locator] using CLAUDE_SESSION_ID file {candidate}", file=sys.stderr)
                return candidate
            print(
                f"[locator] CLAUDE_SESSION_ID={sid} set but {candidate} missing; "
                f"falling back to newest-jsonl",
                file=sys.stderr,
            )

        candidates = sorted(
            slug_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise TranscriptNotFoundError(f"no .jsonl files in {slug_dir}")
        newest = candidates[0]
        print(f"[locator] newest jsonl {newest}", file=sys.stderr)
        return newest
