# ADR-014 — Fire-and-forget `/api/speak` with single-active-job tracking

**Status:** Accepted
**Date:** 2026-05-06
**Depends on:** ADR-010 (web server), ADR-011 (CLI rewriter).

## Context
The web server's `/api/speak` is synchronous: the HTTP request stays
open from POST through rewrite → TTS → mpv handoff. For small inputs
this is fine (under 5 s). For large inputs (≥ 10 KB), the rewrite
alone can take 3–8 minutes thanks to Sonnet's generation speed plus
the Claude CLI's plugin-loaded startup tax. The browser shows a hung
"Speak" button the entire time, with no visible signal that anything
is happening. Users reasonably give up.

## Decision

**`/api/speak` returns immediately on cache miss with HTTP 202 and a
job descriptor.** Rewrite + TTS + mpv handoff run in the background
on the existing single-thread executor. The UI polls a refreshed
`/api/status` endpoint that now reports both mpv state AND any
in-flight or recently-completed job.

Cache **hits** stay synchronous (200) — they finish in ~50 ms and
don't benefit from indirection.

### Single-active-job constraint
At most one rewrite-and-TTS job may be in flight at any time. A
second `/api/speak` arriving while one is already `rewriting` or
`generating` returns **HTTP 409 Conflict** with a message naming the
current phase. This matches the underlying invariants:
- Only one `claude -p` should run at once (ADR-011).
- Only one mpv playback session exists (ADR-009).
- The TTS executor has `max_workers=1` already.

### Job state machine

```
                ┌──────── failed (terminal, error stored)
                │
queued → rewriting → generating → handed_off (terminal)
                                      │
                            (mpv keeps playing, but the
                             job is "done" from the
                             web server's perspective —
                             the next /api/speak is allowed)
```

Phases as machine-readable strings. The job is **complete** for the
purposes of "may another job start" once it reaches `handed_off` or
`failed`. Playback (mpv) continues in the background after handoff.

### Job descriptor
```
id            : ULID-style 16-char hex (no external dep; uuid4 hex)
phase         : "queued" | "rewriting" | "generating" | "handed_off" | "failed"
started_at    : seconds-since-epoch (UTC)
elapsed_s     : seconds elapsed in current phase (computed on read)
mode          : "rewrite" | "passthrough"
source_chars  : len(input text)
rewrite_chars : len(rewritten text), null until rewriting completes
hash          : full source hash (for cache lookup)
error         : null unless phase == "failed"; string otherwise
```

### Endpoint shapes

`POST /api/speak {text, rewrite, rewrite_timeout_s?}`:
- 200 + `{status: "cache_hit", ...}` (unchanged)
- 202 + `{status: "queued", job: {<descriptor>}}`
- 409 + `{error: "...", job: {<current job descriptor>}}`
- 400 / 500 with `{error: "..."}` (unchanged)

`GET /api/status`:
- Existing fields (`active`, `paused`, `position`, `duration`, `wav`)
- New optional field: `job` — descriptor of the current or most recent
  job. Absent when no job has run since server start.

### What the UI does
- On 202: store the `job_id`, switch the speak button to disabled,
  show `speakHint` as `"rewriting…  Ns"` updating from `/api/status`.
- On phase transition `rewriting → generating`: update hint to
  `"generating…  Ns"`.
- On phase `handed_off`: re-enable the speak button, show success
  toast with the audio duration estimate.
- On phase `failed`: re-enable, show error toast with `error` string.
- On 409: show toast with the current job's phase ("a previous request
  is still rewriting / generating").

## Rationale
1. **Honest UX for slow operations.** A user who knows the rewrite
   is taking 4 minutes can wait without staring at a frozen button.
2. **No new threads.** The existing `ThreadPoolExecutor(max_workers=1)`
   already serializes work; we're just changing when the HTTP
   response is sent (immediately) versus when the work completes
   (later).
3. **Cache hits stay fast.** Returning 202 even for cache hits
   would add an unnecessary round-trip for the common case.
4. **Single job is honest.** The TTS executor is `max_workers=1` and
   mpv is singleton — pretending to support concurrent speaks would
   either lie about completion or kill prior playback mid-rewrite.
   409 is the truthful answer.

## Alternatives considered
- **Job queue (multiple).** Adds priority/ordering UX questions for a
  single-user tool. Rejected.
- **Server-Sent Events / WebSocket for live phase transitions.**
  Cleaner than polling but adds an async server stack we've avoided
  to date. The 500 ms poll cadence we already use for mpv state
  covers job state too — same poll, more data. Rejected for now;
  revisit if status updates feel laggy.
- **Keep the synchronous interface, just bump the timeout further.**
  10 minutes was already the "reasonable upper bound" from Phase 14.
  Even at 20 minutes the user has no progress signal. Doesn't fix
  the UX, just delays the failure.

## Consequences
- New module `plugin/scripts/python/job_state.py` (Job dataclass +
  Phase constants).
- New module `plugin/scripts/python/job_tracker.py` (`JobTracker`
  class — thread-safe single-job state holder).
- `web_server.py`:
  - imports + holds a `JobTracker`.
  - `_handle_speak` becomes the queue-and-respond path; the actual
    work moves into a private `_run_speak_job` that the executor
    calls.
  - `_handle_status` includes `job` field if a job exists.
- `index.html`:
  - Speak button click handler updated for 202 + 409 responses.
  - Status poll renders job phase + elapsed time.
- The threading lock around the speak handler shrinks to "decide
  whether to accept" — once we submit the executor task, we release.
  The executor itself is the real serializer.

## Invariants (additions)
- **I-17.1 At-most-one in-flight job.** While `JobTracker.current()`
  is in `queued | rewriting | generating`, `_handle_speak` returns
  409. Cache hits bypass the JobTracker (no job created).
- **I-17.2 Phase monotonicity.** Phases advance one direction:
  `queued → rewriting → generating → handed_off`. The only branching
  transitions are `* → failed`. No backwards moves.
- **I-17.3 Error capture.** A `failed` job's `error` field is the
  human-readable cause (rewrite timeout, pipeline crash, etc.). UIs
  may display it verbatim.
- **I-17.4 mpv outlives the job.** The job is `handed_off` once the
  orchestrator returns; mpv keeps playing detached. The next
  `/api/speak` is permitted even though audio is still going — and
  the new mpv start will replace the old session per ADR-009's
  singleton invariant.

## Out of scope
- Multi-job queueing.
- Cancellation of a running rewrite (would require killing the
  `claude -p` subprocess; deferred until requested).
- Persistence of job history across server restarts.
