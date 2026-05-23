"""TranscriptLocator: resolve the JSONL path for the current session."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path


class TranscriptNotFoundError(FileNotFoundError):
    """Raised when no JSONL can be located for the current session."""


def _slug_candidates(cwd: Path) -> list[str]:
    """Generate slug candidates in best-to-worst order.

    Claude Code's slug derivation isn't a documented API but observed
    behaviour normalizes more than just "/". For a cwd like
    "/Users/me/foo.bar" the dir under ~/.claude/projects is
    "-Users-me-foo-bar" — the "." became "-". Slashes, dots, and likely
    any other non-[A-Za-z0-9] character get mapped to "-".

    We yield candidates from most to least conservative so the strict
    "only-slashes" form still wins where it matches (avoiding false
    hits if a future Claude Code version uses different normalisation).
    """
    raw = str(cwd)
    # 1. Strict: only "/" → "-" (the original heuristic, still right
    #    for paths with no dots or other special chars).
    yield raw.replace("/", "-")
    # 2. Dots + slashes → "-".
    yield re.sub(r"[/.]", "-", raw)
    # 3. All non-alphanumeric (except "-" itself) → "-". Catches spaces,
    #    underscores, +, etc. — only triggers if the first two miss.
    yield re.sub(r"[^A-Za-z0-9-]", "-", raw)


class TranscriptLocator:
    """Locate the Claude Code session JSONL for a given cwd.

    Strategy (in order):
      1. If CLAUDE_SESSION_ID env var is set and the file exists, use it.
      2. Otherwise, return the most-recently-modified .jsonl in the slug dir.
      3. Else raise.

    Slug rule: Claude Code maps "/" and "." (and likely other special
    chars) in the cwd to "-". For example "/Users/me/foo.bar" lives
    under ~/.claude/projects/-Users-me-foo-bar. We try several
    normalisations from strictest to loosest.
    """

    PROJECTS_ROOT = Path.home() / ".claude" / "projects"

    def locate(self, cwd: Path | None = None, session_id: str | None = None) -> Path:
        cwd = (cwd or Path.cwd()).resolve()

        # Try slug candidates from strictest to loosest. dedupe so we
        # don't log identical attempts twice.
        seen: set[str] = set()
        slug_dir: Path | None = None
        tried: list[Path] = []
        for slug in _slug_candidates(cwd):
            if slug in seen:
                continue
            seen.add(slug)
            candidate = self.PROJECTS_ROOT / slug
            tried.append(candidate)
            if candidate.exists() and candidate.is_dir():
                slug_dir = candidate
                print(f"[locator] slug_dir={slug_dir}", file=sys.stderr)
                break

        if slug_dir is None:
            tried_str = ", ".join(str(p) for p in tried)
            raise TranscriptNotFoundError(
                f"no project directory matched cwd {cwd}; tried: {tried_str}. "
                f"Run Claude Code in this cwd first."
            )

        # Precedence: explicit session_id arg → CLAUDE_SESSION_ID env →
        # CLAUDE_CODE_SESSION_ID env (newer Claude Code) → newest-mtime
        # fallback. The arg form lets callers pass the session_id from
        # the hook payload directly (more reliable than env propagation,
        # which broke between Claude Code versions — see commit 03301c8).
        sid = session_id or os.environ.get("CLAUDE_SESSION_ID") or os.environ.get(
            "CLAUDE_CODE_SESSION_ID"
        )
        if sid:
            candidate = slug_dir / f"{sid}.jsonl"
            if candidate.exists():
                print(f"[locator] using session_id file {candidate}", file=sys.stderr)
                return candidate
            print(
                f"[locator] session_id={sid} but {candidate} missing; "
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
