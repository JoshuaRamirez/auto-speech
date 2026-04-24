"""AssistantMessage: a single assistant turn's text content."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AssistantMessage:
    """One assistant turn that contains at least one non-empty text block.

    `text` is the concatenation of every text block in the turn's content,
    joined by newline. `ordinal_from_end` is 1-indexed from the most recent
    qualifying turn (1 = most recent, 2 = next-most-recent, ...). Pure
    tool-use turns are not counted toward the ordinal.
    """

    turn_index: int
    ordinal_from_end: int
    timestamp: str
    text: str
