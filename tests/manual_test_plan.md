# Manual Test Plan (E2E)

Live runs of the full `/speak` flow (extract → rewrite → speak) capturing
artifacts for the record.

## Cases

1. **Short real message** — ordinal from the live session, expected to
   trigger the short path (total duration ≤ 15 s).
2. **Long synthetic message** — pre-authored ~850-char text to trigger
   the Fibonacci long path.
3. **Error: no such turn** — `--ordinal 9999` → exit 2 from extractor.

## NFR targets (from 01-conceptualization.md)

| NFR | Target | How measured |
|---|---|---|
| First-audio latency | ≤ 6 s | Time from speak.py entry to first afplay start |
| Inter-chunk gap | 0 audible gap (< 100 ms) | Inspection of log timestamps |
| Clean termination | exit 0, no orphan processes, tmpdir cleaned unless `--keep-artifacts` | Process tree + `ls /tmp/auto-speech-*` |

## Captured outputs

Stored under `tests/outputs/<timestamp>-<case>/`:
- `run.log` — captured stdout + stderr of the run
- `plan.json` — reconstructed chunk plan (for long-path runs)
- `chunk-*.wav` — produced audio files (preserved via `--keep-artifacts`)
