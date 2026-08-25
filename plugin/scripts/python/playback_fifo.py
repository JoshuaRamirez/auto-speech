"""PlaybackFifo: the strict cross-session FIFO playback queue.

Note on naming: an unrelated PlaybackQueue (a bounded segment buffer for
the TTS pipeline) already exists in playback_queue.py. This FIFO ticket
queue — the cross-session arrival-order arbiter for the autoplay worker —
is named PlaybackFifo to avoid colliding with that established class.

Playbacks must NEVER cut each other off. Each worker enqueues a ticket
(sortable nanosecond-timestamp filename, owner pid inside the file) and
proceeds to speak only when its ticket is the oldest LIVE one AND mpv is
idle AND the narrator FIFO is drained. The ticket is held until the
worker process exits so arrival order survives the multi-second rewrite
step.

Lives OUTSIDE /tmp/auto-speech (which SessionDir.clear() rmtree's) at
/tmp/auto-speech-playback-queue. Direct port of the enqueue_ticket /
ticket_is_head / wait_for_queue_turn helpers in autoplay_worker.sh.

Ticket release is wired to atexit AND SIGTERM/SIGINT so a normal exit or
a signalled shutdown always removes the ticket — matching the bash EXIT
trap. A kill -9 (no trap) leaves an orphan ticket; ticket_is_head()
garbage-collects such tickets whose owner pid is dead, exactly as bash.
"""
from __future__ import annotations

import atexit
import os
import signal
import time
from pathlib import Path

from playback_ticket import HEAD, PLAYING, RELEASED, PlaybackTicketMachine

QUEUE_DIR = Path("/tmp/auto-speech-playback-queue")
POLL_SECONDS = 0.5


def _pid_alive(pid: int) -> bool:
    """kill -0 equivalent: True iff the process exists and is signalable."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user — still alive
    except OSError:
        return False


class PlaybackFifo:
    """Strict FIFO playback queue with dead-owner garbage collection.

    The wait loop is driven by injected `mpv_running` / `narrator_depth`
    / `is_stale` callables so it can be exercised deterministically in
    tests without real mpv, narrator, or beacon state.
    """

    def __init__(
        self,
        pid: int | None = None,
        queue_dir: Path = QUEUE_DIR,
        log=None,
    ) -> None:
        self._pid = pid if pid is not None else os.getpid()
        self._dir = Path(queue_dir)
        self._ticket: Path | None = None
        self._machine = PlaybackTicketMachine()
        self._log = log or (lambda msg: None)

    @property
    def ticket(self) -> Path | None:
        return self._ticket

    @property
    def machine(self) -> PlaybackTicketMachine:
        return self._machine

    def enqueue(self) -> Path:
        """Create our ticket file (nanosecond stamp + pid) and arm release."""
        self._dir.mkdir(parents=True, exist_ok=True)
        stamp = f"{time.time_ns():020d}"
        self._ticket = self._dir / f"{stamp}.{self._pid}"
        self._ticket.write_text(f"{self._pid}\n", encoding="utf-8")
        # Always remove the ticket on a normal or signalled exit, matching
        # the bash `trap 'remove_ticket' EXIT`.
        atexit.register(self.release)
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, self._on_signal)
            except (ValueError, OSError):
                pass  # not main thread / unsupported — atexit covers normal exit
        self._log(f"enqueued ticket {self._ticket.name}")
        return self._ticket

    def _on_signal(self, signum, frame) -> None:
        self.release()
        # Restore default disposition and re-raise so the process terminates.
        signal.signal(signum, signal.SIG_DFL)
        os.kill(self._pid, signum)

    def release(self) -> None:
        """Remove our ticket file (idempotent) and mark RELEASED."""
        if self._ticket is not None:
            try:
                self._ticket.unlink()
            except OSError:
                pass
        if not self._machine.is_terminal():
            self._machine.transition(RELEASED)

    def ticket_is_head(self) -> bool:
        """True iff our ticket is the oldest LIVE ticket in the queue.

        Iterates tickets in lexicographic (== chronological) order. A
        ticket older than ours whose owner pid is dead is garbage-collected
        in place so a crashed worker cannot wedge the queue. Returns True
        on an empty queue (we must have been cleaned up — proceed), exactly
        like the bash helper.
        """
        try:
            entries = sorted(self._dir.iterdir())
        except OSError:
            return True  # dir gone — proceed
        if not entries:
            return True
        for t in entries:
            if self._ticket is not None and t == self._ticket:
                return True
            try:
                owner_raw = t.read_text(encoding="utf-8").strip()
            except OSError:
                owner_raw = ""
            owner = None
            if owner_raw:
                try:
                    owner = int(owner_raw)
                except ValueError:
                    owner = None
            if owner is None or not _pid_alive(owner):
                try:
                    t.unlink()
                except OSError:
                    pass
                continue
            return False  # an older live ticket is ahead of us
        return True

    def wait_for_queue_turn(
        self,
        cap_seconds: float,
        mpv_running,
        narrator_depth,
        is_stale,
        sleep=time.sleep,
    ) -> bool:
        """Block until our turn: head of queue, mpv idle, narrator drained.

        Polls every 0.5s for up to cap_seconds*2 iterations (the exact bash
        timing contract). Never kills anything. Returns:
          True  — proceed (turn arrived, or cap expired → proceed anyway)
          False — staled out mid-wait (newer Stop in our session)

        `mpv_running` / `narrator_depth` / `is_stale` are zero-arg callables
        re-evaluated each poll, mirroring the per-iteration re-stat in bash.
        """
        waited = 0
        last_mpv = False
        last_depth = 0
        while waited < cap_seconds * 2:
            last_mpv = bool(mpv_running())
            last_depth = int(narrator_depth())
            if not last_mpv and last_depth == 0 and self.ticket_is_head():
                self._advance_head()
                return True
            sleep(POLL_SECONDS)
            waited += 1
            if is_stale():
                self._log(
                    f"stale while queued (mpv={int(last_mpv)} depth={last_depth}); "
                    "bailing (newer worker queued behind)"
                )
                return False
        self._log(
            f"queue wait hit cap ({cap_seconds}s, mpv={int(last_mpv)} "
            f"depth={last_depth}); proceeding anyway (never killing)"
        )
        self._advance_head()  # cap expiry proceeds
        return True

    def _advance_head(self) -> None:
        if self._machine.state != HEAD and not self._machine.is_terminal():
            self._machine.transition(HEAD)

    def mark_playing(self) -> None:
        """Advance the ticket to PLAYING (now-playing marker about to write)."""
        if self._machine.state == HEAD:
            self._machine.transition(PLAYING)
