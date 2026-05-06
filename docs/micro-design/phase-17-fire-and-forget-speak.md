# Phase 17 Micro-Design — Fire-and-forget `/api/speak` + job tracker

Implements [ADR-014](../decisions/ADR-014-fire-and-forget-speak.md).

## Scope
Move the rewrite + TTS work off the HTTP request thread. POST returns
202 with a job descriptor; the UI polls `/api/status` (now extended
with a `job` field) for phase and elapsed time. Cache hits stay
synchronous. Single active job — second concurrent POST gets 409.

## M1 — Classes at this level

| Artifact | Role |
|---|---|
| `plugin/scripts/python/job_state.py` | `Job` (frozen dataclass) + `Phase` constants module. |
| `plugin/scripts/python/job_tracker.py` | `JobTracker` — thread-safe single-job state holder; assigns IDs, mutates phase. |

Edits:
- `plugin/scripts/python/web_server.py` — refactor `_handle_speak`, add
  background runner, extend `_handle_status`.
- `plugin/web/templates/index.html` — UI handles 202 + 409 responses
  and renders the new `job` field from `/api/status`.

## M2 — Semantics

### `job_state.py`
```
PHASE_QUEUED      = "queued"
PHASE_REWRITING   = "rewriting"
PHASE_GENERATING  = "generating"
PHASE_HANDED_OFF  = "handed_off"
PHASE_FAILED      = "failed"

ACTIVE_PHASES   = {PHASE_QUEUED, PHASE_REWRITING, PHASE_GENERATING}
TERMINAL_PHASES = {PHASE_HANDED_OFF, PHASE_FAILED}

@dataclass(frozen=True)
class Job:
    id: str                # 16-hex-char (uuid4().hex[:16])
    phase: str             # one of PHASE_*
    started_at: float      # time.time()
    mode: str              # "rewrite" | "passthrough"
    source_chars: int
    rewrite_chars: int | None = None
    hash: str | None = None
    error: str | None = None
```

`Job` is immutable — phase transitions create a new `Job` with the
new phase. The tracker swaps the reference.

### `job_tracker.py`
```
class JobTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: Job | None = None

    def current(self) -> Job | None
    def is_active(self) -> bool        # current and current.phase in ACTIVE_PHASES
    def begin(self, mode, source_chars, hash) -> Job
        # creates a Job at PHASE_QUEUED with new id; replaces current
    def transition(self, new_phase: str, **fields) -> Job | None
        # mutates current to (new_phase, **fields); returns new Job
    def fail(self, error: str) -> Job | None
        # transition to PHASE_FAILED with error
```

All public methods take `self._lock` so concurrent reads/writes are
safe with the Flask thread pool.

### `web_server.py` — `_handle_speak`
Refactored flow:

```
1. Parse body, validate, compute hash, mode.
2. with self._lock:
     a. If cache hit → start mpv → return 200 (existing behavior).
     b. Else if JobTracker.is_active() → return 409 with current job.
     c. Else: tracker.begin(mode, source_chars, hash).
3. Submit `_run_speak_job(text, audio_text=None, ...)` to executor.
4. Return 202 with job descriptor.
```

The submitted callable runs on the executor's single worker:

```
def _run_speak_job(self, text, mode, source_hash, rewrite_timeout):
    try:
        if mode == "rewrite":
            tracker.transition(PHASE_REWRITING)
            audio_text = rewriter.rewrite(text, timeout_seconds=rewrite_timeout)
            tracker.transition(PHASE_GENERATING, rewrite_chars=len(audio_text))
        else:
            audio_text = text
            tracker.transition(PHASE_GENERATING, rewrite_chars=len(text))
        orch = PipelineOrchestrator(source_hash=source_hash, ...)
        rc = orch.run(audio_text)
        if rc != EXIT_OK:
            tracker.fail(f"pipeline exited {rc}")
            return
        tracker.transition(PHASE_HANDED_OFF)
    except ClaudeCliRewriteError as exc:
        tracker.fail(f"rewrite failed: {exc}")
    except Exception as exc:
        tracker.fail(f"crash: {exc!r}")
```

### `web_server.py` — `_handle_status`
Existing fields unchanged. New optional `job` field:

```python
job = self._jobs.current()
payload["job"] = (
    None if job is None else {
        "id": job.id,
        "phase": job.phase,
        "started_at": job.started_at,
        "elapsed_s": time.time() - job.started_at,
        "mode": job.mode,
        "source_chars": job.source_chars,
        "rewrite_chars": job.rewrite_chars,
        "error": job.error,
    }
)
```

### `index.html` UI changes
- On Speak click:
  - 200 (`status: cache_hit`) → existing toast + status update.
  - 202 (`status: queued`) → store `job_id`; disable button; switch
    polling to 500 ms; hint becomes `"queued…"`.
  - 409 → toast: "in flight: <phase>"; do not disable.
  - Other → existing error path.
