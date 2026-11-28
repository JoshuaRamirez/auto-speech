"""The next queued line, rendered while the line before it plays.

The worker took a line, rendered it, played it, and only then took the next,
so every passage of a long read waited for its own render (1 to 4 s) after the
last one ended. While a line plays, the worker now starts rendering the line
queued next, on the same single synthesis lane; when that line's turn comes,
its words and voice match and the render is taken instead of made again.

One render ahead at most. One that is not taken (the next line changed, a stop,
the speaker's name put in front) is dropped and its file removed."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Callable, Optional

Key = tuple[str, Any, Any]


class NextLineRender:
    """One line rendered ahead: its key (words, voice, speed), its file, and
    whether the render finished with audio."""

    def __init__(self, key: Key, path: Path) -> None:
        self.key = key
        self.path = path
        self._done = threading.Event()
        self._ok = False
        self._gone = False   # dropped or taken: the file is no longer ours to keep
        self._lock = threading.Lock()

    def run(self, render: Callable[[Path], bool], log: Callable[[str], None]) -> None:
        """Render on the caller's thread; never raises."""
        ok = False
        try:
            ok = bool(render(self.path)) and self.path.exists() and self.path.stat().st_size > 0
        except Exception as exc:  # noqa: BLE001 - a failed render is only a render not taken
            log(f"next-line render error: {exc!r}")
        with self._lock:
            self._ok = ok
            self._done.set()
            if self._gone:
                self.path.unlink(missing_ok=True)

    def start(self, render: Callable[[Path], bool], log: Callable[[str], None]) -> threading.Thread:
        t = threading.Thread(target=self.run, args=(render, log), name="next-line-render", daemon=True)
        t.start()
        return t

    def drop(self) -> None:
        with self._lock:
            self._gone = True
            if self._done.is_set():
                self.path.unlink(missing_ok=True)

    def take(self, dest: Path, timeout: Optional[float]) -> bool:
        """Move the finished render to `dest`; False (and dropped) when it
        failed or did not finish within `timeout`."""
        if not self._done.wait(timeout):
            self.drop()
            return False
        with self._lock:
            if self._gone or not self._ok:
                self._gone = True
                self.path.unlink(missing_ok=True)
                return False
            self._gone = True
            os.replace(self.path, dest)
            return True
