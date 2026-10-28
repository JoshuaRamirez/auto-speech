"""One line id, one play (ADR-025).

On 2026-10-22 between 00:35 and 00:40, a 24 GB local model pegged the Mac.
The narrator took 2.5 minutes to synthesize the Master Desk's 00:35 line.
desk-speaker, waiting on the socket, took the TimeoutError for a dropped
connection and sent the same line three more times, and the daemon client
retries a timed-out request by itself besides. The narrator knew no line id,
so it queued and played every copy: one line four times, the 00:37 line
twice.

A speak request may carry a `line_id`. The ledger keeps every id for an hour:

  queued    accepted; `synth_started` is set while it synthesizes
  playing   the sink started it
  done      it played to its end
  dropped   it will not play: purged by a stop, lost in a restart,
            set aside (meeting, timeout, stale), or failed; `reason` says which

A speak whose id is queued, playing, or done is acknowledged with that
state and not queued again. A dropped id is admitted again: a replay follows
only a real interruption.

On 2026-10-27 his "Why am I hearing duplication when you say things?"
(ADR-026): a line held across his stop was replayed from the missed list
with no id, and desk-speaker's resend carried the id, so the narrator could
not tell them apart. Now a done id is refused for as long as it is kept
(a day, the missed list's own longest age), not ten minutes, and the ledger
holds at most MAX_LINES ids, the oldest settled ones going first.

The ledger is kept in a file beside the missed list (~/.local/state/
auto-speech/lines.json), so an id survives a restart and a reboot. An id
still queued or playing when the daemon starts was lost in the restart and
reads as dropped.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Optional

QUEUED = "queued"
PLAYING = "playing"
DONE = "done"
DROPPED = "dropped"
STATES = (QUEUED, PLAYING, DONE, DROPPED)

DROP_RESTART = "restart"
DROP_FAILED = "failed"
DROP_INTERRUPTED = "interrupted"
DROP_STOPPED = "stopped"

KEEP_S = 24 * 3600.0  # how long an id is kept; a done id is refused all that time
DEDUP_DONE_S = KEEP_S  # (ADR-026) a line done this recently is not played again: while it is kept
MAX_LINES = 2000  # the most ids kept; past it the oldest settled ones go
MAX_ID_LEN = 200
TEXT_PREVIEW = 60


@dataclass
class LineRecord:
    line_id: str
    state: str = QUEUED
    text: str = ""
    queued_at: float = 0.0
    synth_started: Optional[float] = None
    playing_at: Optional[float] = None
    done_at: Optional[float] = None
    dropped_at: Optional[float] = None
    reason: str = ""
    sends: int = 1  # how many speak requests carried this id
    plays: int = 0
    retries: int = 0
    missed_id: str = ""  # the missed-list line it was set aside as, so its replay keeps this id
    in_pass: bool = False  # the worker has it in hand now (internal)

    @property
    def updated_at(self) -> float:
        return max(t for t in (self.queued_at, self.synth_started, self.playing_at, self.done_at, self.dropped_at) if t)

    def public(self) -> dict[str, Any]:
        out = asdict(self)
        out.pop("in_pass", None)
        out["id"] = self.line_id  # the shape desk-speaker reads: "line": {"id", "state"}
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Optional["LineRecord"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("line_id"), str):
            return None
        names = {f.name for f in fields(cls)}
        rec = cls(**{k: v for k, v in raw.items() if k in names})
        return rec if rec.state in STATES else None


def default_lines_path() -> Path:
    """Where the live daemon keeps the ledger: AUTO_SPEECH_LINES_FILE, else
    the runtime state folder when one is set (a test or a dev daemon), else
    beside the missed list, which outlives a reboot."""
    override = os.environ.get("AUTO_SPEECH_LINES_FILE", "").strip()
    if override:
        return Path(override)
    if os.environ.get("AUTO_SPEECH_TMP_ROOT", "").strip():
        from state_paths import state_path

        return state_path("auto-speech-lines.json")
    return Path.home() / ".local" / "state" / "auto-speech" / "lines.json"


def clean_line_id(value: Any) -> str:
    """A caller's id, trimmed; "" when there is none or it is unusable."""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return ""
    text = str(value).strip()
    if not text or len(text) > MAX_ID_LEN or any(ord(c) < 32 for c in text):
        return ""
    return text


