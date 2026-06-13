"""WorkerLifecycleMachine: the autoplay worker's explicit lifecycle FSM.

Replaces the implicit guard-and-bail sequencing of autoplay_worker.sh
with an assertable record of where the worker is in its run. Every
early-exit path the bash worker took now corresponds to an explicit
transition into BAILED; the success path walks the full chain through
SPEAKING into DONE.

Lifecycle (success path):

    SPAWNED → COALESCING → QUEUED → RESOLVING → AWAITING_TURN
            → SPEAKING → DONE

BAILED is reachable from every pre-playback state (each bash early-exit).
DONE is reachable ONLY from SPEAKING: the worker cannot declare itself
done without having passed through the queue turn and a speak attempt.
Terminal set = {DONE, BAILED}.
"""
from __future__ import annotations

from state_machine import StateMachine

SPAWNED = "spawned"            # process just started; pre-disable-check
COALESCING = "coalescing"      # sleeping the coalesce window
QUEUED = "queued"              # ticket enqueued in the FIFO playback queue
RESOLVING = "resolving"        # extract + min-len + hash + cache decision
AWAITING_TURN = "awaiting_turn"  # waiting for head-of-queue + mpv idle
SPEAKING = "speaking"          # now-playing marker written; speak invoked
DONE = "done"                  # terminal: speak attempt completed
BAILED = "bailed"              # terminal: an early-exit condition fired

_LEGAL_NEXT: dict[str, set[str]] = {
    SPAWNED: {COALESCING, BAILED},        # disable marker → BAILED
    COALESCING: {QUEUED, BAILED},         # stale during coalesce → BAILED
    QUEUED: {RESOLVING, BAILED},          # extract/min-len/hash fail → BAILED
    RESOLVING: {AWAITING_TURN, BAILED},   # stale/dedup/empty rewrite → BAILED
    AWAITING_TURN: {SPEAKING, BAILED},    # staled out mid-wait → BAILED
    SPEAKING: {DONE},                     # speak attempt always lands DONE
    DONE: set(),
    BAILED: set(),
}


class WorkerLifecycleMachine(StateMachine):
    """Autoplay worker lifecycle FSM. Starts at SPAWNED; ends terminal."""

    def __init__(self) -> None:
        super().__init__(SPAWNED, _LEGAL_NEXT, terminal=frozenset({DONE, BAILED}))
