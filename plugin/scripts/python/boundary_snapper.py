"""BoundarySnapper: snap a target offset to a natural text boundary.

Priority: paragraph > sentence > clause > word > hard fallback.
"""
from __future__ import annotations

import re

_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
_CLAUSE_END = re.compile(r"[,;:](?=\s|$)")
_WHITESPACE = re.compile(r"\s")


class BoundarySnapper:
    """Return the exclusive end-offset that best approximates `target`."""

    @staticmethod
    def snap(
        text: str,
        start: int,
        target_len: int,
        tolerance: float = 0.25,
    ) -> int:
        """Return an end offset in `text` (exclusive) relative to the full string.

        Searches window [start + 1, start + target_len * (1 + tolerance)],
        clamped to len(text). Priority: paragraph > sentence > clause > word.
        If no boundary is found, falls back to the last whitespace <= target,
        scanning backward from target within the window. If none even there,
        returns start + target_len (hard cut, last-resort; the calling
        planner treats this as an acceptable final measure since it only
        occurs for very long unbroken substrings — vanishingly rare in
        audio-friendly prose).

        For the final chunk (when start + target_len >= len(text)),
        returns len(text).
        """
        if not text:
            return 0
        remaining = len(text) - start
        if remaining <= 0:
            return len(text)
        if target_len >= remaining:
            return len(text)

        window_max = int(target_len * (1 + tolerance))
        window_end = min(start + window_max, len(text))
        # Search the window sub-string.
        window = text[start:window_end]

        # Priority 1: paragraph break
        match = None
        for m in _PARAGRAPH_BREAK.finditer(window):
            match = m  # keep last
        if match is not None:
            # End at the first char after the paragraph-break match.
            return start + match.end()

        # Priority 2: sentence terminator (pick the LAST one in the window).
        last = None
        for m in _SENTENCE_END.finditer(window):
            last = m
        if last is not None:
            return start + last.end()

        # Priority 3: clause terminator (last in window).
        last = None
        for m in _CLAUSE_END.finditer(window):
            last = m
        if last is not None:
            return start + last.end()

        # Priority 4: word break (last whitespace in window).
        last = None
        for m in _WHITESPACE.finditer(window):
            last = m
        if last is not None:
            return start + last.end()

        # Hard fallback — no boundary of any kind in the window.
        # Scan backward from window_end to find ANY whitespace in
        # [start, window_end]. If still nothing, give up and hard-cut.
        scan_from = min(start + target_len, len(text) - 1)
        for i in range(scan_from, start, -1):
            if text[i].isspace():
                return i + 1
        return start + target_len