class LineLedger:
    """Thread-safe: the IPC threads admit and ask, the tts worker moves lines on."""

    def __init__(
        self,
        path: Optional[Path] = None,
        *,
        clock: Callable[[], float] = time.time,
        legacy: Optional[Path] = None,
    ) -> None:
        self._path = Path(path) if path is not None else None
        self._clock = clock
        self._lock = threading.Lock()
        self._lines: dict[str, LineRecord] = {}
        self._load(legacy)

    # -- the caller's side ---------------------------------------------------------

    def admit(self, line_id: str, text: str = "") -> Optional[LineRecord]:
        """Records a new line as queued and returns None, or returns the
        existing record (its `sends` counted) when this id must not be
        queued again."""
        if not line_id:
            return None
        now = self._clock()
        with self._lock:
            self._prune(now)
            rec = self._lines.get(line_id)
            if rec is not None and self._blocks(rec, now):
                rec.sends += 1
                self._save()
                return LineRecord(**asdict(rec))
            sends = rec.sends + 1 if rec is not None else 1
            plays = rec.plays if rec is not None else 0
            self._lines[line_id] = LineRecord(
                line_id=line_id, text=(text or "")[:TEXT_PREVIEW], queued_at=now, sends=sends, plays=plays
            )
            self._save()
            return None

    def get(self, line_id: str) -> Optional[LineRecord]:
        with self._lock:
            rec = self._lines.get(line_id)
            return LineRecord(**asdict(rec)) if rec is not None else None

    def answer(self, line_id: str) -> dict[str, Any]:
        """What the status reply says for one id."""
        rec = self.get(line_id)
        if rec is None:
            return {"id": line_id, "line_id": line_id, "state": "unknown"}
        return rec.public()

    # -- the worker's side -----------------------------------------------------------

    def taken(self, line_id: str) -> None:
        self._move(line_id, lambda r, now: setattr(r, "in_pass", True))

    def synth_started(self, line_id: str) -> None:
        def go(r: LineRecord, now: float) -> None:
            if r.state == QUEUED:
                r.synth_started = now
        self._move(line_id, go)

    def playing(self, line_id: str) -> None:
        def go(r: LineRecord, now: float) -> None:
            if r.state in (QUEUED, PLAYING):
                r.state, r.playing_at = PLAYING, now
        self._move(line_id, go)

    def done(self, line_id: str) -> None:
        def go(r: LineRecord, now: float) -> None:
            r.state, r.done_at, r.reason, r.in_pass = DONE, now, "", False
            r.plays += 1
        self._move(line_id, go)

    def is_done(self, line_id: str) -> bool:
        """True when the id played to its end and is still kept."""
        rec = self.get(line_id) if line_id else None
        return rec is not None and rec.state == DONE

    def dropped(self, line_id: str, reason: str, *, missed_id: str = "") -> None:
        def go(r: LineRecord, now: float) -> None:
            if r.state == DONE:
                return
            r.state, r.dropped_at, r.reason, r.in_pass = DROPPED, now, reason or DROP_FAILED, False
            if missed_id:
                r.missed_id = missed_id
        self._move(line_id, go)

    def for_missed(self, missed_id: str) -> str:
        """The line id set aside as this missed-list line, so its replay is
        the same line to the ledger; "" when none was."""
        if not missed_id:
            return ""
        with self._lock:
            for rec in self._lines.values():
                if rec.missed_id == missed_id:
                    return rec.line_id
        return ""

    def requeued(self, line_id: str, *, retry: bool = False) -> None:
        """Back on the queue whole: a timed-out line retried, or a line preempted."""
        def go(r: LineRecord, now: float) -> None:
            if r.state == DONE:
                return
            r.state, r.synth_started, r.playing_at, r.in_pass = QUEUED, None, None, False
            if retry:
                r.retries += 1
        self._move(line_id, go)

    def settle(self, line_id: str, reason: str) -> None:
        """The worker's pass over the line ended. A line it had in hand that
        neither played, nor was set aside, nor went back on the queue is
        dropped with `reason`."""
        def go(r: LineRecord, now: float) -> None:
            if r.in_pass and r.state in (QUEUED, PLAYING):
                r.state, r.dropped_at, r.reason = DROPPED, now, reason
            r.in_pass = False
        self._move(line_id, go)

    # -- inside --------------------------------------------------------------------

    @staticmethod
    def _blocks(rec: LineRecord, now: float) -> bool:
        if rec.state in (QUEUED, PLAYING):
            return True
        return rec.state == DONE and rec.done_at is not None and now - rec.done_at < DEDUP_DONE_S

    def _move(self, line_id: str, change: Callable[[LineRecord, float], None]) -> None:
        if not line_id:
            return
        with self._lock:
            rec = self._lines.get(line_id)
            if rec is None:
                return
            change(rec, self._clock())
            self._save()

    def _prune(self, now: float) -> None:
        for key in [k for k, r in self._lines.items() if r.state in (DONE, DROPPED) and now - r.updated_at > KEEP_S]:
            del self._lines[key]
        over = len(self._lines) - MAX_LINES
        if over > 0:
            settled = sorted((r.updated_at, k) for k, r in self._lines.items() if r.state in (DONE, DROPPED))
            for _, key in settled[:over]:
                del self._lines[key]

    def _load(self, legacy: Optional[Path] = None) -> None:
        if self._path is None:
            return
        source = self._path
        if not source.exists() and legacy is not None and Path(legacy).exists():
            source = Path(legacy)  # the file's old place (/tmp, before ADR-026): read once, written to the new
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        now = self._clock()
        for item in raw.get("lines", []) if isinstance(raw, dict) else []:
            rec = LineRecord.from_dict(item)
            if rec is None:
                continue
            if rec.state in (QUEUED, PLAYING):
                # The daemon that held it is gone: lost in the restart.
                rec.state, rec.dropped_at, rec.reason, rec.in_pass = DROPPED, now, DROP_RESTART, False
            self._lines[rec.line_id] = rec
        self._prune(now)
        self._save()

    def _save(self) -> None:
        if self._path is None:
            return
        body = json.dumps({"lines": [asdict(r) for r in self._lines.values()]})
        tmp = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(body, encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
