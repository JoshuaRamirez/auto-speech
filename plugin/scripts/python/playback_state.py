"""PlaybackStateMachine: the mpv singleton playback lifecycle FSM.

Models one process's view of the mpv playback session. The cross-process
truth lives in SessionDir; this is an explicit, assertable record of the
local lifecycle and a guard against illegal internal sequencing.

Cycle (no terminal state):

    IDLE → STARTING → READY → STOPPING → IDLE

with two short-circuit edges: STARTING → IDLE (startup failed/aborted)
and READY → IDLE (the mpv process exited on its own).
"""
from __future__ import annotations

from state_machine import StateMachine

IDLE = "idle"
STARTING = "starting"
READY = "ready"
STOPPING = "stopping"

_LEGAL_NEXT: dict[str, set[str]] = {
    IDLE: {STARTING},
    STARTING: {READY, IDLE},      # READY = socket answered; IDLE = startup failed
    READY: {STOPPING, IDLE},      # STOPPING = we quit it; IDLE = it exited itself
    STOPPING: {IDLE},
}


class PlaybackStateMachine(StateMachine):
    """mpv playback lifecycle FSM. Starts at IDLE; cycles forever."""

    def __init__(self) -> None:
        super().__init__(IDLE, _LEGAL_NEXT)
