"""TranscriptReader: stream a JSONL transcript line-by-line."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator


class TranscriptReadError(RuntimeError):
    """Raised when a JSONL line cannot be parsed."""


class TranscriptReader:
    """Decode a JSONL file, yielding one dict per line.

    Blank lines are skipped. Any malformed line raises TranscriptReadError
    with line number context.
    """

    @staticmethod
    def iter_lines(path: Path) -> Iterator[dict]:
        with open(path, "r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                if not raw.strip():
                    continue
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise TranscriptReadError(
                        f"invalid JSON at {path}:{lineno}: {exc}"
                    ) from exc
