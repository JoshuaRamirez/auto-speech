"""Which enrolment markers belong to a live session (ADR-024).

An autoplay marker (~/.claude/auto-speech-autoplay-enabled/<id>) or a
narrate marker (~/.claude/auto-speech-narrate-sessions/<id>) says a session
is meant to speak. A marker outlives its session: on 2026-10-21, 12 narrate
markers and 2 autoplay markers were from 2 to 5 October with no live
session behind them. A marker counts only while its session is alive.

Alive, as the daemon can tell: the session's transcript
(~/.claude/projects/<project>/<id>.jsonl) was written in the last 24 hours,
or the marker itself is younger than that (a session that has just enrolled
may not have a transcript yet). A hook that fires is itself a live session,
so the shell hooks keep their plain marker check; the daemon, which acts on
events after the fact, and the Python readers apply this rule.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

LIVE_WINDOW_S = 24 * 3600
CACHE_S = 60.0  # a transcript lookup reads one directory level; cache it per session
MARKER_DIRS = ("auto-speech-autoplay-enabled", "auto-speech-narrate-sessions")


@dataclass(frozen=True)
class StaleMarker:
    kind: str  # the marker directory's name
    session_id: str
    path: Path
    age_s: float  # how long since the marker was written


class SessionLiveness:
    def __init__(
        self,
        home: Path | None = None,
        *,
        clock: Callable[[], float] = time.time,
        window_s: float = LIVE_WINDOW_S,
    ) -> None:
        self._home = Path(home) if home is not None else Path.home()
        self._clock = clock
        self._window_s = float(window_s)
        self._cache: dict[str, tuple[float, bool]] = {}

    @property
    def projects_dir(self) -> Path:
        return self._home / ".claude" / "projects"

    def transcript_recent(self, session_id: str) -> bool:
        """True when a transcript for the session was written in the window.
        Reads one level of the projects folder: <project>/<id>.jsonl."""
        if not session_id or "/" in session_id or session_id.startswith("."):
            return False
        now = self._clock()
        hit = self._cache.get(session_id)
        if hit is not None and now - hit[0] < CACHE_S:
            return hit[1]
        recent = False
        try:
            for transcript in self.projects_dir.glob(f"*/{session_id}.jsonl"):
                try:
                    if now - transcript.stat().st_mtime < self._window_s:
                        recent = True
                        break
                except OSError:
                    continue
        except OSError:
            recent = False
        self._cache[session_id] = (now, recent)
        return recent

    def alive(self, marker: Path, session_id: str | None = None) -> bool:
        """Whether the session behind a marker is alive: the marker is young,
        or the session's transcript was written lately."""
        session_id = session_id or marker.name
        try:
            if self._clock() - marker.stat().st_mtime < self._window_s:
                return True
        except OSError:
            return False
        return self.transcript_recent(session_id)

    def stale(self) -> list[StaleMarker]:
        """Every enrolment marker whose session is not alive, oldest first."""
        out: list[StaleMarker] = []
        now = self._clock()
        for kind in MARKER_DIRS:
            d = self._home / ".claude" / kind
            if not d.is_dir():
                continue
            for marker in d.iterdir():
                try:
                    if not marker.is_file():
                        continue
                    age = now - marker.stat().st_mtime
                except OSError:
                    continue
                if not self.alive(marker, marker.name):
                    out.append(StaleMarker(kind, marker.name, marker, age))
        out.sort(key=lambda m: -m.age_s)
        return out
