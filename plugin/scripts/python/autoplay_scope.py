"""SoloScope: which session, if any, holds the autoplay "spotlight".

Autoplay is OFF unless a session enrolls (SessionEnrollment +
autoplay_hook.sh). SoloScope narrows further, orthogonally: when the
spotlight marker is present it names exactly ONE session id that is
allowed to play; every other session is muted for the duration. Absent
marker => every ENROLLED session plays.

  Marker: ~/.claude/auto-speech-autoplay-solo   (contents = session id)

The marker is global state. The per-session decision ("am I allowed to
play?") is made wherever per-session gating already happens —
autoplay_hook.sh — so the detached worker keeps its minimal single-check
scope. This module is the testable MODEL of that decision and the writer
used by the /auto-speech-scope command. `home` is overridable for tests.
"""
from __future__ import annotations

import os
from pathlib import Path

ALL = "all"
SOLO = "solo"

MARKER_NAME = "auto-speech-autoplay-solo"


class SoloScope:
    """Reads/writes the autoplay spotlight marker and classifies scope."""

    def __init__(self, home: Path | None = None) -> None:
        self._home = Path(home) if home is not None else Path(os.path.expanduser("~"))

    def marker_path(self) -> Path:
        return self._home / ".claude" / MARKER_NAME

    def current(self) -> str | None:
        """The session id holding the spotlight, or None when scope is ALL."""
        try:
            text = self.marker_path().read_text(encoding="utf-8").strip()
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            return None
        except OSError:
            return None
        return text or None

    def mode(self) -> str:
        """ALL when no spotlight is set, else SOLO."""
        return SOLO if self.current() is not None else ALL

    def session_allowed(self, session_id: str | None) -> bool:
        """True if `session_id` may play under the current scope.

        ALL scope => always True. SOLO scope => True only for the soloed
        session. An empty/unknown session id is NOT muted under SOLO: we
        cannot prove it is the wrong session, and silencing an
        unidentifiable session risks total silence (e.g. jq missing in the
        hook), so it is allowed.
        """
        held = self.current()
        if held is None:
            return True
        if not session_id:
            return True
        return session_id == held

    def set_solo(self, session_id: str) -> None:
        """Claim the spotlight for `session_id` (atomic replace)."""
        if not session_id:
            raise ValueError("set_solo requires a non-empty session id")
        marker = self.marker_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        tmp = marker.with_name(marker.name + ".tmp")
        tmp.write_text(session_id, encoding="utf-8")
        os.replace(tmp, marker)

    def clear(self) -> None:
        """Return to ALL scope (remove the marker if present)."""
        try:
            self.marker_path().unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def format_status(scope: SoloScope, session_id: str | None) -> str:
    """One-line, human-readable scope summary for the CLI / status script."""
    held = scope.current()
    if held is None:
        return "autoplay scope: ALL — every session reads (default)"
    if session_id and held == session_id:
        return f"autoplay scope: SOLO — only THIS session reads (spotlight={held})"
    return (
        f"autoplay scope: SOLO — only session {held} reads; "
        "this session is muted"
    )


if __name__ == "__main__":
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "") or None
    print(format_status(SoloScope(), sid))
