"""EdgePadding: quiet added around a clip as it plays, so the output device
does not swallow the last syllable.

Measured 2026-11-20 on 300 lines of his speech history: `say` renders a clip
whose speech ends about 55 ms before the file does (25 to 180 ms). mpv closes
the output within tens of milliseconds of the last sample reaching the device
the Mac sees. His output is a USB Bluetooth adapter (Sennheiser BTD 700): the
Mac sees 7 ms of latency and nothing of the radio hop behind it, so the end of
a line can still be on its way to the headphones when the stream is closed.
His words, 11:56: "autospeech stuff gets cutoff at the end and maybe the start".

The quiet is added by mpv as it plays (an audio filter on its command line).
The clip on disk, the speech history and the word timings are untouched.

AUTO_SPEECH_TAIL_MS sets the quiet after a clip in milliseconds; 0 turns it
off; unset or unreadable means the default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

TAIL_ENV = "AUTO_SPEECH_TAIL_MS"
DEFAULT_TAIL_MS = 500
MAX_PAD_MS = 3000


def _ms_from_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, min(MAX_PAD_MS, int(float(raw))))
    except ValueError:
        return default


@dataclass(frozen=True)
class EdgePadding:
    """How much quiet mpv plays after a clip, in milliseconds of listening time."""

    tail_ms: int = DEFAULT_TAIL_MS

    @classmethod
    def from_env(cls) -> "EdgePadding":
        return cls(tail_ms=_ms_from_env(TAIL_ENV, DEFAULT_TAIL_MS))

    @classmethod
    def none(cls) -> "EdgePadding":
        return cls(tail_ms=0)

    def mpv_filter(self, speed: float = 1.0) -> str | None:
        """The --af option that adds the quiet, or None when there is none to
        add. mpv changes the rate after this filter, so the lengths are scaled
        by `speed` to stay the same to the ear."""
        rate = speed if speed and speed > 0 else 1.0
        stages: list[str] = []
        if self.tail_ms > 0:
            stages.append(f"apad=pad_dur={self.tail_ms * rate / 1000:g}")
        if not stages:
            return None
        return "--af=lavfi=[" + ",".join(stages) + "]"

    def clip_position(self, time_pos: float, clip_seconds: float | None = None) -> float:
        """Seconds into the clip itself for an mpv time-pos: mpv keeps counting
        through the quiet after the clip, the clip's own position stops at its end."""
        pos = max(0.0, float(time_pos))
        if clip_seconds is not None and clip_seconds > 0:
            pos = min(pos, float(clip_seconds))
        return pos
