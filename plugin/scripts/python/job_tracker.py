"""JobTracker: thread-safe single-job state holder.

Owned by WebServer (Phase 17). Allows at most one in-flight job; the
HTTP layer uses `is_active()` to decide whether a new POST returns
202 (queue) or 409 (busy). All mutators take an internal lock so
concurrent reads/writes from Flask worker threads and the TTS
executor thread are race-free.
"""
from __future__ import annotations

import threading
import time
import uuid

from job_state import (
    ACTIVE_PHASES,
    PHASE_FAILED,
    PHASE_QUEUED,
    Job,
    is_legal_transition,
)


class JobTracker:
    """Single in-memory slot for the current/most-recent Job."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: Job | None = None

    def current(self) -> Job | None:
        with self._lock:
            return self._current

    def is_active(self) -> bool:
        with self._lock:
            return self._current is not None and self._current.phase in ACTIVE_PHASES

    def begin(
        self,
        mode: str,
        source_chars: int,
        source_hash: str | None,
    ) -> Job:
        """Create a new Job at PHASE_QUEUED. Replaces any prior terminal job.

        The caller is expected to have checked `is_active()` first; if a
        prior job is still active this raises RuntimeError to surface the
        race rather than silently clobber it.
        """
        with self._lock:
            if self._current is not None and self._current.phase in ACTIVE_PHASES:
                raise RuntimeError(
                    f"cannot begin: job {self._current.id} still in phase "
                    f"{self._current.phase}"
                )
            job = Job(
                id=uuid.uuid4().hex[:16],
                phase=PHASE_QUEUED,
                started_at=time.time(),
                mode=mode,
                source_chars=source_chars,
                hash=source_hash,
            )
            self._current = job
            return job

    def transition(self, new_phase: str, **fields) -> Job:
        """Advance the current job to `new_phase`, optionally setting fields.

        Raises RuntimeError if no current job; ValueError on illegal
        transition. Returns the new Job instance.
        """
        with self._lock:
            cur = self._current
            if cur is None:
                raise RuntimeError(f"transition({new_phase!r}) with no current job")
            if not is_legal_transition(cur.phase, new_phase):
                raise ValueError(
                    f"illegal phase transition {cur.phase!r} → {new_phase!r}"
                )
            data = cur.to_dict()
            data["phase"] = new_phase
            data.update(fields)
            new_job = Job(**data)
            self._current = new_job
            return new_job

    def fail(self, error: str) -> Job:
        """Move current job to PHASE_FAILED with the given error string."""
        with self._lock:
            cur = self._current
            if cur is None:
                raise RuntimeError(f"fail({error!r}) with no current job")
            data = cur.to_dict()
            data["phase"] = PHASE_FAILED
            data["error"] = error
            new_job = Job(**data)
            self._current = new_job
            return new_job
