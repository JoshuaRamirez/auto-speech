"""Unit tests for the mpv PlaybackStateMachine.

Exercises every legal edge of the playback lifecycle plus representative
illegal transitions out of IDLE.
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from playback_state import (  # noqa: E402
    IDLE,
    READY,
    STARTING,
    STOPPING,
    PlaybackStateMachine,
)
from state_machine import IllegalTransition  # noqa: E402


def test_full_cycle() -> None:
    m = PlaybackStateMachine()
    assert m.state == IDLE
    m.transition(STARTING)
    m.transition(READY)
    m.transition(STOPPING)
    m.transition(IDLE)
    assert m.state == IDLE  # cycles back to rest


def test_startup_failure_edge() -> None:
    m = PlaybackStateMachine()
    m.transition(STARTING)
    m.transition(IDLE)  # STARTING → IDLE (startup failed/aborted)
    assert m.state == IDLE


def test_process_exited_on_its_own_edge() -> None:
    m = PlaybackStateMachine()
    m.transition(STARTING)
    m.transition(READY)
    m.transition(IDLE)  # READY → IDLE (process exited itself)
    assert m.state == IDLE


def test_no_terminal_state() -> None:
    m = PlaybackStateMachine()
    assert m.is_terminal() is False
    m.transition(STARTING)
    assert m.is_terminal() is False


def _expect_illegal(m: PlaybackStateMachine, to: str) -> None:
    try:
        m.transition(to)
    except IllegalTransition:
        return
    raise AssertionError(f"expected IllegalTransition for IDLE → {to}")


def test_illegal_transitions_from_idle() -> None:
    _expect_illegal(PlaybackStateMachine(), READY)     # IDLE → READY
    _expect_illegal(PlaybackStateMachine(), STOPPING)  # IDLE → STOPPING


def main() -> int:
    tests = [
        test_full_cycle,
        test_startup_failure_edge,
        test_process_exited_on_its_own_edge,
        test_no_terminal_state,
        test_illegal_transitions_from_idle,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"playback_state: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
