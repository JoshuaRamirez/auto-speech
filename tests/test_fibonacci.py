"""Unit tests for FibonacciSeq.

The autoplay chunk planner relies on this generator producing a strict
1, 1, 2, 3, 5, 8, ... sequence. Pin the contract.
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from fibonacci import FibonacciSeq

_EXPECTED_FIRST_15 = [1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610]


def test_sequence_matches_expected_prefix() -> None:
    seq = FibonacciSeq()
    actual = [seq.next_target() for _ in range(len(_EXPECTED_FIRST_15))]
    assert actual == _EXPECTED_FIRST_15, f"got {actual}"


def test_emitted_count_increments_per_call() -> None:
    seq = FibonacciSeq()
    assert seq.emitted_count == 0
    for n in range(1, 6):
        seq.next_target()
        assert seq.emitted_count == n


def test_two_instances_are_independent() -> None:
    a = FibonacciSeq()
    b = FibonacciSeq()
    a.next_target()  # 1
    a.next_target()  # 1
    a.next_target()  # 2
    # b is untouched.
    assert b.next_target() == 1
    assert b.emitted_count == 1
    # a continues from where it left off.
    assert a.next_target() == 3


def test_first_two_are_both_one() -> None:
    seq = FibonacciSeq()
    assert seq.next_target() == 1
    assert seq.next_target() == 1


def test_grows_monotonically_after_position_2() -> None:
    seq = FibonacciSeq()
    prev = seq.next_target()  # 1
    seq.next_target()         # 1 (duplicate)
    prev = 1
    for _ in range(20):
        nxt = seq.next_target()
        assert nxt > prev, f"sequence must strictly increase after position 2: {prev} -> {nxt}"
        prev = nxt


def main() -> int:
    tests = [
        test_sequence_matches_expected_prefix,
        test_emitted_count_increments_per_call,
        test_two_instances_are_independent,
        test_first_two_are_both_one,
        test_grows_monotonically_after_position_2,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"FibonacciSeq: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
