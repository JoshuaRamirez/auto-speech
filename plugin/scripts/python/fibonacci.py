"""FibonacciSeq: on-demand Fibonacci generator (F(1)=F(2)=1, F(3)=2, ...)."""
from __future__ import annotations


class FibonacciSeq:
    """Stateful generator of Fibonacci numbers, 1-indexed starting at 1, 1, 2, 3..."""

    def __init__(self) -> None:
        self._a = 1
        self._b = 1
        self._emitted = 0

    def next_target(self) -> int:
        self._emitted += 1
        if self._emitted == 1:
            return self._a
        if self._emitted == 2:
            return self._b
        nxt = self._a + self._b
        self._a = self._b
        self._b = nxt
        return nxt

    @property
    def emitted_count(self) -> int:
        return self._emitted
