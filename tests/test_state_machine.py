"""Unit tests for the shared StateMachine foundation.

Covers legal advancement, illegal-transition rejection with a clear
message, terminal detection, and a thread-safety smoke test that
concurrent transitions never leave the machine in a corrupt state.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from state_machine import IllegalTransition, StateMachine


def _machine() -> StateMachine:
    return StateMachine(
        "a",
        {"a": {"b"}, "b": {"c", "a"}, "c": set()},
        terminal=frozenset({"c"}),
    )


def test_legal_transition_advances() -> None:
    m = _machine()
    assert m.state == "a"
    assert m.transition("b") == "b"
    assert m.state == "b"
    assert m.can("c") is True
    assert m.can("a") is True


def test_illegal_transition_raises_with_message() -> None:
    m = _machine()
    try:
        m.transition("c")  # a → c is illegal
    except IllegalTransition as exc:
        assert "'a'" in str(exc) and "'c'" in str(exc), str(exc)
    else:
        raise AssertionError("expected IllegalTransition")
    # State must be unchanged after a rejected transition.
    assert m.state == "a"


def test_terminal_detection() -> None:
    m = _machine()
    assert m.is_terminal() is False
    m.transition("b")
    m.transition("c")
    assert m.is_terminal() is True
    assert m.can("a") is False  # no exits from terminal


def test_thread_safety_smoke() -> None:
    # Many threads hammer a 2-state ping-pong machine; every transition is
    # either applied or legally rejected, and the final state stays valid.
    m = StateMachine("x", {"x": {"y"}, "y": {"x"}})
    errors: list[Exception] = []

    def worker() -> None:
        for _ in range(500):
            for target in ("y", "x"):
                try:
                    m.transition(target)
                except IllegalTransition:
                    pass  # lost the race; legal outcome
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"unexpected errors under concurrency: {errors}"
    assert m.state in {"x", "y"}, f"corrupt state: {m.state!r}"


def main() -> int:
    tests = [
        test_legal_transition_advances,
        test_illegal_transition_raises_with_message,
        test_terminal_detection,
        test_thread_safety_smoke,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"state_machine: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
