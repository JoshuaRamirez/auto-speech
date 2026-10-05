"""Unit tests for PriorityArbiter (in-memory 4-tier FIFO playback arbiter).

Modernized replacement for the legacy file-based playback_fifo test.
Validates:
  - strict FIFO arrival-order within the same priority tier
  - multi-band priority ordering (P1 > P2 > P3 > P4)
  - preemption and re-queueing at head
  - P1 user barge-in purging lower tiers
  - drop-oldest bounded queue backpressure
  - thread-safe condition variable unblocking
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from priority_arbiter import Priority, PriorityArbiter, QueueItem, QueueProxyFacade  # noqa: E402


class TestPriorityArbiterModernized(unittest.TestCase):
    """Test suite for in-memory priority and FIFO arbitration."""

    def test_strict_fifo_arrival_order_within_same_band(self) -> None:
        arbiter = PriorityArbiter(maxsize=10)
        arbiter.enqueue("first", priority=Priority.AUTOPLAY)
        arbiter.enqueue("second", priority=Priority.AUTOPLAY)
        arbiter.enqueue("third", priority=Priority.AUTOPLAY)

        self.assertEqual(arbiter.dequeue(timeout=0.1), "first")
        self.assertEqual(arbiter.dequeue(timeout=0.1), "second")
        self.assertEqual(arbiter.dequeue(timeout=0.1), "third")

    def test_higher_priority_preempts_lower_priority(self) -> None:
        arbiter = PriorityArbiter(maxsize=10)
        # Enqueue low priority items first
        arbiter.enqueue("narration_1", priority=Priority.TOOL_NARRATION)
        arbiter.enqueue("autoplay_1", priority=Priority.AUTOPLAY)
        arbiter.enqueue("mcp_1", priority=Priority.EXPLICIT_MCP)
        arbiter.enqueue("interrupt_1", priority=Priority.USER_INTERRUPT)

        # Must be dequeued in order of priority (P1 -> P2 -> P3 -> P4)
        self.assertEqual(arbiter.dequeue(timeout=0.1), "interrupt_1")
        self.assertEqual(arbiter.dequeue(timeout=0.1), "mcp_1")
        self.assertEqual(arbiter.dequeue(timeout=0.1), "autoplay_1")
        self.assertEqual(arbiter.dequeue(timeout=0.1), "narration_1")

    def test_p1_barge_in_purges_lower_priority_items(self) -> None:
        arbiter = PriorityArbiter(maxsize=10)
        arbiter.enqueue("narration_stale", priority=Priority.TOOL_NARRATION)
        arbiter.enqueue("autoplay_stale", priority=Priority.AUTOPLAY)

        purged = arbiter.purge_lower_queues()
        self.assertEqual(purged, 2)
        self.assertEqual(arbiter.total_depth(), 0)

    def test_drop_oldest_backpressure_shedding(self) -> None:
        arbiter = PriorityArbiter(maxsize=3)
        arbiter.enqueue("item_1", priority=Priority.TOOL_NARRATION)
        arbiter.enqueue("item_2", priority=Priority.TOOL_NARRATION)
        arbiter.enqueue("item_3", priority=Priority.TOOL_NARRATION)
        self.assertEqual(arbiter.total_depth(), 3)

        # 4th item pushes out the oldest item (item_1)
        arbiter.enqueue("item_4", priority=Priority.TOOL_NARRATION)
        self.assertEqual(arbiter.total_depth(), 3)

        self.assertEqual(arbiter.dequeue(timeout=0.1), "item_2")
        self.assertEqual(arbiter.dequeue(timeout=0.1), "item_3")
        self.assertEqual(arbiter.dequeue(timeout=0.1), "item_4")

    def test_requeue_preempted_item_goes_to_front_of_band(self) -> None:
        arbiter = PriorityArbiter(maxsize=10)
        arbiter.enqueue("auto_2", priority=Priority.AUTOPLAY)
        item_1 = QueueItem(priority=Priority.AUTOPLAY, payload="auto_1")
        arbiter.requeue_at_head(item_1)

        # auto_1 must come before auto_2
        self.assertEqual(arbiter.dequeue(timeout=0.1), "auto_1")
        self.assertEqual(arbiter.dequeue(timeout=0.1), "auto_2")

    def test_thread_safe_blocking_and_notification(self) -> None:
        arbiter = PriorityArbiter(maxsize=10)
        received = []

        def consumer() -> None:
            item = arbiter.dequeue(timeout=2.0)
            if item:
                received.append(item)

        t = threading.Thread(target=consumer)
        t.start()

        time.sleep(0.05)
        arbiter.enqueue("notified_payload", priority=Priority.EXPLICIT_MCP)
        t.join(timeout=2.0)

        self.assertEqual(received, ["notified_payload"])

    def test_queue_proxy_facade_compatibility(self) -> None:
        arbiter = PriorityArbiter(maxsize=5)
        proxy = QueueProxyFacade(arbiter, maxsize=5)

        self.assertTrue(proxy.empty())
        proxy.put("string_payload")
        self.assertFalse(proxy.empty())
        self.assertEqual(proxy.qsize(), 1)
        self.assertEqual(proxy.get(timeout=0.1), "string_payload")
        self.assertTrue(proxy.empty())


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestPriorityArbiterModernized)
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=1)
    res = runner.run(suite)
    if res.wasSuccessful():
        print(f"playback_fifo: {res.testsRun} tests passed")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
