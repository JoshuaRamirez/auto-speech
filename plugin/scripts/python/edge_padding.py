"""EdgePadding: sound added before and after a clip as it plays, so the output
device does not swallow the first or the last syllable.

Measured 2026-11-20 on 300 lines of his speech history: `say` renders a clip
whose speech ends about 55 ms before the file does (25 to 180 ms). mpv closes
the output within tens of milliseconds of the last sample reaching the device
the Mac sees. His output is a USB Bluetooth adapter (Sennheiser BTD 700): the
Mac sees 7 ms of latency and nothing of the radio hop behind it, so the end of
a line can still be on its way to the headphones when the stream is closed.
His words, 11:56: "autospeech stuff gets cutoff at the end and maybe the start".

The start: the same 300 clips begin speaking at 0 ms (median; 85 ms at most),
and every line opens the device afresh after minutes of quiet, so whatever
the adapter and headphones lose while they wake is the first sound of the
first word.

The first lead-in was 300 ms of digital silence. It was not enough. His
words, 15:24 the same day, of a line that began "Two things." after 3 min
20 s of quiet: "i didnt hear the word two in two things during the beginning
of your speech". Either the adapter does not wake on silence, or it takes
longer than 300 ms; the Mac cannot tell which. So a line that follows quiet
(a cold start) now gets a longer lead-in that is not silence: 1.2 s of a
25 Hz tone at -30 dBFS, below what speech headphones make audible and well
above any silence detector. A line that follows another within
WARM_SECONDS finds the device awake and keeps the short silent lead-in.

Everything is added by mpv as it plays (an audio filter on its command line).
The clip on disk, the speech history and the word timings are untouched.

AUTO_SPEECH_WAKE_MS (cold lead-in), AUTO_SPEECH_LEAD_IN_MS (warm lead-in) and
AUTO_SPEECH_TAIL_MS set the lengths in milliseconds; 0 turns one off (a cold
start with no wake lead-in gets the warm one); unset or unreadable means the
default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

WAKE_ENV = "AUTO_SPEECH_WAKE_MS"
LEAD_IN_ENV = "AUTO_SPEECH_LEAD_IN_MS"
TAIL_ENV = "AUTO_SPEECH_TAIL_MS"
DEFAULT_WAKE_MS = 1200
DEFAULT_LEAD_IN_MS = 300
DEFAULT_TAIL_MS = 500
MAX_PAD_MS = 3000

# A play that starts within this long of the last one's end finds the device awake.
WARM_SECONDS = 2.0

# The wake tone. ffmpeg's sine source is at -18 dBFS (amplitude 1/8), so -12 dB more is -30 dBFS.
WAKE_TONE_HZ = 25
WAKE_TONE_GAIN_DB = -12
WAKE_FADE_SECONDS = 0.05


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
    """What mpv plays before and after a clip, in milliseconds of listening time."""

    lead_ms: int = DEFAULT_LEAD_IN_MS
    tail_ms: int = DEFAULT_TAIL_MS
    wake_ms: int = DEFAULT_WAKE_MS

    @classmethod
    def from_env(cls) -> "EdgePadding":
        return cls(
            lead_ms=_ms_from_env(LEAD_IN_ENV, DEFAULT_LEAD_IN_MS),
            tail_ms=_ms_from_env(TAIL_ENV, DEFAULT_TAIL_MS),
            wake_ms=_ms_from_env(WAKE_ENV, DEFAULT_WAKE_MS),
        )

    @classmethod
    def none(cls) -> "EdgePadding":
        return cls(lead_ms=0, tail_ms=0, wake_ms=0)

    def is_cold(self, seconds_since_last_play: float | None) -> bool:
        """True when the device has had time to go to sleep: nothing has played
        yet, or the last play ended more than WARM_SECONDS ago."""
        return seconds_since_last_play is None or seconds_since_last_play > WARM_SECONDS

    def lead_in_ms(self, cold: bool = False) -> int:
        """The lead-in a play gets: the wake lead-in on a cold start (when
        there is one), else the short silent one."""
        return self.wake_ms if cold and self.wake_ms > 0 else self.lead_ms

    def mpv_filter(self, speed: float = 1.0, cold: bool = False) -> str | None:
        """The --af option that adds the lead-in and the tail, or None when
        there is nothing to add. mpv changes the rate after this filter, so
        the lengths are scaled by `speed` to stay the same to the ear."""
        rate = speed if speed and speed > 0 else 1.0
        lead = self.lead_in_ms(cold)
        graph = ""
        if lead > 0:
            graph = f"adelay=delays={round(lead * rate)}:all=1"
            if cold and self.wake_ms > 0:
                # The clip is delayed, and the tone is mixed over the delay: the
                # clip's samples pass through unchanged (normalize=0).
                seconds = lead * rate / 1000
                fade = min(WAKE_FADE_SECONDS, seconds / 2)
                graph += (
                    f"[d];sine=f={WAKE_TONE_HZ}:d={seconds:g},volume={WAKE_TONE_GAIN_DB}dB,"
                    f"afade=t=in:d={fade:g},afade=t=out:st={seconds - fade:g}:d={fade:g}[w];"
                    "[d][w]amix=inputs=2:duration=first:normalize=0"
                )
        if self.tail_ms > 0:
            graph += ("," if graph else "") + f"apad=pad_dur={self.tail_ms * rate / 1000:g}"
        if not graph:
            return None
        return f"--af=lavfi=[{graph}]"

    def clip_position(
        self, time_pos: float, clip_seconds: float | None = None, speed: float = 1.0, cold: bool = False,
    ) -> float:
        """Seconds into the clip itself for an mpv time-pos: mpv counts the
        lead-in before the clip and keeps counting through the quiet after it;
        the clip's own position starts when its first sample plays and stops
        at its end. `speed` and `cold` are the ones mpv_filter was given: the
        lead-in is `speed` times lead_in_ms(cold) of mpv's clock."""
        rate = speed if speed and speed > 0 else 1.0
        pos = max(0.0, float(time_pos) - self.lead_in_ms(cold) * rate / 1000.0)
        if clip_seconds is not None and clip_seconds > 0:
            pos = min(pos, float(clip_seconds))
        return pos
