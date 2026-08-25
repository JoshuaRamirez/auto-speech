"""Unit tests for SessionEnrollment (autoplay opt-IN model).

Autoplay is OFF unless a session enrolls. The critical property is the
DEFAULT: a session nobody has touched must not play. Also pinned here is
the directory split — enrollment must not read the pre-inversion opt-out
directory, because every marker in it was written by someone asking for
SILENCE, and reading them as enrollments would turn autoplay on for
exactly the wrong sessions.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from autoplay_enrollment import (
    LEGACY_OPTOUT_DIR_NAME,
    SessionEnrollment,
    format_status,
)


def _home() -> Path:
    return Path(tempfile.mkdtemp(prefix="auto-speech-enroll-test-"))


def test_default_is_off() -> None:
    e = SessionEnrollment(home=_home())
    assert e.is_enrolled("any-session") is False
    assert e.enrolled_ids() == []


def test_enroll_then_withdraw() -> None:
    e = SessionEnrollment(home=_home())
    assert e.enroll("s1") is True
    assert e.is_enrolled("s1") is True
    assert e.enroll("s1") is False, "second enroll reports 'already'"
    assert e.withdraw("s1") is True
    assert e.is_enrolled("s1") is False
    assert e.withdraw("s1") is False, "second withdraw reports 'nothing removed'"


def test_enrollment_is_per_session() -> None:
    e = SessionEnrollment(home=_home())
    e.enroll("s1")
    assert e.is_enrolled("s1") is True
    assert e.is_enrolled("s2") is False, "enrolling one session must not enable others"
    assert e.enrolled_ids() == ["s1"]


def test_missing_session_id_is_never_enrolled() -> None:
    """An unidentifiable session stays quiet under an opt-in default."""
    e = SessionEnrollment(home=_home())
    e.enroll("s1")
    assert e.is_enrolled("") is False
    assert e.is_enrolled(None) is False


def test_legacy_optout_markers_are_not_read_as_enrollments() -> None:
    """The pre-inversion directory must stay untouched.

    Its markers mean 'this session asked for silence'. Reading them as
    enrollments would enable autoplay for precisely the sessions that had
    opted out — the worst possible inversion bug.
    """
    home = _home()
    legacy = home / ".claude" / LEGACY_OPTOUT_DIR_NAME
    legacy.mkdir(parents=True)
    (legacy / "previously-muted").touch()

    e = SessionEnrollment(home=home)
    assert e.is_enrolled("previously-muted") is False
    assert e.enrolled_ids() == []


def test_enroll_rejects_empty_session_id() -> None:
    e = SessionEnrollment(home=_home())
    try:
        e.enroll("")
    except ValueError:
        return
    raise AssertionError("enroll('') must raise rather than create a stray marker")


def test_format_status_reports_both_states() -> None:
    home = _home()
    e = SessionEnrollment(home=home)
    assert "OFF" in format_status(e, "s1")
    e.enroll("s1")
    assert "ON" in format_status(e, "s1")
    # A different, unenrolled session sees OFF but is told others are on.
    other = format_status(e, "s2")
    assert "OFF" in other and "1 other session" in other


def main() -> int:
    tests = [
        test_default_is_off,
        test_enroll_then_withdraw,
        test_enrollment_is_per_session,
        test_missing_session_id_is_never_enrolled,
        test_legacy_optout_markers_are_not_read_as_enrollments,
        test_enroll_rejects_empty_session_id,
        test_format_status_reports_both_states,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"autoplay_enrollment: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
