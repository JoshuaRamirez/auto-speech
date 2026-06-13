"""DedupGuard: literal-duplicate playback suppression.

If another worker is already playing the EXACT same source hash, playing
it again would render identical audio and (since playback start may quit
the prior mpv) cut the user off mid-line only to restart the same words.
This guard suppresses that duplicate.

"Already playing the same hash" holds iff ALL of:
  - the now-playing marker file exists,
  - its content equals our source hash,
  - the marker is younger than 120s (a stale marker from a long-finished
    play must not block a legitimate replay of identical content later),
  - the recorded mpv pid is still alive.

Direct port of already_playing_same_hash + write_now_playing in
autoplay_worker.sh.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

NOW_PLAYING_MARKER = Path("/tmp/auto-speech-now-playing-hash")
AGE_CAP_SECONDS = 120
MPV_PID_PATH = Path("/tmp/auto-speech/mpv.pid")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


class DedupGuard:
    """Suppresses a duplicate playback of an already-in-flight source hash.

    `mpv_pid_path` and `now` are injectable so the age cap and mpv-alive
    branches can be exercised deterministically in tests.
    """

    def __init__(
        self,
        marker_path: Path = NOW_PLAYING_MARKER,
        mpv_pid_path: Path = MPV_PID_PATH,
        now=time.time,
    ) -> None:
        self._marker = Path(marker_path)
        self._mpv_pid_path = Path(mpv_pid_path)
        self._now = now

    def _mpv_alive(self) -> bool:
        try:
            raw = self._mpv_pid_path.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        if not raw:
            return False
        try:
            pid = int(raw)
        except ValueError:
            return False
        return _pid_alive(pid)

    def already_playing(self, source_hash: str) -> bool:
        """True iff the same source hash is already in flight (see module doc)."""
        try:
            current = self._marker.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        if current != source_hash:
            return False
        try:
            marker_mtime = self._marker.stat().st_mtime
        except OSError:
            return False
        age = self._now() - marker_mtime
        if not age < AGE_CAP_SECONDS:
            return False
        return self._mpv_alive()

    def write_now_playing(self, source_hash: str) -> None:
        """Stamp the now-playing marker with our source hash (best effort)."""
        try:
            self._marker.write_text(source_hash, encoding="utf-8")
        except OSError:
            pass  # best effort, like the bash `|| true`
