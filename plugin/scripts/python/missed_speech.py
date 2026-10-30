"""The missed list: speech that was held for another app's sound and has
not been heard yet.

A line goes on the list the moment the daemon holds it (status HELD), and
stays there as MISSED when the longest wait passes, when it turns out to
be stale backlog once the sound stops, or when a user interrupt releases
the hold. It leaves the list only when it has played, by itself once the
sound stops or by `missed --play`. Nothing held is ever dropped. See
ADR-015.

One speech line, one entry (ADR-026): a line that carries a `line_id` (the
speak request's, ADR-025) keeps it here, so a line set aside twice, or set
aside and also added by its caller, is one entry; its replay carries the id,
so the narrator plays it once whichever path delivers it.

One file, one shape, two readers: the CLI (`auto-speech missed`,
`--play`, `--clear`) and the MCP tool (`missed(action)`) share
`run_action` so they say the same thing. The daemon is the only writer
while it runs; `--clear` writes too, under the same file lock.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

MISSED_FILE_ENV = "AUTO_SPEECH_MISSED_FILE"
FILE_VERSION = 1

HELD = "held"  # in the queue, waiting for the other sound to stop
MISSED = "missed"  # not played; waits for `missed --play` or `--clear`

REASON_LONGEST_WAIT = "longest wait"
REASON_STALE = "stale backlog"
REASON_INTERRUPTED = "interrupted"
REASON_RESTART = "daemon restarted"
REASON_TIMEOUT = "timed out"
REASON_MEETING = "meeting"  # set aside at once while he was in a meeting
REASON_STOPPED = "stopped"  # arrived while he had stopped speech; plays on his play or resume
REASON_ADDED = "added"  # put on the list by a caller (`missed add`): a line held or cut elsewhere

ACTIONS = ("list", "play", "clear")

# A missed line older than this leaves the list by itself, so the list holds
# what is still news instead of piling up (#m1021-1615-4497). Held lines are
# in the queue and never expire.
MAX_AGE_S = 24 * 3600.0


def default_missed_path() -> Path:
    override = os.environ.get(MISSED_FILE_ENV)
    if override:
        return Path(override)
    return Path.home() / ".local" / "state" / "auto-speech" / "missed.json"


def new_id() -> str:
    return secrets.token_hex(4)


@dataclass(frozen=True)
class MissedLine:
    """One line of speech that was held. Immutable; `replace()` to change."""

    id: str
    text: str  # "" for a cached-audio replay with no text (source_hash then names it)
    queued_at: float  # epoch seconds, when the line was asked for
    status: str = HELD
    reason: str = ""  # why it is MISSED: longest wait | stale backlog | interrupted | daemon restarted
    held_for: tuple[str, ...] = ()  # the apps that were playing
    waited_seconds: float = 0.0
    missed_at: Optional[float] = None
    priority: int = 4
    source_hash: Optional[str] = None
    session_id: Optional[str] = None
    voice_id: str = ""
    speed: float = 1.0
    announced: bool = False  # a meeting line already counted in a spoken summary
    source: Optional[str] = None  # who said it, when a caller named it (`missed add --source`)
    line_id: str = ""  # the speak request's line id (ADR-025/026): one entry per id, and its replay carries it
    origin_file: Optional[str] = None  # the file the line came from (desk-say's), for the history sidecar
    # The desk that sent it, recorded from the line itself when it was set
    # aside or added (its session's name, as the history records it), so its
    # row, its replay and its history name it even once the session is gone.
    # None when the line came with no session and no named sender: never guessed.
    sender: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["held_for"] = list(self.held_for)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MissedLine":
        return cls(
            id=str(d.get("id") or new_id()),
            text=str(d.get("text") or ""),
            queued_at=float(d.get("queued_at") or 0.0),
            status=str(d.get("status") or MISSED),
            reason=str(d.get("reason") or ""),
            held_for=tuple(str(x) for x in (d.get("held_for") or [])),
            waited_seconds=float(d.get("waited_seconds") or 0.0),
            missed_at=float(d["missed_at"]) if d.get("missed_at") is not None else None,
            priority=int(d.get("priority") or 4),
            source_hash=str(d["source_hash"]) if d.get("source_hash") else None,
            session_id=str(d["session_id"]) if d.get("session_id") else None,
            voice_id=str(d.get("voice_id") or ""),
            speed=float(d.get("speed") or 1.0),
            announced=bool(d.get("announced")),
            source=str(d["source"]) if d.get("source") else None,
            line_id=str(d.get("line_id") or ""),
            origin_file=str(d["origin_file"]) if d.get("origin_file") else None,
            sender=str(d["sender"]) if d.get("sender") else None,
        )

    @property
    def label(self) -> str:
        if self.text:
            return self.text
        if self.source_hash:
            return f"(cached audio {self.source_hash[:12]})"
        return "(no text)"


class MissedList:
    """The file behind `auto-speech missed`. Every mutation is a locked
    read-modify-write, so the daemon and a CLI `--clear` cannot lose each
    other's change; every write is atomic (tmp + rename)."""

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        clock: Callable[[], float] = time.time,
        max_age_s: float = MAX_AGE_S,
    ) -> None:
        if max_age_s <= 0:
            raise ValueError("max_age_s must be positive")
        self._path = Path(path) if path is not None else default_missed_path()
        self._clock = clock
        self._max_age_s = float(max_age_s)

    @property
    def path(self) -> Path:
        return self._path

    # -- storage -------------------------------------------------------------

    def _read(self, *, include_expired: bool = False) -> list[MissedLine]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError:
            return []
        try:
            data = json.loads(raw) if raw.strip() else {}
        except ValueError:
            return []
        items = data.get("lines", []) if isinstance(data, dict) else []
        lines = [MissedLine.from_dict(x) for x in items if isinstance(x, dict)]
        lines.sort(key=lambda m: m.queued_at)
        return lines if include_expired else [m for m in lines if not self._expired(m)]

    def _expired(self, m: MissedLine) -> bool:
        """A missed line whose latest moment (asked for, or set aside) is
        older than the longest age. Every read leaves it out, and the next
        write drops it from the file."""
        if m.status != MISSED:
            return False
        return max(m.queued_at, m.missed_at or 0.0) < self._clock() - self._max_age_s

    def _write(self, lines: Iterable[MissedLine]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(
            {"version": FILE_VERSION, "lines": [m.to_dict() for m in sorted(lines, key=lambda m: m.queued_at)]},
            indent=2,
        )
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(body + "\n", encoding="utf-8")
        os.replace(tmp, self._path)

    def _mutate(self, fn: Callable[[list[MissedLine]], Any], *, include_expired: bool = False) -> Any:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_name(self._path.name + ".lock")
        with open(lock_path, "a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                lines = self._read(include_expired=include_expired)
                result = fn(lines)
                self._write(lines)
                return result
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    # -- reads -----------------------------------------------------------------

    def load(self) -> list[MissedLine]:
        """All lines, oldest first."""
        return self._read()

    def counts(self) -> dict[str, int]:
        lines = self._read()
        return {
            "held": sum(1 for m in lines if m.status == HELD),
            "missed": sum(1 for m in lines if m.status == MISSED),
            "meeting": sum(1 for m in lines if m.status == MISSED and m.reason == REASON_MEETING),
        }

    def missed_in_meeting(self, since: float = 0.0) -> list[MissedLine]:
        """The lines set aside for a meeting, missed after `since`, oldest first."""
        return [
            m for m in self._read()
            if m.status == MISSED and m.reason == REASON_MEETING and (m.missed_at or 0.0) >= since
        ]

    # -- writes ------------------------------------------------------------------

    def expire(self) -> int:
        """Drop the expired lines from the file now. Returns how many went."""
        if not self._path.exists():
            return 0

        def go(lines: list[MissedLine]) -> int:
            before = len(lines)
            lines[:] = [m for m in lines if not self._expired(m)]
            return before - len(lines)

        return self._mutate(go, include_expired=True)

    def take_meeting_summary(self) -> list[MissedLine]:
        """The meeting lines no summary has counted yet, oldest first, marked
        as counted in the same locked write. The mark is in the file, so a
        restarted daemon never says the same summary again."""

        def due(m: MissedLine) -> bool:
            return m.status == MISSED and m.reason == REASON_MEETING and not m.announced

        if not any(due(m) for m in self._read()):
            return []  # the daemon asks every poll: no write unless one is due

        def go(lines: list[MissedLine]) -> list[MissedLine]:
            taken = []
            for i, m in enumerate(lines):
                if due(m):
                    lines[i] = replace(m, announced=True)
                    taken.append(lines[i])
            return taken

        return self._mutate(go)

    def upsert(self, line: MissedLine) -> MissedLine:
        """Add the line, or replace the one with the same id, or the one with
        the same speech line id (ADR-026): one line is one entry, under the
        id it first had, so a replay already queued under that id stays
        right. Returns the line as stored."""

        def go(lines: list[MissedLine]) -> MissedLine:
            for i, existing in enumerate(lines):
                if existing.id == line.id:
                    lines[i] = line
                    return line
            if line.line_id:
                for i, existing in enumerate(lines):
                    if existing.line_id == line.line_id:
                        stored = replace(
                            line,
                            id=existing.id,
                            queued_at=min(existing.queued_at, line.queued_at) if existing.queued_at else line.queued_at,
                            source=line.source or existing.source,
                            session_id=line.session_id or existing.session_id,
                            sender=line.sender or existing.sender,
                            origin_file=line.origin_file or existing.origin_file,
                        )
                        lines[i] = stored
                        return stored
            lines.append(line)
            return line

        return self._mutate(go)

    def find_line(self, line_id: str) -> Optional[MissedLine]:
        """The entry for a speech line id, or None."""
        if not line_id:
            return None
        return next((m for m in self._read() if m.line_id == line_id), None)

    def hold(self, line: MissedLine, held_for: Iterable[str] = ()) -> MissedLine:
        return self.upsert(replace(line, status=HELD, held_for=tuple(held_for), reason="", missed_at=None))

    def miss(self, line: MissedLine, *, reason: str, waited_seconds: float = 0.0, held_for: Iterable[str] | None = None) -> MissedLine:
        return self.upsert(
            replace(
                line,
                status=MISSED,
                reason=reason,
                waited_seconds=float(waited_seconds),
                held_for=tuple(held_for) if held_for is not None else line.held_for,
                missed_at=self._clock(),
            )
        )

    def remove(self, line_id: str) -> bool:
        """The line has played: forget it. True when it was on the list."""

        def go(lines: list[MissedLine]) -> bool:
            before = len(lines)
            lines[:] = [m for m in lines if m.id != line_id]
            return len(lines) != before

        return self._mutate(go)

    def remove_line(self, line_id: str) -> int:
        """The speech line has played, by whichever path: every entry for
        its id leaves the list. Returns how many went."""
        if not line_id or not any(m.line_id == line_id for m in self._read(include_expired=True)):
            return 0  # asked after every play: no write unless one is there

        def go(lines: list[MissedLine]) -> int:
            before = len(lines)
            lines[:] = [m for m in lines if m.line_id != line_id]
            return before - len(lines)

        return self._mutate(go, include_expired=True)

    def clear(self) -> int:
        def go(lines: list[MissedLine]) -> int:
            n = len(lines)
            lines.clear()
            return n

        return self._mutate(go)

    def settle_held(self, reason: str = REASON_RESTART) -> int:
        """Every HELD line becomes MISSED (the daemon that held them is gone)."""
        now = self._clock()

        def go(lines: list[MissedLine]) -> int:
            n = 0
            for i, m in enumerate(lines):
                if m.status == HELD:
                    lines[i] = replace(m, status=MISSED, reason=reason, missed_at=now)
                    n += 1
            return n

        return self._mutate(go)


# --------------------------------------------------------------------------
# Shared text for CLI and MCP (same words in both)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    text: str


def _hms(epoch: float) -> str:
    try:
        return datetime.fromtimestamp(epoch).strftime("%H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "--:--:--"


def _who(names: Iterable[str]) -> str:
    names = [n for n in names if n]
    return ", ".join(names) if names else "another app"


def format_lines(lines: list[MissedLine], *, now: float | None = None) -> str:
    now = time.time() if now is None else now
    held = [m for m in lines if m.status == HELD]
    missed = [m for m in lines if m.status == MISSED]
    if not lines:
        return "missed speech: nothing missed, nothing held"
    out = [
        f"missed speech: {len(lines)} line{'s' if len(lines) != 1 else ''} you have not heard "
        f"({len(held)} held, {len(missed)} missed), oldest first"
    ]
    for i, m in enumerate(lines, 1):
        when = _hms(m.queued_at)
        if m.status == HELD:
            so_far = max(0.0, now - m.queued_at)
            why = f"held {so_far:.0f} s so far while {_who(m.held_for)} plays"
        elif m.reason == REASON_LONGEST_WAIT:
            why = f"missed: longest wait ({m.waited_seconds:.0f} s) while {_who(m.held_for)} played"
        elif m.reason == REASON_STALE:
            why = f"missed: stale backlog ({m.waited_seconds:.0f} s old when its turn came)"
        elif m.reason == REASON_INTERRUPTED:
            why = f"missed: interrupted after {m.waited_seconds:.0f} s held"
        elif m.reason == REASON_RESTART:
            why = "missed: the daemon restarted while it was held"
        elif m.reason == REASON_TIMEOUT:
            why = "missed: timed out (its synthesis or playback ran past its budget)"
        elif m.reason == REASON_MEETING:
            why = f"missed: in a meeting ({_who(m.held_for)})"
        else:
            why = "missed"
        out.append(f"  {i}. {when}  {why}")
        out.append(f"      {m.label}")
    if missed:
        out.append("replay the missed lines, oldest first: auto-speech missed --play")
        out.append("missed lines older than a day leave the list by themselves")
    if held:
        out.append(f"{len(held)} held line{'s' if len(held) != 1 else ''} will play by themselves when the sound stops")
    out.append("forget all of them: auto-speech missed --clear")
    return "\n".join(out)


def run_action(
    action: str | None,
    *,
    missed: MissedList | None = None,
    client: Any | None = None,
    now: float | None = None,
) -> ActionResult:
    """One verb for the CLI (`auto-speech missed [--play|--clear]`) and the
    MCP tool (`missed(action)`), so both say the same thing."""
    missed = missed or MissedList()
    action = (action or "list").strip().lower()
    if action not in ACTIONS:
        return ActionResult(False, f"missed: unknown action {action!r} (expected {' | '.join(ACTIONS)})")
    if action == "list":
        return ActionResult(True, format_lines(missed.load(), now=now))
    if action == "clear":
        n = missed.clear()
        return ActionResult(True, f"missed speech: cleared {n} line{'s' if n != 1 else ''}")
    # play
    lines = missed.load()
    to_play = [m for m in lines if m.status == MISSED]
    held = sum(1 for m in lines if m.status == HELD)
    if not to_play:
        tail = (
            f"; {held} held line{'s' if held != 1 else ''} will play by themselves when the sound stops"
            if held
            else ""
        )
        return ActionResult(True, "missed speech: nothing to replay" + tail)
    if client is None:
        from daemon_client import DaemonClient

        client = DaemonClient()
    sent = [0]
    try:
        _send(to_play, missed=missed, client=client, sent=sent)
    except ConnectionError as exc:
        return ActionResult(False, f"missed speech: could not reach the auto-speech daemon ({exc}); {sent[0]} line(s) queued before that")
    sent = sent[0]
    text = (
        f"missed speech: replaying {sent} line{'s' if sent != 1 else ''}, oldest first, over any other sound; "
        "each leaves the list once it has played"
    )
    if held:
        text += f"\n{held} held line{'s' if held != 1 else ''} will play by themselves when the sound stops"
    return ActionResult(True, text)


def _send(lines: list[MissedLine], *, missed: MissedList, client: Any, sent: list[int]) -> None:
    """Queue each line to play over any other sound, oldest first; the daemon
    takes it off the list once heard. A line with neither text nor audio is
    dropped. `sent[0]` counts the lines queued (it stays right when the
    client raises ConnectionError part way)."""
    from daemon_client import Priority

    for m in lines:
        if m.text:
            # The line's own id, sender and file go with it (ADR-026): the
            # narrator plays one id once, and its history names the sender.
            same_line = {k: v for k, v in (("line_id", m.line_id), ("session_id", m.session_id),
                                           ("origin_file", m.origin_file), ("sender", m.sender)) if v}
            client.speak(
                m.text,
                priority=Priority.EXPLICIT_MCP,
                source_hash=m.source_hash,
                voice_id=m.voice_id or None,
                speed=m.speed,
                missed_id=m.id,
                hold=False,
                **same_line,
            )
        elif m.source_hash:
            client.play_cache(m.source_hash, priority=Priority.EXPLICIT_MCP, missed_id=m.id, hold=False)
        else:
            missed.remove(m.id)
            continue
        sent[0] += 1


def play_ids(ids: Iterable[str], *, missed: MissedList, client: Any) -> int:
    """`missed --play` for the named lines only, in the order named (the
    speech control's play-item and play-all). Held lines are skipped: they
    play by themselves. Returns how many were queued; raises ConnectionError."""
    by_id = {m.id: m for m in missed.load() if m.status == MISSED}
    lines = [by_id[i] for i in ids if i in by_id]
    sent = [0]
    _send(lines, missed=missed, client=client, sent=sent)
    return sent[0]
