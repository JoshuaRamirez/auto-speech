"""Unit tests for MessageSelector + TranscriptReader.

Builds synthetic JSONL transcripts and verifies the ring-buffer
ordering, qualifying-message filter, and error paths.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from message_selector import MessageSelector, NoSuchAssistantTurn  # noqa: E402
from transcript_reader import TranscriptReadError, TranscriptReader  # noqa: E402


def _write_jsonl(records: list[dict]) -> Path:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    for r in records:
        f.write(json.dumps(r))
        f.write("\n")
    f.close()
    return Path(f.name)


def _assistant(text: str, ts: str = "2026-05-16T00:00:00Z") -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
        "timestamp": ts,
    }


def _user(text: str) -> dict:
    return {
        "type": "user",
        "message": {"role": "user", "content": text},
    }


def test_select_first_picks_most_recent_assistant() -> None:
    path = _write_jsonl([
        _user("hi"),
        _assistant("first reply", ts="2026-05-16T01:00:00Z"),
        _user("ok"),
        _assistant("second reply", ts="2026-05-16T02:00:00Z"),
    ])
    try:
        msg = MessageSelector().select(path, 1)
        assert msg.text == "second reply"
        assert msg.ordinal_from_end == 1
        assert msg.timestamp == "2026-05-16T02:00:00Z"
    finally:
        path.unlink()


def test_select_nth_oldest_in_window() -> None:
    path = _write_jsonl([
        _assistant("oldest", ts="t1"),
        _assistant("middle", ts="t2"),
        _assistant("newest", ts="t3"),
    ])
    try:
        # n=1 → newest, n=2 → middle, n=3 → oldest.
        assert MessageSelector().select(path, 1).text == "newest"
        assert MessageSelector().select(path, 2).text == "middle"
        assert MessageSelector().select(path, 3).text == "oldest"
    finally:
        path.unlink()


def test_select_skips_tool_only_assistant_turns() -> None:
    # Assistant turn with NO text block (just a tool_use) does not qualify.
    tool_only = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "x", "name": "Bash"}],
        },
    }
    path = _write_jsonl([
        _assistant("real reply", ts="t1"),
        tool_only,
    ])
    try:
        msg = MessageSelector().select(path, 1)
        # The most-recent QUALIFYING assistant turn is "real reply".
        assert msg.text == "real reply"
    finally:
        path.unlink()


def test_select_skips_blank_text_blocks() -> None:
    blank = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "   \n  "}],
        },
    }
    path = _write_jsonl([
        _assistant("substantive", ts="t1"),
        blank,
    ])
    try:
        msg = MessageSelector().select(path, 1)
        assert msg.text == "substantive"
    finally:
        path.unlink()


def test_select_joins_multiple_text_blocks() -> None:
    # An assistant turn can have multiple text blocks (e.g., before and
    # after a tool call). The selector should concatenate them.
    multi = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "part one"},
                {"type": "tool_use", "id": "x", "name": "Bash"},
                {"type": "text", "text": "part two"},
            ],
        },
    }
    path = _write_jsonl([multi])
    try:
        msg = MessageSelector().select(path, 1)
        assert "part one" in msg.text
        assert "part two" in msg.text
    finally:
        path.unlink()


def test_select_raises_when_not_enough_messages() -> None:
    path = _write_jsonl([_assistant("only one")])
    try:
        try:
            MessageSelector().select(path, 5)
        except NoSuchAssistantTurn as exc:
            assert "5" in str(exc) or "only" in str(exc).lower()
            return
        raise AssertionError("expected NoSuchAssistantTurn")
    finally:
        path.unlink()


def test_exclude_regex_skips_matching_assistant_messages() -> None:
    """Regression for the /auto-speech-speak self-reference loop: a
    repeated invocation was extracting its own previous status line
    ("spoke message #1 — ...") instead of the real prior content."""
    path = _write_jsonl([
        _assistant("real technical answer with the actual content"),
        _assistant("spoke message #1 — 28 source chars, 48 rewrite chars"),
    ])
    try:
        # Without the filter, ordinal=1 = the speak status echo.
        msg = MessageSelector().select(path, 1)
        assert msg.text.startswith("spoke message #1"), (
            f"sanity: without filter, status is most recent. Got: {msg.text!r}"
        )

        # With the filter, ordinal=1 = the real content (status is skipped).
        msg = MessageSelector().select(
            path, 1, exclude_regex=r"^spoke message #\d+"
        )
        assert msg.text == "real technical answer with the actual content", (
            f"filter should have skipped the speak echo. Got: {msg.text!r}"
        )
    finally:
        path.unlink()


def test_exclude_regex_consecutive_echoes_all_skipped() -> None:
    """Two prior /speak invocations, only one real message. ordinal=1
    with the filter still hits the real message."""
    path = _write_jsonl([
        _assistant("real content"),
        _assistant("spoke message #1 — 50 source chars, 80 rewrite chars"),
        _assistant("spoke message #1 — 80 source chars, 95 rewrite chars"),
    ])
    try:
        msg = MessageSelector().select(
            path, 1, exclude_regex=r"^spoke message #\d+"
        )
        assert msg.text == "real content"
    finally:
        path.unlink()


def test_exclude_regex_raises_when_all_messages_match() -> None:
    """If every qualifying message matches the filter, ordinal=1 should
    raise NoSuchAssistantTurn (filtered count is zero)."""
    path = _write_jsonl([
        _assistant("spoke message #1 — first"),
        _assistant("spoke message #1 — second"),
    ])
    try:
        try:
            MessageSelector().select(path, 1, exclude_regex=r"^spoke message")
        except NoSuchAssistantTurn:
            return
        raise AssertionError("expected NoSuchAssistantTurn when all messages filtered")
    finally:
        path.unlink()


def test_select_raises_on_zero_or_negative_n() -> None:
    path = _write_jsonl([_assistant("x")])
    try:
        for bad_n in (0, -1):
            try:
                MessageSelector().select(path, bad_n)
            except ValueError:
                continue
            raise AssertionError(f"n={bad_n} should have raised ValueError")
    finally:
        path.unlink()


def test_transcript_reader_skips_blank_lines() -> None:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    f.write("\n")
    f.write(json.dumps({"a": 1}) + "\n")
    f.write("   \n")
    f.write(json.dumps({"a": 2}) + "\n")
    f.close()
    path = Path(f.name)
    try:
        records = list(TranscriptReader.iter_lines(path))
        assert records == [{"a": 1}, {"a": 2}]
    finally:
        path.unlink()


def test_transcript_reader_raises_on_malformed_line_with_line_number() -> None:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    f.write(json.dumps({"ok": 1}) + "\n")
    f.write("this is not json\n")
    f.close()
    path = Path(f.name)
    try:
        try:
            list(TranscriptReader.iter_lines(path))
        except TranscriptReadError as exc:
            assert ":2" in str(exc), f"expected line number in {exc!r}"
            return
        raise AssertionError("expected TranscriptReadError")
    finally:
        path.unlink()


def main() -> int:
    tests = [
        test_select_first_picks_most_recent_assistant,
        test_select_nth_oldest_in_window,
        test_select_skips_tool_only_assistant_turns,
        test_select_skips_blank_text_blocks,
        test_select_joins_multiple_text_blocks,
        test_select_raises_when_not_enough_messages,
        test_exclude_regex_skips_matching_assistant_messages,
        test_exclude_regex_consecutive_echoes_all_skipped,
        test_exclude_regex_raises_when_all_messages_match,
        test_select_raises_on_zero_or_negative_n,
        test_transcript_reader_skips_blank_lines,
        test_transcript_reader_raises_on_malformed_line_with_line_number,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"MessageSelector + TranscriptReader: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
