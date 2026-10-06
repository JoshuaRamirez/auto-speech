"""In-memory 4-tier Priority Arbiter and QueueProxyFacade for AutoSpeech daemon.

Provides:
  - Priority: 4-tier hierarchy (P1 User Interrupt > P2 Explicit MCP > P3 Autoplay > P4 Tool Narration).
  - QueueItem: Typed representation of queued speech requests.
  - PriorityArbiter: Thread-safe multi-band priority queue with condition variables,
    preemption re-queuing, and P1 user barge-in purging.
  - QueueProxyFacade: Duck-typed drop-in facade implementing all 9 public methods/attributes
    of standard Python queue.Queue for 100% backward compatibility.
"""

from __future__ import annotations

import collections
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from config_constants import DEFAULT_SPEED, DEFAULT_VOICE_ID

logger = logging.getLogger(__name__)


class Priority(IntEnum):
    USER_INTERRUPT = 1
    EXPLICIT_MCP = 2
    AUTOPLAY = 3
    TOOL_NARRATION = 4


@dataclass
class QueueItem:
    priority: int = Priority.TOOL_NARRATION
    payload: Any = None
    source_hash: str | None = None
    session_id: str | None = None
    voice_id: str = DEFAULT_VOICE_ID
    speed: float = DEFAULT_SPEED
    timestamp: float = field(default_factory=time.time)
    enqueued_at: float = field(default_factory=time.time)
    request_id: str = ""

    def __post_init__(self) -> None:
        if self.enqueued_at is None:
            self.enqueued_at = self.timestamp
        elif self.timestamp is None:
            self.timestamp = self.enqueued_at


