"""SpanSplitter: the next-finer decomposition of a text span.

Used by ResilientSynthesizer to retry a span that tripped a generation
fault. The progression is deliberately coarse-to-fine so a recovered
span keeps the largest prosodic unit that still synthesizes:

    sentences → clauses → word-halves → (single word: the floor)

Stateless and referentially transparent: the same input always yields
the same decomposition, and nothing outside the returned list changes.
"""
from __future__ import annotations

import re

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_RE = re.compile(r"\s*[,;:—–]\s*")


class SpanSplitter:
    """Splits a span one level finer for a synthesis retry."""

    def split(self, text: str) -> list[str]:
        """Return the next-finer split of `text`.

        Sentences if there is more than one; else clauses; else the word
        list halved; else `[text]` unchanged — a single word, the floor of
        the recursion, which callers use as the signal to stop retrying.
        """
        text = text.strip()
        for rx in (_SENTENCE_RE, _CLAUSE_RE):
            parts = [p.strip() for p in rx.split(text) if p.strip()]
            if len(parts) > 1:
                return parts
        words = text.split()
        if len(words) > 1:
            mid = len(words) // 2
            return [" ".join(words[:mid]), " ".join(words[mid:])]
        return [text]
