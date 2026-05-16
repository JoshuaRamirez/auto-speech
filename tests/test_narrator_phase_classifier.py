"""Unit tests for PhaseClassifier. Run directly; no pytest required.

Covers the four phase-close triggers:
  1. category change
  2. silence threshold exceeded
  3. Stop event
  4. UserPromptSubmit event

Plus invariants: empty/missing fields are tolerated, OTHER category is
emitted faithfully (suppression happens in the daemon, not the classifier),
and the classifier's internal state resets after each close.
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from narrator_phase_classifier import (  # noqa: E402
    Category,
    Phase,
    PhaseClassifier,
)


def _post_tool(ts: float, tool_name: str, **extras) -> dict:
    payload = {"hook_event_name": "PostToolUse", "tool_name": tool_name}
    payload.update(extras)
    return {"event": "PostToolUse", "ts": ts, "payload": payload}


def _stop(ts: float) -> dict:
    return {"event": "Stop", "ts": ts, "payload": {"hook_event_name": "Stop"}}


def _user_prompt(ts: float) -> dict:
    return {"event": "UserPromptSubmit", "ts": ts, "payload": {}}


def test_category_change_closes_phase() -> None:
    clf = PhaseClassifier(silence_seconds=60.0)
    assert clf.feed(_post_tool(1.0, "Read", tool_input={"file_path": "a.py"})) is None
    assert clf.feed(_post_tool(2.0, "Read", tool_input={"file_path": "b.py"})) is None

    closed = clf.feed(_post_tool(3.0, "Edit", tool_input={"file_path": "a.py"}))
    assert closed is not None, "phase should close on category change"
    assert closed.category == Category.EXPLORE
    assert len(closed.events) == 2
    assert closed.started_ts == 1.0 and closed.ended_ts == 2.0

    # New in-flight phase started; flushing returns it.
    final = clf.flush()
    assert final is not None and final.category == Category.EDIT
    assert len(final.events) == 1


def test_silence_threshold_closes_phase() -> None:
    clf = PhaseClassifier(silence_seconds=5.0)
    assert clf.feed(_post_tool(1.0, "Bash", tool_input={"command": "ls"})) is None
    # 10 s gap > 5 s silence threshold → next event closes the previous phase.
    closed = clf.feed(_post_tool(11.0, "Bash", tool_input={"command": "pwd"}))
    assert closed is not None, "phase should close on silence > threshold"
    assert closed.category == Category.RUN
    assert len(closed.events) == 1


def test_silence_just_under_threshold_does_NOT_close() -> None:
    clf = PhaseClassifier(silence_seconds=5.0)
    assert clf.feed(_post_tool(1.0, "Bash", tool_input={"command": "x"})) is None
    closed = clf.feed(_post_tool(5.9, "Bash", tool_input={"command": "y"}))
    assert closed is None, "phases must NOT close when silence < threshold"
    final = clf.flush()
    assert final is not None and len(final.events) == 2


def test_stop_event_flushes_phase() -> None:
    clf = PhaseClassifier()
    assert clf.feed(_post_tool(1.0, "Bash", tool_input={"command": "x"})) is None
    assert clf.feed(_post_tool(2.0, "Bash", tool_input={"command": "y"})) is None
    closed = clf.feed(_stop(3.0))
    assert closed is not None
    assert closed.category == Category.RUN
    assert len(closed.events) == 2
    # State reset — subsequent flush returns nothing.
    assert clf.flush() is None


def test_user_prompt_submit_resets_state() -> None:
    clf = PhaseClassifier()
    assert clf.feed(_post_tool(1.0, "Read", tool_input={"file_path": "a"})) is None
    closed = clf.feed(_user_prompt(2.0))
    assert closed is not None and len(closed.events) == 1
    assert clf.flush() is None, "classifier should be empty after UserPromptSubmit"


def test_other_category_passes_through_classifier() -> None:
    # The classifier should NOT suppress OTHER — that's the daemon's job.
    clf = PhaseClassifier()
    assert clf.feed(_post_tool(1.0, "mcp__threads__add_progress")) is None
    closed = clf.flush()
    assert closed is not None
    assert closed.category == Category.OTHER
    assert len(closed.events) == 1


def test_pre_tool_use_events_are_ignored() -> None:
    clf = PhaseClassifier()
    pre = {"event": "PreToolUse", "ts": 1.0, "payload": {"tool_name": "Bash"}}
    assert clf.feed(pre) is None, "PreToolUse must not start a phase"
    assert clf.flush() is None


def test_event_with_no_tool_name_is_ignored() -> None:
    clf = PhaseClassifier()
    # PostToolUse but no tool_name → no-op (defensive against malformed payloads)
    bad = {
        "event": "PostToolUse",
        "ts": 1.0,
        "payload": {"hook_event_name": "PostToolUse"},
    }
    assert clf.feed(bad) is None
    assert clf.flush() is None


def test_event_summary_includes_tool_argument() -> None:
    clf = PhaseClassifier()
    clf.feed(_post_tool(1.0, "Bash", tool_input={"command": "ls -la"}))
    closed = clf.flush()
    assert closed is not None
    # The classifier formats each event's summary as "ToolName: arg" — the
    # MockSummarizer relies on this prefix being present so it can strip it.
    assert closed.events[0].summary.startswith("Bash:")
    assert "ls -la" in closed.events[0].summary


def main() -> int:
    tests = [
        test_category_change_closes_phase,
        test_silence_threshold_closes_phase,
        test_silence_just_under_threshold_does_NOT_close,
        test_stop_event_flushes_phase,
        test_user_prompt_submit_resets_state,
        test_other_category_passes_through_classifier,
        test_pre_tool_use_events_are_ignored,
        test_event_with_no_tool_name_is_ignored,
        test_event_summary_includes_tool_argument,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"PhaseClassifier: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
