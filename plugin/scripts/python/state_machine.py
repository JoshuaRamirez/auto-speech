"""StateMachine: the shared finite-state-machine foundation.

This generalizes the idiom established in job_state.py (string state
constants, ACTIVE/TERMINAL frozensets, a _LEGAL_NEXT transition table,
and is_legal_transition()). job_state.py predates this module and uses
the same shape; the new lifecycle machines (playback, narrator) and the
refactored JobTracker build on this class so transition legality is
defined in exactly one place per machine.

Tiny and dependency-free. Thread-safe via an internal lock so concurrent
callers cannot interleave a read of the current state with a write.
"""
from __future__ import annotations

import threading


class IllegalTransition(Exception):
    """Raised when a transition is not permitted by the machine's table."""


class StateMachine:
    """A thread-safe finite state machine over string states.

    Construct with an initial state, a transition table mapping each
    state to the set of states it may move to, and an optional frozenset
    of terminal states (states with no legal exits, by convention).
    """

    def __init__(
        self,
        initial: str,
        transitions: dict[str, set[str]],
        terminal: frozenset[str] = frozenset(),
    ) -> None:
        self._lock = threading.Lock()
        self._state = initial
        self._transitions = transitions
        self._terminal = terminal

    @property
    def state(self) -> str:
        """The current state."""
        with self._lock:
            return self._state

    def can(self, to: str) -> bool:
        """True iff a transition from the current state to `to` is legal."""
        with self._lock:
            return to in self._transitions.get(self._state, set())

    def transition(self, to: str) -> str:
        """Move to `to`, returning the new state.

        Raises IllegalTransition (with a clear `from → to` message) if
        the move is not permitted by the transition table.
        """
        with self._lock:
            if to not in self._transitions.get(self._state, set()):
                raise IllegalTransition(
                    f"illegal transition {self._state!r} → {to!r}"
                )
            self._state = to
            return to

    def is_terminal(self) -> bool:
        """True iff the current state is in the terminal set."""
        with self._lock:
            return self._state in self._terminal
