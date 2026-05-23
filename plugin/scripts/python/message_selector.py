"""MessageSelector: pick the Nth-most-recent qualifying AssistantMessage."""
from __future__ import annotations

import re
from collections import deque
from pathlib import Path

from assistant_message import AssistantMessage
from transcript_reader import TranscriptReader


class NoSuchAssistantTurn(LookupError):
    """Raised when fewer than N qualifying assistant turns exist."""


class MessageSelector:
    """Return the N-th most recent qualifying assistant turn.

    A "qualifying" assistant turn is one whose message.content has at
    least one block of type 'text' with non-empty text (post strip)
    AND whose combined text does not match exclude_regex (when given).

    The exclude_regex is how /auto-speech-speak avoids picking up its
    own prior status echo ("spoke message #1 …") as the message to
    re-speak. Without it, every /speak after the first one would
    target the previous /speak's status line, not the original
    assistant response.
    """

    def select(
        self,
        path: Path,
        n: int,
        exclude_regex: str | None = None,
    ) -> AssistantMessage:
        if n < 1:
            raise ValueError(f"n must be >= 1, got {n}")

        excl = None
        if exclude_regex:
            excl = re.compile(exclude_regex, re.MULTILINE)

        # Ring buffer: we only ever need the last n QUALIFYING turns in memory.
        buf: deque[tuple[int, str, str]] = deque(maxlen=n)

        total_turns = 0
        for turn_index, record in enumerate(TranscriptReader.iter_lines(path)):
            total_turns += 1
            if record.get("type") != "assistant":
                continue
            msg = record.get("message") or {}
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content") or []
            texts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict)
                and block.get("type") == "text"
                and (block.get("text") or "").strip()
            ]
            if not texts:
                continue
            full_text = "\n".join(texts).strip()
            if excl is not None and excl.search(full_text):
                # Treat as if not a qualifying turn so ordinal counting
                # skips past it.
                continue
            ts = record.get("timestamp") or ""
            buf.append((turn_index, ts, full_text))

        if len(buf) < n:
            raise NoSuchAssistantTurn(
                f"only {len(buf)} qualifying assistant messages available; "
                f"you asked for #{n}"
            )

        # The ring buffer holds the LAST n items in chronological order.
        # Index 0 = oldest in the window = Nth-most-recent.
        selected_turn_index, ts, text = buf[0]
        return AssistantMessage(
            turn_index=selected_turn_index,
            ordinal_from_end=n,
            timestamp=ts,
            text=text,
        )
