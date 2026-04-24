"""PlaybackQueue: thin wrapper over queue.Queue adding a SENTINEL close()."""
from __future__ import annotations

import queue
from typing import Any


SENTINEL: Any = object()


class PlaybackQueue:
    """Bounded FIFO of AudioSegments + a sentinel to signal end-of-plan."""

    def __init__(self, capacity: int = 3) -> None:
        self._q: queue.Queue = queue.Queue(maxsize=capacity)

    def put(self, segment: Any) -> None:
        self._q.put(segment)

    def get(self, timeout: float | None = None) -> Any:
        return self._q.get(timeout=timeout)

    def close(self) -> None:
        """Idempotent: safe to call multiple times."""
        self._q.put(SENTINEL)
