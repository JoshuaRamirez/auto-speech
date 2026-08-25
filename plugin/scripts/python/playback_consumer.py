"""PlaybackConsumer: dequeue AudioSegments and play each via afplay."""
from __future__ import annotations

import threading

from afplay_launcher import AfplayLauncher
from playback_queue import SENTINEL, PlaybackQueue


class PlaybackError(RuntimeError):
    """Raised when afplay reports a nonzero exit for any segment."""


class PlaybackConsumer:
    """Drain the queue to completion, playing each segment in order."""

    def __init__(
        self,
        queue: PlaybackQueue,
        stop_event: threading.Event,
        launcher: AfplayLauncher | None = None,
    ) -> None:
        self._queue = queue
        self._stop_event = stop_event
        self._launcher = launcher or AfplayLauncher()
        self.error: Exception | None = None
        self.played_count = 0

    def run(self) -> None:
        try:
            while True:
                item = self._queue.get()
                if item is SENTINEL:
                    print("[consumer] received SENTINEL; done")
                    return
                if self._stop_event.is_set():
                    print("[consumer] stop_event set; dropping remaining segments")
                    # Drain remaining items (including SENTINEL) without playing.
                    while item is not SENTINEL:
                        item = self._queue.get()
                    return
                seg = item
                print(
                    f"[consumer] play  #{seg.descriptor.index} "
                    f"dur={seg.actual_duration_seconds:.2f}s "
                    f"path={seg.wav_path.name}"
                )
                rc = self._launcher.play(seg.wav_path, self._stop_event)
                if rc != 0 and not self._stop_event.is_set():
                    raise PlaybackError(
                        f"afplay exit {rc} on chunk #{seg.descriptor.index}"
                    )
                self.played_count += 1
        except Exception as exc:
            self.error = exc
            self._stop_event.set()
            print(f"[consumer] ERROR: {exc}")
            raise
