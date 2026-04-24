# ADR-006 — Producer/consumer via in-process queue, WAV files on disk

**Status:** Accepted
**Date:** 2026-04-23

## Context
Generation and playback need to run concurrently. The coordination primitive
and the audio-data transport each have multiple viable choices.

## Decision
**Use a Python `queue.Queue` for coordination, and pass `Path` references to
on-disk WAV files through the queue.** Both threads are in the same process.

## Rationale
- `queue.Queue` is thread-safe, well-understood, and blocks appropriately with
  `put(block=True)` and `get(block=True, timeout=...)`.
- Passing WAV file paths (not bytes) keeps the queue lightweight and makes
  inspection trivial during development — `ls /tmp/auto-speech-<runid>` shows
  exactly what has been generated.
- `afplay` wants a file anyway — passing bytes would require writing a file
  right before the afplay call, defeating the early materialization.
- Bounded capacity (`QUEUE_CAPACITY = 3`) provides natural back-pressure if
  playback stalls without starving it.

## Alternatives considered
- **In-memory audio buffers (bytes) + sounddevice playback.** Rejected: more
  moving parts (audio device lifecycle, sample-rate handling) for no gain when
  `afplay` already solves playback cleanly.
- **Subprocesses + stdin piping.** Rejected: WAV streaming to `afplay` is not
  robust — afplay expects a seekable file.
- **Multiprocessing.** Rejected: the TTS engine holds the Kokoro model in
  memory; re-loading it per subprocess is unacceptable. Single-process
  + threads is the right grain.
- **asyncio.** Rejected: `afplay` is a blocking subprocess and Kokoro generation
  is CPU/GPU-bound. Async doesn't buy anything here.

## Consequences
- Tmpdir management is the orchestrator's responsibility: create per run, clean
  up on success, preserve on error for debugging (with `--keep-artifacts` to
  override).
- The `PlaybackQueue` is a thin wrapper over `queue.Queue` adding a `close()`
  helper that enqueues the `SENTINEL`.
- Cancellation flows through a `threading.Event` (`stop_event`) that both
  threads poll between operations.