class PriorityArbiter:
    """Thread-safe 4-tier priority arbiter managing speech execution bands."""

    def __init__(self, maxsize: int = 32) -> None:
        self.maxsize = int(maxsize)
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._p1: collections.deque[QueueItem] = collections.deque()
        self._p2: collections.deque[QueueItem] = collections.deque()
        self._p3: collections.deque[QueueItem] = collections.deque()
        self._p4: collections.deque[QueueItem] = collections.deque()
        self._dropped_count = 0
        self._active_item: QueueItem | None = None
        self._active_priority: int | None = None
        self._purge_callbacks: list[Callable[[int], None]] = []

    def register_purge_callback(self, cb: Callable[[int], None]) -> None:
        """Register callback invoked whenever items are pruned or purged."""
        with self._lock:
            self._purge_callbacks.append(cb)

    def _notify_purged(self, count: int) -> None:
        for cb in self._purge_callbacks:
            try:
                cb(count)
            except Exception:  # one callback must not break purge
                logger.debug("purge callback failed", exc_info=True)

    @property
    def active_priority(self) -> int | None:
        with self._lock:
            return self._active_priority

    @property
    def active_item(self) -> QueueItem | None:
        with self._lock:
            return self._active_item

    @property
    def dropped_count(self) -> int:
        with self._lock:
            return self._dropped_count

    def enqueue(
        self,
        item: Any,
        priority: Priority | int | None = None,
        source_hash: str | None = None,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> QueueItem:
        """Enqueues an item into the appropriate priority band."""
        if isinstance(item, QueueItem):
            q_item = item
            if priority is not None:
                q_item.priority = int(priority)
        else:
            p = int(priority if priority is not None else Priority.TOOL_NARRATION)
            q_item = QueueItem(
                priority=p,
                payload=item,
                source_hash=source_hash,
                session_id=session_id,
                **kwargs,
            )

        dropped_one = False
        with self._lock:
            p = q_item.priority
            if p == Priority.USER_INTERRUPT:
                self._p1.append(q_item)
            elif p == Priority.EXPLICIT_MCP:
                self._p2.append(q_item)
            elif p == Priority.AUTOPLAY:
                self._p3.append(q_item)
            else:
                # P4: Tool Narration with drop-oldest shedding if maxsize exceeded
                if self.maxsize > 0 and len(self._p4) >= self.maxsize:
                    shed = self._shed_oldest_low_priority_locked()
                    if shed is not None:
                        dropped_one = True
                self._p4.append(q_item)

            self._not_empty.notify()

        if dropped_one:
            self._notify_purged(1)

        return q_item

    def _shed_oldest_low_priority_locked(self) -> QueueItem | None:
        """Sheds oldest item from lowest non-empty band (P4 first, then P3, never P1/P2).

        Must be called while holding self._lock. Returns None if neither P4 nor P3 has items.
        """
        if self._p4:
            item = self._p4.popleft()
            self._dropped_count += 1
            return item
        if self._p3:
            item = self._p3.popleft()
            self._dropped_count += 1
            return item
        return None

    def shed_oldest_low_priority(self) -> QueueItem | None:
        """Sheds oldest item from lowest non-empty priority band (P4 first, then P3).

        Never sheds from P1 (User Interrupt) or P2 (Explicit MCP).
        Returns the dropped QueueItem, or None if no lower-priority items are available.
        Automatically notifies registered purge callbacks.
        """
        with self._lock:
            item = self._shed_oldest_low_priority_locked()

        if item is not None:
            self._notify_purged(1)
        return item

    def shed_oldest_narration(self) -> QueueItem | None:
        """Alias for shed_oldest_low_priority."""
        return self.shed_oldest_low_priority()

    def requeue_at_head(
        self,
        item: Any,
        priority: Priority | int | None = None,
    ) -> None:
        """Inserts an item at the head of its priority band (used on preemption)."""
        if isinstance(item, QueueItem):
            q_item = item
            p = int(priority if priority is not None else q_item.priority)
        else:
            p = int(priority if priority is not None else Priority.TOOL_NARRATION)
            q_item = QueueItem(priority=p, payload=item)

        with self._lock:
            if p == Priority.USER_INTERRUPT:
                self._p1.appendleft(q_item)
            elif p == Priority.EXPLICIT_MCP:
                self._p2.appendleft(q_item)
            elif p == Priority.AUTOPLAY:
                self._p3.appendleft(q_item)
            else:
                self._p4.appendleft(q_item)

            self._not_empty.notify()

    def dequeue(
        self,
        block: bool = True,
        timeout: float | None = None,
        unwrap: bool = True,
    ) -> Any:
        """Dequeues the highest-priority item across bands (P1 > P2 > P3 > P4)."""
        with self._lock:
            if not block:
                if self._total_depth_locked() == 0:
                    raise queue.Empty
                q_item = self._pop_highest_locked()
            elif timeout is None:
                while self._total_depth_locked() == 0:
                    self._not_empty.wait()
                q_item = self._pop_highest_locked()
            else:
                deadline = time.time() + timeout
                while self._total_depth_locked() == 0:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        raise queue.Empty
                    self._not_empty.wait(timeout=remaining)
                q_item = self._pop_highest_locked()

            self._active_item = q_item
            self._active_priority = q_item.priority
            return q_item.payload if (unwrap and isinstance(q_item, QueueItem)) else q_item

    def purge_lower_queues(self) -> int:
        """Purges P3 (Autoplay) and P4 (Tool Narration) queues strictly for P1 barge-in."""
        with self._lock:
            count = len(self._p3) + len(self._p4)
            self._dropped_count += count
            self._p3.clear()
            self._p4.clear()

        if count > 0:
            self._notify_purged(count)

        return count

    def purge_lower_tiers(self) -> int:
        """Alias for purge_lower_queues."""
        return self.purge_lower_queues()

    def clear(self) -> None:
        """Clears all priority bands."""
        with self._lock:
            total = self._total_depth_locked()
            self._p1.clear()
            self._p2.clear()
            self._p3.clear()
            self._p4.clear()
            self._active_item = None
            self._active_priority = None

        if total > 0:
            self._notify_purged(total)

    def total_depth(self) -> int:
        with self._lock:
            return self._total_depth_locked()

    def depths(self) -> dict[str, int]:
        with self._lock:
            return {
                "p1_interrupt": len(self._p1),
                "p2_mcp": len(self._p2),
                "p3_autoplay": len(self._p3),
                "p4_narration": len(self._p4),
            }

    def depth_by_priority(self) -> dict[str, int]:
        """Alias for depths()."""
        return self.depths()

    @property
    def queue(self) -> collections.deque:
        """Underlying deque exposing queued items for queue.Queue compatibility."""
        items: collections.deque = collections.deque()
        with self._lock:
            for q in (self._p1, self._p2, self._p3, self._p4):
                for item in q:
                    items.append(item.payload if isinstance(item, QueueItem) else item)
        return items

    @property
    def mutex(self) -> threading.Lock:
        return self._lock

    def _total_depth_locked(self) -> int:
        return len(self._p1) + len(self._p2) + len(self._p3) + len(self._p4)

    def _pop_highest_locked(self) -> QueueItem:
        if self._p1:
            return self._p1.popleft()
        if self._p2:
            return self._p2.popleft()
        if self._p3:
            return self._p3.popleft()
        if self._p4:
            return self._p4.popleft()
        raise queue.Empty


class QueueProxyFacade:
    """100% queue.Queue compatible proxy facade bridging to PriorityArbiter."""

    def __init__(self, arbiter: PriorityArbiter, maxsize: int = 32) -> None:
        self._arbiter = arbiter
        self._maxsize = int(maxsize)
        self._unfinished_tasks = 0
        self._cond = threading.Condition()
        self._arbiter.register_purge_callback(self._on_purged)

    def _on_purged(self, count: int) -> None:
        with self._cond:
            if self._unfinished_tasks > 0:
                self._unfinished_tasks = max(0, self._unfinished_tasks - count)
            self._cond.notify_all()

    @property
    def queue(self) -> collections.deque:
        """Underlying deque exposing queued items for duck-typed queue.Queue compatibility."""
        return self._arbiter.queue

    @property
    def mutex(self) -> threading.Lock:
        return self._arbiter.mutex

    @property
    def maxsize(self) -> int:
        return self._maxsize

    @maxsize.setter
    def maxsize(self, val: int) -> None:
        self._maxsize = int(val)
        self._arbiter.maxsize = int(val)

    def put(self, item: Any, block: bool = True, timeout: float | None = None) -> None:
        """Enqueues item into PriorityArbiter. Raises queue.Full if full and non-blocking/timed-out."""
        if isinstance(item, QueueItem):
            q_item = item
        else:
            q_item = QueueItem(priority=Priority.TOOL_NARRATION, payload=item)

        # RFC §4.4: P1, P2, P3 are unbounded priority bands; only P4 is capped by maxsize
        is_low_priority = q_item.priority >= Priority.TOOL_NARRATION

        with self._cond:
            if is_low_priority and self._maxsize > 0:
                if not block:
                    if self._arbiter.total_depth() >= self._maxsize:
                        raise queue.Full
                else:
                    end_time = time.time() + timeout if timeout is not None else None
                    while self._arbiter.total_depth() >= self._maxsize:
                        if timeout is not None:
                            remaining = end_time - time.time()
                            if remaining <= 0:
                                raise queue.Full
                            self._cond.wait(remaining)
                        else:
                            self._cond.wait()
            self._unfinished_tasks += 1

        self._arbiter.enqueue(q_item)

    def shed_oldest_low_priority(self) -> Any:
        """Sheds oldest item from lowest non-empty band (P4 first, then P3, never P1/P2).

        Returns the unwrapped item payload, or None if no droppable item exists.
        Task counters and condition variables are automatically updated via purge callbacks.
        """
        item = self._arbiter.shed_oldest_low_priority()
        if item is None:
            return None
        return item.payload if isinstance(item, QueueItem) else item

    def put_nowait(self, item: Any) -> None:
        self.put(item, block=False)

    def get(self, block: bool = True, timeout: float | None = None) -> Any:
        """Dequeues ready item from highest priority non-empty band."""
        item = self._arbiter.dequeue(block=block, timeout=timeout, unwrap=True)
        with self._cond:
            self._cond.notify_all()
        return item

    def get_nowait(self) -> Any:
        return self.get(block=False)

    def qsize(self) -> int:
        return self._arbiter.total_depth()

    def empty(self) -> bool:
        return self._arbiter.total_depth() == 0

    def full(self) -> bool:
        return self._maxsize > 0 and self._arbiter.total_depth() >= self._maxsize

    def task_done(self) -> None:
        """Decrements unfinished task counter, waking join() when 0."""
        with self._cond:
            if self._unfinished_tasks > 0:
                self._unfinished_tasks -= 1
                if self._unfinished_tasks == 0:
                    self._cond.notify_all()

    def join(self) -> None:
        """Blocks until all items in the queue have had task_done() called."""
        with self._cond:
            while self._unfinished_tasks > 0:
                self._cond.wait()

    def clear(self) -> None:
        with self._cond:
            self._arbiter.clear()
            self._unfinished_tasks = 0
            self._cond.notify_all()

    def __len__(self) -> int:
        return self.qsize()

    def __repr__(self) -> str:
        return f"<QueueProxyFacade qsize={self.qsize()} maxsize={self.maxsize}>"
