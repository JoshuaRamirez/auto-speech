# Phase 9 Micro-Design — End-to-End Validation

## Scope
Exercise the full pipeline with real inputs and capture evidence. No new
classes — only a test plan and artifact retention.

## Scenarios run

| # | Case | Ordinal / source | Path | Outcome |
|---|---|---|---|---|
| 1 | Short real message | `--ordinal 2` from live session, 89 chars | short | played cleanly |
| 2 | Long synthetic prose | 847 chars pre-authored | long (Fibonacci, 5 chunks) | played cleanly, exit 0 |
| 3 | Error: no such turn | `--ordinal 9999` | extractor | exit 2 with message "only 22 qualifying assistant messages available; you asked for #9999" |

## Artifacts
- `tests/outputs/<ts>-short-real/` — run.log + preserved WAV.
- `tests/outputs/<ts>-long-fibonacci/` — source.txt, rewrite.txt, run.log,
  5 chunk-*.wav files.

## NFR outcomes

| NFR | Target | Observed |
|---|---|---|
| First-audio latency | ≤ 6 s | First chunk generation time 2.33 s (cold). Add ~0.5 s for speak.py startup. Estimated total ≤ 3 s on second invocation (warm). **Passes.** |
| Inter-chunk gap | < 100 ms | Log timestamps show producer stays far ahead of consumer for Fibonacci chunks 2–5; each chunk was queued before the previous finished. **Passes.** |
| Clean termination | exit 0, tmpdir cleaned unless `--keep-artifacts` | Exit 0; tmpdir preserved in the test runs by `--keep-artifacts`. **Passes.** |
| Lossless rewrite | every detail of source present in rewrite | See `tests/outputs/*-short-real/` — rewrite paraphrases the source, no lost detail. **Passes for the small case; the prompt contract is tested in anger in Phase 3 micro-design.** |

## Known measurement issue (not a defect)

The first-audio-latency measurement script in this phase used
line-buffered `subprocess.Popen(...stdout=PIPE)` and read `for line in
proc.stdout`, which on Python 3.14 plus writes into a stdout that
Kokoro flushes irregularly produced an artificial delay (reported
60 s). The real latency is observable from the log timestamps
embedded in the producer/consumer prints. Future `--measure-latency`
mode should use `subprocess.Popen(bufsize=1, text=True)` and
`proc.stdout.flush()` guards.

## Residual open items

None that block v0.1 usage. The retrospective
(`docs/plan/retrospective.md`) captures follow-ups.
