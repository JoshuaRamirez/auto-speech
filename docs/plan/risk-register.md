# Risk Register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | mlx-audio install fails on user's Python/macOS version | Medium | Blocks all phases | Pin a known-working version in `install.sh`; document Python 3.11+ requirement; provide a pip fallback if `uv` is unavailable. |
| R2 | Kokoro's measured chars/sec varies by voice and speed settings in ways that break chunk-size estimates | Medium | Low — manifests as over-/under-sized chunks | Calibration is per-voice; recalibration is cheap. Boundary tolerance (±25%) absorbs modest drift. |
| R3 | Claude Code transcript JSONL schema changes in a future update | Low | Blocks `/speak` until locator updated | Schema parsing uses the minimal fields (`role`, `content`, `type: "text"`). Test against current schema; if it breaks, the fix is localized to `transcript_reader.py`. |
| R4 | Cwd-slug convention for transcript path changes | Low | Blocks `TranscriptLocator` | Locator also falls back to env-var-based session id if present; both paths tested. |
| R5 | Fibonacci growth outpaces generator on the rare occasion of a very long chunk (k ≥ 10) | Low | User hears a brief silence mid-response | Kokoro's ~5× realtime margin plus file-then-enqueue discipline makes this very unlikely. If observed, cap growth at an empirical maximum (e.g., chunk ≤ 60 s) by truncating the Fibonacci sequence past a fixed k. |
| R6 | The rewrite step loses information despite prompt guidance | Medium | Violates core requirement (lossless) | The prompt contract is explicit about inclusion. Manual spot-check in Phase 9. If evidence of lossy rewrites accumulates, escalate prompt strictness or add a second pass. |
| R7 | afplay stalls or errors mid-sequence | Low | Partial playback | Consumer detects nonzero exit → orchestrator halts with a clear error. No silent recovery. |
| R8 | Orphaned tmpdirs accumulate in `/tmp/` across failed runs | Low | Disk clutter | Orchestrator cleans tmpdirs on success. Failed runs preserve them deliberately for debugging; periodic cleanup is a user chore — documented in README. |
| R9 | First-chunk latency exceeds the 6 s NFR because of Kokoro cold-start model load | Medium | NFR missed on first invocation after boot | Consider a tiny pre-warm script (optional) that loads Kokoro once after login. Document as a known condition; not a blocking defect. |
| R10 | User runs `/speak` during Claude's in-flight response | Low | Selects the wrong message | Slash commands in Claude Code can only execute when the user has control, so by construction the in-flight response is not yet in the transcript. Document this as a non-bug. |
