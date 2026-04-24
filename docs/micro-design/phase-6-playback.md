# Phase 6 Micro-Design — Playback Consumer

## Scope
Drain the `PlaybackQueue` and play each WAV in order via `afplay`. Clean
cancellation semantics: `stop_event` set → finish current play then exit
(or kill the current play if it's cooperative to do so).

## M1 — Classes
- `AfplayLauncher` — L1 adapter: spawn afplay, wait for exit, optionally terminate.
- `PlaybackConsumer` — L3 service: dequeue + play loop.

## M2 — Semantics

### AfplayLauncher
- `play(wav_path, stop_event) -> int`: spawn `afplay <path>`; poll the
  subprocess and the stop_event; if stop set, terminate the subprocess.
  Return the process's exit code.

### PlaybackConsumer
- `__init__(queue, stop_event, launcher)`.
- `run()`: loop pulling from queue until SENTINEL, invoking launcher.play
  on each segment. If afplay returns nonzero, log and set the orchestrator's
  error flag.

## M3 — Relationships
```
PlaybackConsumer ──uses──► PlaybackQueue
PlaybackConsumer ──uses──► AfplayLauncher
AfplayLauncher ──spawns──► subprocess.Popen('afplay', path)
```

## M4 — Implementation notes

- Polling interval for stop_event during afplay: 100 ms.
- On SIGINT: send SIGTERM to afplay (immediate stop).
- Consumer's own exceptions set `self.error = exc` for the orchestrator.
- Consumer always drains until SENTINEL even on error, unless stop_event
  is set (then it exits as soon as current playback finishes).

## Check gate
Feed three WAVs from Phase 5's test tmpdir into a new queue, run the
consumer, and listen. Should hear three segments in order with no gaps >
~150 ms (afplay process startup is ~50 ms on modern macOS).
