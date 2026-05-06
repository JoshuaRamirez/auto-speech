"""Job: immutable descriptor of a single fire-and-forget /api/speak.

Phases form a small state machine. The tracker creates a Job at
PHASE_QUEUED, then replaces it with a new Job at each transition
(immutable dataclass — phase change = new instance, same id).

See ADR-014 + docs/micro-design/phase-17-fire-and-forget-speak.md.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


PHASE_QUEUED = "queued"
PHASE_REWRITING = "rewriting"
PHASE_GENERATING = "generating"
PHASE_HANDED_OFF = "handed_off"
PHASE_FAILED = "failed"

ACTIVE_PHASES = frozenset({PHASE_QUEUED, PHASE_REWRITING, PHASE_GENERATING})
TERMINAL_PHASES = frozenset({PHASE_HANDED_OFF, PHASE_FAILED})

# Legal forward transitions (besides any → PHASE_FAILED, allowed always).
_LEGAL_NEXT = {
    PHASE_QUEUED: {PHASE_REWRITING, PHASE_GENERATING, PHASE_FAILED},
    PHASE_REWRITING: {PHASE_GENERATING, PHASE_FAILED},
    PHASE_GENERATING: {PHASE_HANDED_OFF, PHASE_FAILED},
    PHASE_HANDED_OFF: set(),
    PHASE_FAILED: set(),
}


@dataclass(frozen=True)
class Job:
    """Immutable descriptor; phase advances by replacement."""

    id: str               # 16-hex-char (uuid4().hex[:16])
    phase: str            # one of PHASE_*
    started_at: float     # epoch seconds (time.time())
    mode: str             # "rewrite" | "passthrough"
    source_chars: int
    rewrite_chars: int | None = None
    hash: str | None = None
    error: str | None = None

    def is_active(self) -> bool:
        return self.phase in ACTIVE_PHASES

    def is_terminal(self) -> bool:
        return self.phase in TERMINAL_PHASES

    def to_dict(self) -> dict:
        return asdict(self)


def is_legal_transition(from_phase: str, to_phase: str) -> bool:
    """True iff `from_phase` may move to `to_phase` per the state machine."""
    return to_phase in _LEGAL_NEXT.get(from_phase, set())
