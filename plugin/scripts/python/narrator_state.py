"""NarratorStateMachine: the narrator daemon lifecycle FSM.

Records and guards the daemon's lifecycle transitions. NOT_RUNNING is
the rest state; the daemon leaves it on startup and returns to it on
either shutdown path.

Lifecycle:

    NOT_RUNNING → STARTING → RUNNING → IDLE_SHUTDOWN   → NOT_RUNNING
                                     ↘ SIGNAL_SHUTDOWN ↗

with STARTING → NOT_RUNNING for a failed start.
"""
from __future__ import annotations

from state_machine import StateMachine

NOT_RUNNING = "not_running"
STARTING = "starting"
RUNNING = "running"
IDLE_SHUTDOWN = "idle_shutdown"
SIGNAL_SHUTDOWN = "signal_shutdown"

_LEGAL_NEXT: dict[str, set[str]] = {
    NOT_RUNNING: {STARTING},
    STARTING: {RUNNING, NOT_RUNNING},   # NOT_RUNNING = start failed
    RUNNING: {IDLE_SHUTDOWN, SIGNAL_SHUTDOWN},
    IDLE_SHUTDOWN: {NOT_RUNNING},
    SIGNAL_SHUTDOWN: {NOT_RUNNING},
}


class NarratorStateMachine(StateMachine):
    """Narrator daemon lifecycle FSM. Starts at NOT_RUNNING (rest state)."""

    def __init__(self) -> None:
        super().__init__(NOT_RUNNING, _LEGAL_NEXT)
