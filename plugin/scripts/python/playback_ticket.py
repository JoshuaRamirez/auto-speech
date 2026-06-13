"""PlaybackTicketMachine: one worker's slot in the FIFO playback queue.

A ticket records arrival order. Its lifecycle:

    QUEUED → HEAD → PLAYING → RELEASED

QUEUED    enqueued, an older live ticket may be ahead
HEAD      observed to be the oldest live ticket (our turn)
PLAYING   now-playing marker written, speak invoked
RELEASED  terminal: ticket file removed on process exit

The state is the local view; the cross-process truth is the set of
ticket files in the queue directory. This machine guards the local
sequencing and makes the worker's queue position assertable.
"""
from __future__ import annotations

from state_machine import StateMachine

QUEUED = "queued"
HEAD = "head"
PLAYING = "playing"
RELEASED = "released"

_LEGAL_NEXT: dict[str, set[str]] = {
    QUEUED: {HEAD, RELEASED},   # RELEASED = bailed/exited before our turn
    HEAD: {PLAYING, RELEASED},
    PLAYING: {RELEASED},
    RELEASED: set(),
}


class PlaybackTicketMachine(StateMachine):
    """FIFO ticket lifecycle FSM. Starts QUEUED; ends RELEASED."""

    def __init__(self) -> None:
        super().__init__(QUEUED, _LEGAL_NEXT, terminal=frozenset({RELEASED}))