- Status poll updates the speak hint based on `job.phase`:
  - `queued` → `"queued · 1s"`
  - `rewriting` → `"rewriting · Ns"`
  - `generating` → `"generating · Ns"`
  - `handed_off` → re-enable button; hint clears (or shows
    "spoke N chars · ~M:SS").
  - `failed` → re-enable button; toast with `error`; hint clears.
- The "is the job ours?" check uses the stored `job_id` from the
  POST response — if a *newer* job has started (different id), the
  UI doesn't claim ownership.

## M3 — Relationships

```
HTTP POST /api/speak
   │
   ├── synchronous path (cache hit) ── start mpv ── 200
   │
   └── miss path:
         ├── tracker.begin → Job(QUEUED)
         ├── executor.submit(_run_speak_job)
         └── return 202 + job

executor worker thread:
   ├── tracker.transition(REWRITING)
   ├── rewriter.rewrite(...)               (may take minutes)
   ├── tracker.transition(GENERATING, rewrite_chars=...)
   ├── PipelineOrchestrator.run(audio_text)
   ├── tracker.transition(HANDED_OFF)
   └── (mpv keeps playing; job is done)

GET /api/status
   ├── reads SessionDir + mpv IPC for playback fields
   └── reads tracker.current() for job field
```

The job lives entirely in memory. No persistence.

## M4 — Implementation details

### Concurrency
- `WebServer._lock` (existing) protects the cache-check + tracker-begin
  decision. Released before submitting the job.
- `JobTracker._lock` (new) protects all mutations to `_current`.
- `ThreadPoolExecutor(max_workers=1)` (existing) is the actual
  serializer. The lock-then-release pattern just prevents two
  requests from both passing the "is_active" check.

### Job ID
`uuid.uuid4().hex[:16]`. 64 bits is overkill for a single user but
consistent with our other identifiers (cache prefix length).

### Cache short-circuit and tracker
Cache hits do **NOT** create a Job. From the user's perspective
they're synchronous and instant; no need to clutter the job log.
This means `JobTracker.current()` may be in `handed_off` state when
`/api/status` is queried — the UI should ignore terminal-state jobs
that are stale (e.g., older than the current page's `job_id`).

### UI ownership tracking
JS keeps `lastJobId` set when the POST returns 202. The status poll
checks `j.job?.id === lastJobId` before re-enabling the button on
`handed_off`. This avoids races: if the page is reloaded mid-job and
a NEW job starts, the new tab sees the new id from its own POST.

### Failure messages
The `error` field on a failed `Job` is the message we already render
into 500 responses today (e.g., `"claude rewrite timed out after 600s"`).
Same string, surfaced via status poll instead of HTTP body.

## Failure modes

| Mode | Handling |
|---|---|
| Rewrite timeout | Job → `failed` with `"claude rewrite timed out after Ns"`. |
| Pipeline crashes | Job → `failed` with `"crash: <repr>"`. Server log gets full traceback (existing). |
| Pipeline returns non-zero | Job → `failed` with `"pipeline exited N"`. |
| `claude` not on PATH | Job → `failed` with the existing `ClaudeCliUnavailable` message. |
| Concurrent POST while job active | 409 with current job descriptor; no new work. |
| Concurrent POST while job in terminal state | New job created; old descriptor overwritten. |

## Invariants (beyond ADR-014's)

- **I-17.5 Tracker monotonicity.** A job's ID never changes; only its
  phase + ancillary fields advance. `JobTracker.transition` MUST be
  called with a phase that follows the state machine — the tracker
  asserts this and raises `ValueError` on illegal moves.
- **I-17.6 Lock ordering.** Always acquire `WebServer._lock` BEFORE
  `JobTracker._lock` if both are needed. The tracker releases its
  own lock before any callable runs.

## Check gate

1. **Cache hit unchanged.** POST a known-cached source → 200,
   `status: cache_hit`, no job descriptor (or terminal one from
   prior).
2. **Cache miss queues.** POST a fresh small source with rewrite=true
   → 202, `status: queued`, job at `phase: queued|rewriting`.
3. **Status reflects phase.** Within a few seconds, GET /api/status
   shows job phase progressing from `rewriting` → `generating` →
   `handed_off`.
4. **409 on second concurrent POST.** While the job is `rewriting` or
   `generating`, a second POST with different text returns 409 with
   the current job descriptor.
5. **Failure path.** Force an error (e.g., kill `claude`'s process
   mid-rewrite) → job → `failed` with `error` populated.
6. **Cache miss without rewrite (passthrough) still queues.**
7. **UI integration:** UI hint reads `"rewriting · Ns"` while job
   active; button stays disabled; toast shows on completion.

## Out of scope
- Cancellation of a running rewrite.
- Multi-job queueing.
- Persistence across server restarts.
- WebSocket / SSE for push-style updates.
