"""Unit tests for JobTracker after the StateMachine refactor.

Pins the public API/exception semantics: begin/transition/fail/current/
is_active, RuntimeError for no-current-job, ValueError for illegal
transition. These must hold regardless of the shared FSM backing.
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from job_state import (
    PHASE_FAILED,
    PHASE_GENERATING,
    PHASE_HANDED_OFF,
    PHASE_QUEUED,
    PHASE_REWRITING,
)
from job_tracker import JobTracker


def test_begin_creates_queued_active_job() -> None:
    t = JobTracker()
    assert t.current() is None
    assert t.is_active() is False
    job = t.begin("rewrite", source_chars=42, source_hash="h")
    assert job.phase == PHASE_QUEUED
    assert t.is_active() is True
    assert t.current().id == job.id


def test_begin_rejects_second_active_job() -> None:
    t = JobTracker()
    t.begin("rewrite", 1, None)
    try:
        t.begin("rewrite", 1, None)
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError on second active begin")


def test_legal_transition_sequence() -> None:
    t = JobTracker()
    t.begin("rewrite", 10, None)
    t.transition(PHASE_REWRITING)
    t.transition(PHASE_GENERATING, rewrite_chars=8)
    j = t.transition(PHASE_HANDED_OFF, hash="abc")
    assert j.phase == PHASE_HANDED_OFF
    assert j.rewrite_chars == 8
    assert j.hash == "abc"
    assert t.is_active() is False  # handed off is terminal


def test_illegal_transition_raises_value_error() -> None:
    t = JobTracker()
    t.begin("rewrite", 10, None)
    try:
        t.transition(PHASE_HANDED_OFF)  # queued → handed_off is illegal
    except ValueError:
        # The job must be unchanged after a rejected transition.
        assert t.current().phase == PHASE_QUEUED
        return
    raise AssertionError("expected ValueError on illegal transition")


def test_transition_without_job_raises_runtime_error() -> None:
    t = JobTracker()
    try:
        t.transition(PHASE_REWRITING)
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError with no current job")


def test_fail_from_active_phase() -> None:
    t = JobTracker()
    t.begin("rewrite", 10, None)
    t.transition(PHASE_GENERATING)
    j = t.fail("boom")
    assert j.phase == PHASE_FAILED
    assert j.error == "boom"
    assert t.is_active() is False


def test_fail_without_job_raises_runtime_error() -> None:
    t = JobTracker()
    try:
        t.fail("x")
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError with no current job")


def test_fail_from_terminal_is_unconditional() -> None:
    # Original behavior: fail() never validated legality.
    t = JobTracker()
    t.begin("rewrite", 10, None)
    t.transition(PHASE_GENERATING)
    t.transition(PHASE_HANDED_OFF)
    j = t.fail("late error")  # handed_off → failed, forced
    assert j.phase == PHASE_FAILED
    assert j.error == "late error"


def main() -> int:
    tests = [
        test_begin_creates_queued_active_job,
        test_begin_rejects_second_active_job,
        test_legal_transition_sequence,
        test_illegal_transition_raises_value_error,
        test_transition_without_job_raises_runtime_error,
        test_fail_from_active_phase,
        test_fail_without_job_raises_runtime_error,
        test_fail_from_terminal_is_unconditional,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"job_tracker: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
