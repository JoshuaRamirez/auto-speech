"""Unit tests for SoloScope (autoplay session spotlight).

Covers the ALL default, claiming/clearing the spotlight, the per-session
allow decision under both scopes, the empty-session-id safety rule
(never mute an unidentifiable session), atomic re-claim, and the
human-readable status formatter.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from autoplay_scope import ALL, SOLO, SoloScope, format_status  # noqa: E402


def _home() -> Path:
    h = Path(tempfile.mkdtemp(prefix="auto-speech-scope-test-"))
    (h / ".claude").mkdir(parents=True)
    return h


def test_all_by_default() -> None:
    s = SoloScope(home=_home())
    assert s.current() is None
    assert s.mode() == ALL
    assert s.session_allowed("sess-A") is True
    assert s.session_allowed(None) is True


def test_set_solo_claims_spotlight() -> None:
    s = SoloScope(home=_home())
    s.set_solo("sess-A")
    assert s.current() == "sess-A"
    assert s.mode() == SOLO


def test_solo_allows_only_holder() -> None:
    s = SoloScope(home=_home())
    s.set_solo("sess-A")
    assert s.session_allowed("sess-A") is True
    assert s.session_allowed("sess-B") is False


def test_solo_does_not_mute_unknown_session() -> None:
    # Safety rule: an empty/unknown session id is never muted under SOLO,
    # so a jq-less hook cannot accidentally silence everything.
    s = SoloScope(home=_home())
    s.set_solo("sess-A")
    assert s.session_allowed(None) is True
    assert s.session_allowed("") is True


def test_clear_returns_to_all() -> None:
    s = SoloScope(home=_home())
    s.set_solo("sess-A")
    s.clear()
    assert s.current() is None
    assert s.mode() == ALL
    assert s.session_allowed("sess-B") is True


def test_clear_is_idempotent() -> None:
    s = SoloScope(home=_home())
    s.clear()  # no marker present — must not raise
    assert s.current() is None


def test_reclaim_moves_spotlight() -> None:
    s = SoloScope(home=_home())
    s.set_solo("sess-A")
    s.set_solo("sess-B")
    assert s.current() == "sess-B"
    assert s.session_allowed("sess-A") is False
    assert s.session_allowed("sess-B") is True


def test_set_solo_rejects_empty() -> None:
    s = SoloScope(home=_home())
    try:
        s.set_solo("")
    except ValueError:
        pass
    else:
        raise AssertionError("set_solo('') should raise ValueError")


def test_whitespace_marker_reads_as_all() -> None:
    # A marker file holding only whitespace is treated as no spotlight.
    s = SoloScope(home=_home())
    s.marker_path().write_text("  \n", encoding="utf-8")
    assert s.current() is None
    assert s.mode() == ALL


def test_format_status() -> None:
    s = SoloScope(home=_home())
    assert "ALL" in format_status(s, "sess-A")
    s.set_solo("sess-A")
    assert "THIS session" in format_status(s, "sess-A")
    assert "muted" in format_status(s, "sess-B")


def main() -> int:
    tests = [
        test_all_by_default,
        test_set_solo_claims_spotlight,
        test_solo_allows_only_holder,
        test_solo_does_not_mute_unknown_session,
        test_clear_returns_to_all,
        test_clear_is_idempotent,
        test_reclaim_moves_spotlight,
        test_set_solo_rejects_empty,
        test_whitespace_marker_reads_as_all,
        test_format_status,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"autoplay_scope: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
