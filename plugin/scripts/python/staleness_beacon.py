"""StalenessBeacon: a FRESH→STALE monotonic latch over a Stop beacon.

The hook touches a per-session beacon file on every Stop event and hands
the worker the beacon mtime captured at spawn time. A worker is "stale"
once the beacon's mtime advances past that start mtime — i.e. a newer
Stop in the SAME session has superseded it. The latch is monotonic: once
observed stale, the machine stays STALE.

Beacon path derivation mirrors autoplay_hook.sh / autoplay_worker.sh:
  - global default  /tmp/auto-speech-last-stop
  - per-session     /tmp/auto-speech-last-stop.<session_id>
when a session_id is present, else the legacy global beacon.

is_stale() re-stats on every call (like the bash is_stale function) so a
caller polling in a wait loop observes the beacon advancing in real time.
"""
from __future__ import annotations

from pathlib import Path

from state_machine import StateMachine

FRESH = "fresh"
STALE = "stale"

_LEGAL_NEXT: dict[str, set[str]] = {
    FRESH: {STALE},
    STALE: set(),
}

BEACON_DEFAULT = "/tmp/auto-speech-last-stop"

# The start mtime arrives as a decimal STRING from the hook (`stat -f %Fm`)
# and is compared against Python's float st_mtime for the same file. The two
# round-trips can differ by an ULP, which a bare `>` would read as a newer
# Stop. Require the beacon to have advanced by at least this much before
# declaring supersession. Far below the shortest realistic gap between two
# Stop events, so genuine staleness is still detected.
MTIME_EPSILON_SECONDS = 1e-3


def beacon_path(session_id: str | None) -> Path:
    """Per-session beacon path, or the global default when no session_id."""
    if session_id:
        return Path(f"{BEACON_DEFAULT}.{session_id}")
    return Path(BEACON_DEFAULT)


class StalenessBeacon(StateMachine):
    """Monotonic FRESH→STALE latch over a per-session Stop beacon file."""

    def __init__(self, start_mtime: float, session_id: str | None = None) -> None:
        super().__init__(FRESH, _LEGAL_NEXT, terminal=frozenset({STALE}))
        self._start_mtime = float(start_mtime)
        self._path = beacon_path(session_id)

    def _current_mtime(self) -> float:
        """Beacon mtime, or 0 if the beacon is absent (matches bash echo 0)."""
        try:
            return self._path.stat().st_mtime
        except OSError:
            return 0.0

    def is_stale(self) -> bool:
        """Re-stat the beacon; True iff a newer Stop has advanced its mtime.

        Latches FRESH→STALE the first time staleness is observed so the
        recorded state reflects the supersession permanently.
        """
        stale = self._current_mtime() > self._start_mtime + MTIME_EPSILON_SECONDS
        if stale and self.state == FRESH:
            self.transition(STALE)
        return stale
