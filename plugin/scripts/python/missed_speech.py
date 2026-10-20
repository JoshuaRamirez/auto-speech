"""The missed list: speech that was held for another app's sound and has
not been heard yet.

A line goes on the list the moment the daemon holds it (status HELD), and
stays there as MISSED when the longest wait passes, when it turns out to
be stale backlog once the sound stops, or when a user interrupt releases
the hold. It leaves the list only when it has played, by itself once the
sound stops or by `missed --play`. Nothing held is ever dropped. See
ADR-015.

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

ACTIONS = ("list", "play", "clear")


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

    def __init__(self, path: Path | str | None = None, *, clock: Callable[[], float] = time.time) -> None:
        self._path = Path(path) if path is not None else default_missed_path()
        self._clock = clock

    @property
    def path(self) -> Path:
        return self._path

    # -- storage -------------------------------------------------------------

    def _read(self) -> list[MissedLine]:
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
        return lines

    def _write(self, lines: Iterable[MissedLine]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(
            {"version": FILE_VERSION, "lines": [m.to_dict() for m in sorted(lines, key=lambda m: m.queued_at)]},
            indent=2,
        )
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(body + "\n", encoding="utf-8")
        os.replace(tmp, self._path)

    def _mutate(self, fn: Callable[[list[MissedLine]], Any]) -> Any:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_name(self._path.name + ".lock")
        with open(lock_path, "a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                lines = self._read()
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

    def upsert(self, line: MissedLine) -> MissedLine:
        """Add the line, or replace the one with the same id."""

        def go(lines: list[MissedLine]) -> MissedLine:
            for i, existing in enumerate(lines):
                if existing.id == line.id:
                    lines[i] = line
                    return line
            lines.append(line)
            return line

        return self._mutate(go)

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
    from daemon_client import Priority

    sent = 0
    try:
        for m in to_play:
            if m.text:
                client.speak(
                    m.text,
                    priority=Priority.EXPLICIT_MCP,
                    source_hash=m.source_hash,
                    voice_id=m.voice_id or None,
                    speed=m.speed,
                    missed_id=m.id,
                    hold=False,
                )
            elif m.source_hash:
                client.play_cache(m.source_hash, priority=Priority.EXPLICIT_MCP, missed_id=m.id, hold=False)
            else:
                missed.remove(m.id)
                continue
            sent += 1
    except ConnectionError as exc:
        return ActionResult(False, f"missed speech: could not reach the auto-speech daemon ({exc}); {sent} line(s) queued before that")
    text = (
        f"missed speech: replaying {sent} line{'s' if sent != 1 else ''}, oldest first, over any other sound; "
        "each leaves the list once it has played"
    )
    if held:
        text += f"\n{held} held line{'s' if held != 1 else ''} will play by themselves when the sound stops"
    return ActionResult(True, text)
