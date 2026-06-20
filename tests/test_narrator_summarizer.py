"""Unit tests for narrator_summarizer (Mock + factory).

MlxSummarizer is intentionally not tested here — exercising it would
require the mlx-lm dep and a 2-4 GB model download. Provider="mock" is
the safe path and what the factory falls back to on MLX failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from narrator_phase_classifier import Category, Phase, ToolEvent  # noqa: E402
from narrator_summarizer import (  # noqa: E402
    MockSummarizer,
    load_summarizer,
)


def _phase(category: Category, summaries: list[str], start: float = 0.0, end: float = 1.0) -> Phase:
    p = Phase(category=category)
    for i, s in enumerate(summaries):
        p.add(ToolEvent(ts=start + i, tool_name=s.split(":", 1)[0], category=category, summary=s))
    p.started_ts = start
    p.ended_ts = end
    return p


def test_mock_single_event_strips_tool_prefix() -> None:
    summ = MockSummarizer()
    phase = _phase(Category.EDIT, ["Edit: /path/to/foo.py"])
    line = summ.summarize(phase)
    # Should NOT double-voice — "Editing: Edit: /path" is the bug we fixed.
    assert "Edit: Edit:" not in line
    assert line.startswith("Editing"), f"got: {line!r}"
    assert "/path/to/foo.py" in line


def test_mock_single_event_each_category() -> None:
    summ = MockSummarizer()
    cases = [
        (Category.EXPLORE, ["Read: a.py"], "Exploring", "a.py"),
        (Category.EDIT, ["Write: b.py"], "Editing", "b.py"),
        (Category.RUN, ["Bash: ls -la"], "Running", "ls -la"),
        (Category.DELEGATE, ["Agent: do X"], "Delegating to", "do X"),
    ]
    for cat, events, expected_verb_prefix, expected_arg in cases:
        line = summ.summarize(_phase(cat, events))
        assert line.startswith(expected_verb_prefix), (
            f"category={cat.value}: expected prefix {expected_verb_prefix!r}, got {line!r}"
        )
        assert expected_arg in line, (
            f"category={cat.value}: expected {expected_arg!r} in {line!r}"
        )


def test_mock_multi_event_includes_count_and_first() -> None:
    summ = MockSummarizer()
    phase = _phase(Category.EDIT, ["Edit: a.py", "Edit: b.py", "Edit: c.py"])
    line = summ.summarize(phase)
    assert "3 items" in line, f"expected count in: {line!r}"
    assert "a.py" in line, f"expected first event's arg in: {line!r}"
    # The tool prefix from the first event should also be stripped here.
    assert "Edit: Edit:" not in line


def test_mock_NEVER_uses_canned_assistant_or_ai_subject() -> None:
    """User feedback: "the assistant is ..." sounds canned.
    Mock output must lead with the verb or the artifact — never with
    a generic narrator subject. Regression-pin all four categories,
    both single- and multi-event variants."""
    summ = MockSummarizer()
    banned = ("the assistant", "the ai", "the system", "the user")
    cases: list[Phase] = []
    for cat, events in [
        (Category.EDIT, ["Edit: a.py"]),
        (Category.EDIT, ["Edit: a.py", "Edit: b.py", "Edit: c.py"]),
        (Category.EXPLORE, ["Read: x.py"]),
        (Category.EXPLORE, ["Read: x.py", "Grep: foo"]),
        (Category.RUN, ["Bash: ls"]),
        (Category.RUN, ["Bash: ls", "Bash: pwd"]),
        (Category.DELEGATE, ["Agent: deep-dive"]),
    ]:
        cases.append(_phase(cat, events))
    for p in cases:
        line = summ.summarize(p).lower()
        for phrase in banned:
            assert phrase not in line, (
                f"mock summary leaked the canned narrator subject "
                f"{phrase!r}: {line!r}"
            )


def test_mock_unknown_category_falls_back_to_performing() -> None:
    summ = MockSummarizer()
    phase = _phase(Category.OTHER, ["mcp__threads__add_progress"])
    # OTHER maps to "performing" (gerund fallback).
    line = summ.summarize(phase)
    assert "Performing" in line


def test_mock_strip_tool_prefix_handles_no_colon() -> None:
    # _strip_tool_prefix should be a no-op when the summary has no "ToolName: " prefix.
    assert MockSummarizer._strip_tool_prefix("bare summary") == "bare summary"
    assert MockSummarizer._strip_tool_prefix("Tool: arg") == "arg"
    assert MockSummarizer._strip_tool_prefix("Tool: a: b") == "a: b", (
        "only the FIRST ': ' should be split"
    )


def test_factory_returns_mock_when_provider_is_mock() -> None:
    summ = load_summarizer({"provider": "mock"})
    assert isinstance(summ, MockSummarizer)


def test_factory_defaults_to_mock_when_provider_missing() -> None:
    # Empty config → mock fallback (safe default; no surprise model downloads).
    summ = load_summarizer({})
    assert isinstance(summ, MockSummarizer)


def test_factory_falls_back_to_mock_on_mlx_failure() -> None:
    # Point at a model that almost certainly doesn't resolve, with a
    # non-existent prompt template path. MLX import + load should raise;
    # factory must catch and downgrade.
    summ = load_summarizer({
        "provider": "mlx",
        "model": "definitely-not-a-real-model-xyz-12345",
        "prompt_template_path": "/nonexistent/prompt.txt",
        "max_tokens": 10,
    })
    assert isinstance(summ, MockSummarizer), (
        "MLX failure must downgrade to Mock, not crash"
    )


def test_factory_returns_ollama_summarizer_when_provider_is_ollama() -> None:
    # OllamaSummarizer's __init__ doesn't hit the network — it just
    # reads the template file and stores config. So this test is safe
    # even with no Ollama service running.
    import tempfile
    from pathlib import Path

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    tmp.write("body {events}")
    tmp.close()
    try:
        summ = load_summarizer({
            "provider": "ollama",
            "model": "qwen2.5:3b",
            "prompt_template_path": tmp.name,
            "max_tokens": 50,
            "ollama_host": "http://127.0.0.1:11434",
        })
        # On success it's OllamaSummarizer; on import/init failure the
        # factory's exception handler downgrades to Mock.
        assert summ.__class__.__name__ in ("OllamaSummarizer", "MockSummarizer"), (
            f"unexpected: {summ.__class__.__name__}"
        )
    finally:
        Path(tmp.name).unlink()


def test_factory_raises_on_unknown_provider() -> None:
    try:
        load_summarizer({"provider": "definitely-unknown-provider"})
    except ValueError as exc:
        assert "definitely-unknown-provider" in str(exc)
        return
    raise AssertionError("unknown provider should raise ValueError")


def main() -> int:
    tests = [
        test_mock_single_event_strips_tool_prefix,
        test_mock_single_event_each_category,
        test_mock_multi_event_includes_count_and_first,
        test_mock_NEVER_uses_canned_assistant_or_ai_subject,
        test_mock_unknown_category_falls_back_to_performing,
        test_mock_strip_tool_prefix_handles_no_colon,
        test_factory_returns_mock_when_provider_is_mock,
        test_factory_defaults_to_mock_when_provider_missing,
        test_factory_falls_back_to_mock_on_mlx_failure,
        test_factory_returns_ollama_summarizer_when_provider_is_ollama,
        test_factory_raises_on_unknown_provider,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"Summarizer: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
