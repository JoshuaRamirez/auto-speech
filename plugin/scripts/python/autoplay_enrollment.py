"""SessionEnrollment: which sessions have opted IN to autoplay.

Autoplay is OFF unless a session explicitly enrolls. A session enrolls by
creating a marker named after its session id:

  ~/.claude/auto-speech-autoplay-enabled/<session_id>

This replaces the previous opt-OUT model, where every session played by
default and a marker in .../auto-speech-autoplay-sessions/ silenced one.
The two directories are deliberately DIFFERENT: the old one still holds
opt-out markers from before the inversion, and reading those as enrollments
would turn autoplay on for exactly the sessions that had asked for silence.

Precedence, widest first:
  1. ~/.claude/auto-speech.disabled   — global mute, overrides everything
  2. this enrollment                  — off unless the session opted in
  3. SoloScope spotlight              — narrows further among enrolled ones

A session with no id cannot be enrolled and therefore does not play. That
is the safe direction for an opt-in default: an unidentifiable session
stays quiet rather than speaking without ever having been asked for.

Like SoloScope, this is the testable MODEL of a decision the hook makes in
shell (autoplay_hook.sh) plus the writer used by the /auto-speech-autoplay-on
and /auto-speech-autoplay-off commands. `home` is overridable for tests.
"""
from __future__ import annotations

import os
from pathlib import Path

DIR_NAME = "auto-speech-autoplay-enabled"

# The pre-inversion opt-out directory. Retained as a NAME only, so the
# sweep and the docs can refer to it; enrollment never reads it.
LEGACY_OPTOUT_DIR_NAME = "auto-speech-autoplay-sessions"


class SessionEnrollment:
    """Reads/writes per-session autoplay enrollment markers."""

    def __init__(self, home: Path | None = None) -> None:
        self._home = Path(home) if home is not None else Path(os.path.expanduser("~"))

    def dir_path(self) -> Path:
        return self._home / ".claude" / DIR_NAME

    def marker_path(self, session_id: str) -> Path:
        return self.dir_path() / session_id

    def is_enrolled(self, session_id: str | None) -> bool:
        """True iff `session_id` has opted in to autoplay.

        An empty/unknown session id is never enrolled — under an opt-in
        default there is no marker that could have named it.
        """
        if not session_id:
            return False
        try:
            return self.marker_path(session_id).exists()
        except OSError:
            return False

    def enroll(self, session_id: str) -> bool:
        """Opt `session_id` in. True if newly enrolled, False if already."""
        if not session_id:
            raise ValueError("enroll requires a non-empty session id")
        marker = self.marker_path(session_id)
        if marker.exists():
            return False
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        return True

    def withdraw(self, session_id: str) -> bool:
        """Opt `session_id` back out. True if a marker was removed."""
        if not session_id:
            return False
        try:
            self.marker_path(session_id).unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return False

    def enrolled_ids(self) -> list[str]:
        """Every enrolled session id, sorted. Empty when none/no dir."""
        try:
            return sorted(p.name for p in self.dir_path().iterdir() if p.is_file())
        except OSError:
            return []


def format_status(enrollment: SessionEnrollment, session_id: str | None) -> str:
    """One-line, human-readable enrollment summary for the CLI."""
    if enrollment.is_enrolled(session_id):
        return f"autoplay: ON for this session (enrolled: {session_id})"
    count = len(enrollment.enrolled_ids())
    others = f"; {count} other session(s) enrolled" if count else ""
    if not session_id:
        return f"autoplay: OFF — no session id to enroll{others}"
    return f"autoplay: OFF for this session (opt-in required){others}"


if __name__ == "__main__":
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "") or None
    print(format_status(SessionEnrollment(), sid))
