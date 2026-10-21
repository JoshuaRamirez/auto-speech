# Data model: Player launch

## HeardLine (existing, ADR-020 `HistoryLine`)
- `id`, `stem`, `wav` (absolute path), `sidecar` (dict): `text`, `spoken_at`, `sender.name`,
  `word_timings.words` (list of `{w, start, end}`), `duration_seconds`.
- Read only here. A WAV gone by hand-over time → outcome `skipped`.

## PlayerOutcome
What happened to the last line offered. Returned by `after_line` (synchronous part) or recorded
by the worker (asynchronous part).

| Value | Set by | Meaning |
|---|---|---|
| `off` | after_line | setting off, or no line |
| `held` | after_line, worker | a hold stands (or the gate could not say) |
| `not installed` | after_line | no `ui-utility` on PATH and no `UI Utility.app` |
| `queued` | after_line | handed to the worker |
| `opened` | worker | `open` answered `ok:true` |
| `skipped` | worker | the WAV was pruned before the hand-over |
| `no socket` | worker | could not connect, and launching was off or did not bring the socket up in 10 s |
| `refused` | worker | a reply `ok:false`; detail is `<error>: <detail>` |
| `timeout` | worker | no reply within 5 s |
| `wrong contract` | worker | `hello` did not say contract 1 with the `player` piece |

State file `auto-speech-player.json`: `{"outcome": str, "detail": str, "at": ISO local time,
"line_id": str|null}`, written by tmp + rename; absent means no hand-over yet.

## PlayerPanel
- Fixed id `auto-speech`; panel `player`. Opened again for each line (replace).
- Lifecycle (contract): `loading → ready`; `closed` by user frees the id; the next line opens it
  again.

## Connection (worker-owned)
- States: `none → connected → greeted`; any I/O error or EOF → `none`.
- `greeted` only after `hello` replied `contract: 1` with `player` in `pieces`.
