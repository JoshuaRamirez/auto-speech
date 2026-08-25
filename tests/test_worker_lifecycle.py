"""Unit tests for WorkerLifecycleMachine.

Exercises the full success path, every BAILED early-exit edge, and the
prohibition that DONE is reachable only through SPEAKING (never directly
from RESOLVING or AWAITING_TURN).
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from state_machine import IllegalTransition
from worker_lifecycle import (
    AWAITING_TURN,
    BAILED,
    COALESCING,
    DONE,
    QUEUED,
    RESOLVING,
    SPAWNED,
    SPEAKING,
    WorkerLifecycleMachine,
)


def test_full_success_path() -> None:
    m = WorkerLifecycleMachine()
    assert m.state == SPAWNED
    for s in (COALESCING, QUEUED, RESOLVING, AWAITING_TURN, SPEAKING, DONE):
        m.transition(s)
    assert m.state == DONE
    assert m.is_terminal()


def test_bailed_reachable_from_every_pre_play_state() -> None:
    for path, last in [
        ([], SPAWNED),
        ([COALESCING], COALESCING),
        ([COALESCING, QUEUED], QUEUED),
        ([COALESCING, QUEUED, RESOLVING], RESOLVING),
        ([COALESCING, QUEUED, RESOLVING, AWAITING_TURN], AWAITING_TURN),
    ]:
        m = WorkerLifecycleMachine()
        for s in path:
            m.transition(s)
        assert m.state == last
        m.transition(BAILED)
        assert m.is_terminal()


def _expect_illegal(m: WorkerLifecycleMachine, to: str) -> None:
    try:
        m.transition(to)
    except IllegalTransition:
        return
    raise AssertionError(f"expected IllegalTransition for {m.state} → {to}")


def test_done_not_reachable_without_speaking() -> None:
    # RESOLVING → DONE is prohibited.
    m = WorkerLifecycleMachine()
    m.transition(COALESCING)
    m.transition(QUEUED)
    m.transition(RESOLVING)
    _expect_illegal(m, DONE)
    # AWAITING_TURN → DONE is prohibited.
    m.transition(AWAITING_TURN)
    _expect_illegal(m, DONE)


def test_terminal_states_have_no_exit() -> None:
    m = WorkerLifecycleMachine()
    m.transition(BAILED)
    assert m.can(COALESCING) is False
    assert m.can(DONE) is False


def main() -> int:
    tests = [
        test_full_success_path,
        test_bailed_reachable_from_every_pre_play_state,
        test_done_not_reachable_without_speaking,
        test_terminal_states_have_no_exit,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"worker_lifecycle: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
