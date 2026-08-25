"""Unit tests for the narrator daemon NarratorStateMachine.

Covers both shutdown lifecycle paths and a representative illegal
transition out of the NOT_RUNNING rest state.
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from narrator_state import (
    IDLE_SHUTDOWN,
    NOT_RUNNING,
    RUNNING,
    SIGNAL_SHUTDOWN,
    STARTING,
    NarratorStateMachine,
)
from state_machine import IllegalTransition


def test_idle_shutdown_lifecycle() -> None:
    m = NarratorStateMachine()
    assert m.state == NOT_RUNNING
    m.transition(STARTING)
    m.transition(RUNNING)
    m.transition(IDLE_SHUTDOWN)
    m.transition(NOT_RUNNING)
    assert m.state == NOT_RUNNING


def test_signal_shutdown_lifecycle() -> None:
    m = NarratorStateMachine()
    m.transition(STARTING)
    m.transition(RUNNING)
    m.transition(SIGNAL_SHUTDOWN)
    m.transition(NOT_RUNNING)
    assert m.state == NOT_RUNNING


def test_failed_start_returns_to_rest() -> None:
    m = NarratorStateMachine()
    m.transition(STARTING)
    m.transition(NOT_RUNNING)  # start failed
    assert m.state == NOT_RUNNING


def test_illegal_skip_to_running() -> None:
    m = NarratorStateMachine()
    try:
        m.transition(RUNNING)  # NOT_RUNNING → RUNNING (must go via STARTING)
    except IllegalTransition:
        return
    raise AssertionError("expected IllegalTransition for NOT_RUNNING → RUNNING")


def main() -> int:
    tests = [
        test_idle_shutdown_lifecycle,
        test_signal_shutdown_lifecycle,
        test_failed_start_returns_to_rest,
        test_illegal_skip_to_running,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"narrator_state: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
