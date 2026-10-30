"""Replay from the speech history: the one way `auto-speech replay` and the
MCP tool `playback_control` (action `replay`) pick a line and ask the
narrator to play it again (T38, T40).

A replay is the Nth most recent line of the speech history (1 = the newest):
the lines the speech player lists, never the old content-addressed cache
(config/cache). Each caller turns the outcome into its own shape: the CLI
into an exit code and a line on stderr, the MCP tool into its result.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from daemon_client import NARRATOR_TOO_OLD
from speech_history import HistoryLine, SpeechHistory


@dataclass(frozen=True)
class HistoryPick:
    """The line to replay, or why there is none (`refusal`, a plain sentence
    without a caller's prefix)."""

    line: Optional[HistoryLine]
    refusal: str = ""


def pick_history_line(ordinal: int, history: Optional[SpeechHistory] = None) -> HistoryPick:
    """The Nth most recent history line with an audio file. Refused, with a
    plain reason, when the history is off, empty, shorter than `ordinal`, or
    the line's WAV is gone. `ordinal` must already be >= 1."""
    history = history if history is not None else SpeechHistory()
    if not history.settings().enabled:
        return HistoryPick(
            None,
            "the speech history is off, so the lines spoken are not kept to replay. "
            "Turn it on with `auto-speech history on`.",
        )
    lines = list(reversed(history.load(limit=ordinal)))  # newest first
    if not lines:
        return HistoryPick(None, "the speech history is empty; nothing has been spoken yet.")
    if ordinal > len(lines):
        return HistoryPick(
            None,
            f"the speech history holds {len(lines)} line{'s' if len(lines) != 1 else ''}; "
            f"you asked for #{ordinal}",
        )
    line = lines[ordinal - 1]
    if not line.wav.is_file():
        return HistoryPick(None, f"history line {line.id} has no audio file ({line.wav})")
    return HistoryPick(line)


class NarratorReplay(Enum):
    QUEUED = "queued"
    TOO_OLD = "too_old"  # the running narrator does not know play_history: nothing was sent
    REFUSED = "refused"  # the narrator answered, and did not queue it


NARRATOR_TOO_OLD_MESSAGE = (
    "the running narrator is older than replay from the speech history; "
    "restart it with `auto-speech narrate restart` and try again."
)


@dataclass(frozen=True)
class NarratorReplayResult:
    outcome: NarratorReplay
    message: str = ""  # a plain sentence, without a caller's prefix

    @property
    def queued(self) -> bool:
        return self.outcome is NarratorReplay.QUEUED


def replay_through_narrator(client: Any, line_id: str) -> NarratorReplayResult:
    """Asks the narrator to queue history line `line_id` as the speech
    control's play-item does (`play_history`), so it waits behind and obeys
    the narrator. A narrator older than play_history is never sent it
    (DaemonClient.play_history asks its status first). Connection errors and
    KeyboardInterrupt propagate: each caller reports them its own way, and
    never follows a failure with a local play, since the narrator may have
    queued the line already."""
    resp = client.play_history(line_id)
    if resp.status in ("ok", "queued"):
        return NarratorReplayResult(NarratorReplay.QUEUED)
    if getattr(resp, "error_code", None) == NARRATOR_TOO_OLD:
        return NarratorReplayResult(NarratorReplay.TOO_OLD, NARRATOR_TOO_OLD_MESSAGE)
    message = getattr(resp, "message", "") or ""
    return NarratorReplayResult(
        NarratorReplay.REFUSED, f"the narrator did not queue line {line_id}: {message or resp.status}"
    )
