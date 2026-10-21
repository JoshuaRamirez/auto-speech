"""Speaker names: "<desk> here," only when who is talking changes.

His 19:06, 2026-10-18 (via Clerk #m1018-1908-8c7b): when one desk is the only
one talking to him it should not keep saying "Master Desk"; when another desk
starts talking it has to say which. "I just need to know what desk it is when
who decides to talk to me changes."

Three parts, each testable on its own:

- DeskNames: session id → desk name, read from the desk agents' notes
  (`<Agents dir>/*.md`, front matter `session:`; the name is the note's
  `name:` when it has one, else the note's file name). A personal note is
  never read into the map (section [[Personal]], herdr session "personal",
  or a "Personal - …" name: the desk's own rule, card 1369). Overrides from
  the config win over the notes.
- SpeakerSettings / SpeakerStore: ~/.config/auto-speech/speaker.toml
  ([speaker] enabled, quiet_gap_seconds, agents_dir, [speaker.names]).
- Announcer: the rule. A line is prefixed "<name> here," when its session
  differs from the last one spoken, or when the last line was spoken more
  than the quiet gap ago. A line that already opens with "<name> here," is
  not doubled. A line with no session is left alone and moves nothing. Only
  a line that was actually spoken moves the speaker (a held line set aside
  as missed does not), so the daemon calls `spoken()` after the sink played.

`run_action` is the one verb behind `auto-speech speaker …` and the MCP tool
`speaker`, so both say the same thing. See docs/decisions/ADR-016.
"""

from __future__ import annotations

import os
import re
import sys
import time
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

CONFIG_FILE_NAME = "speaker.toml"
CONFIG_ENV = "AUTO_SPEECH_SPEAKER_CONFIG"
AGENTS_DIR_ENV = "AUTO_SPEECH_AGENTS_DIR"
SECTION = "speaker"

DEFAULT_ENABLED = True
DEFAULT_QUIET_GAP_SECONDS = 900.0  # 15 minutes: a desk that has been quiet that long names itself again
DEFAULT_REFRESH_SECONDS = 30.0  # how often the notes are re-read for a new or renamed agent
UNKNOWN_NAME = "Another desk"  # a session no note names: still a change he should hear

ACTIONS = ("status", "on", "off", "gap", "name", "forget", "who")

# "Master Desk here," / "Clerk - Desk here." / "aspen desk here:" at the very
# start of a line. The name is short (a desk name, not a clause), so a line
# that merely contains "here" later on is not self-announced.
_SELF_ANNOUNCED = re.compile(r"\A\s*(?P<who>[^\n,.:;!?]{1,40}?)\s+here\s*(?:,|\.|:|;|!|—|-)", re.IGNORECASE)

_STATE_RANK = {"working": 0, "idle": 1, "parked": 2, "done": 3, "closed": 4}


def default_agents_dir() -> Path:
    """The desk's Agents folder: $AUTO_SPEECH_AGENTS_DIR, else $DESK_DIR/Agents,
    else the desk's own default tree."""
    override = os.environ.get(AGENTS_DIR_ENV)
    if override:
        return Path(override).expanduser()
    desk = os.environ.get("DESK_DIR")
    if desk:
        return Path(desk).expanduser() / "Agents"
    return Path.home() / "Desk" / "Agents"


def default_config_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override)
    return Path.home() / ".config" / "auto-speech" / CONFIG_FILE_NAME


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


def is_self_announced(text: str, names: Iterable[str]) -> bool:
    """True when the line already opens with "<name> here," for one of
    `names` (its own desk, or any desk the daemon knows: it names a desk, so
    he knows who, and the prefix would only double it). "I am here, finally."
    opens with "here," but "I am" is nobody, so it is not self-announced."""
    m = _SELF_ANNOUNCED.match(text or "")
    if not m:
        return False
    who = _norm(m.group("who"))
    return who in {_norm(n) for n in names if n}


# --------------------------------------------------------------------------
# Names from the desk agents' notes
# --------------------------------------------------------------------------


def _front_matter(text: str) -> dict[str, str]:
    """A note's front matter as {key: value}, quotes stripped; {} when none."""
    m = re.match(r"\A---\n(.*?)\n---", text or "", re.S)
    out: dict[str, str] = {}
    for line in (m[1] if m else "").splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith((" ", "-")):
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _is_personal(stem: str, props: Mapping[str, str]) -> bool:
    """The desk's rule (card 1369): a note in the [[Personal]] section, in the
    herdr session "personal", or named "Personal - …" is his; nothing of it
    is read past the front matter that classifies it."""
    if stem.lower() == "personal" or stem.lower().startswith("personal - "):
        return True
    if (props.get("herdr_session") or "").strip().lower() == "personal":
        return True
    return bool(re.search(r"\[\[\s*Personal\s*(\|[^\]]*)?\]\]", props.get("section") or ""))


class DeskNames:
    """session id → desk name from the agent notes, re-read every
    `refresh_seconds`; `overrides` (from the config) win over the notes."""

    def __init__(
        self,
        agents_dir: Path | str | None = None,
        *,
        overrides: Mapping[str, str] | None = None,
        refresh_seconds: float = DEFAULT_REFRESH_SECONDS,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._dir = Path(agents_dir).expanduser() if agents_dir is not None else default_agents_dir()
        self._fixed = {k: v for k, v in (overrides or {}).items() if k and v}  # given at construction
        self._overrides: dict[str, str] = {}  # from the config file, set_overrides(); wins over everything
        self._refresh = float(refresh_seconds)
        self._now = now
        self._map: dict[str, str] = {}
        self._rank: dict[str, tuple[int, int, str]] = {}
        self._read_at: Optional[float] = None

    @property
    def agents_dir(self) -> Path:
        return self._dir

    def set_overrides(self, overrides: Mapping[str, str] | None) -> None:
        self._overrides = {k: v for k, v in (overrides or {}).items() if k and v}

    def refresh(self, *, force: bool = False) -> None:
        now = self._now()
        if not force and self._read_at is not None and now - self._read_at < self._refresh:
            return
        self._read_at = now
        found: dict[str, str] = {}
        rank: dict[str, tuple[int, int, str]] = {}
        try:
            notes = sorted(self._dir.glob("*.md"))
        except OSError:
            notes = []
        for note in notes:
            try:
                head = note.read_text(encoding="utf-8", errors="ignore")[:4096]
            except OSError:
                continue
            props = _front_matter(head)
            if _is_personal(note.stem, props):
                continue
            session = (props.get("session") or "").strip()
            if len(session) < 8:
                continue
            name = (props.get("name") or "").strip() or note.stem
            # Two notes on one session (a desk and its closed predecessor):
            # the live one wins, then the one that names itself, then A–Z.
            key = (_STATE_RANK.get((props.get("state") or "").strip().lower(), 5), 0 if props.get("name") else 1, note.stem)
            if session not in found or key < rank[session]:
                found[session] = name
                rank[session] = key
        self._map = found
        self._rank = rank

    def name_for(self, session_id: Optional[str]) -> Optional[str]:
        sid = (session_id or "").strip()
        if not sid:
            return None
        if sid in self._overrides:
            return self._overrides[sid]
        if sid in self._fixed:
            return self._fixed[sid]
        self.refresh()
        return self._map.get(sid)

    def all(self) -> dict[str, str]:
        self.refresh()
        merged = dict(self._map)
        merged.update(self._fixed)
        merged.update(self._overrides)
        return merged

    def count(self) -> int:
        return len(self.all())


# --------------------------------------------------------------------------
# Settings file
# --------------------------------------------------------------------------


def _toml_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


@dataclass(frozen=True)
class SpeakerSettings:
    enabled: bool = DEFAULT_ENABLED
    quiet_gap_seconds: float = DEFAULT_QUIET_GAP_SECONDS
    agents_dir: Optional[str] = None  # None: default_agents_dir()
    names: tuple[tuple[str, str], ...] = ()  # (session id, name) overrides
    config_path: Optional[str] = None
    problems: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, section: object, config_path: Optional[str] = None) -> "SpeakerSettings":
        problems: list[str] = []
        if not isinstance(section, dict):
            section = {}
            if config_path is not None:
                problems.append(f"[{SECTION}] is not a table; using defaults")

        enabled = section.get("enabled", DEFAULT_ENABLED)
        if not isinstance(enabled, bool):
            problems.append(f"[{SECTION}] enabled should be true or false, got {enabled!r}")
            enabled = DEFAULT_ENABLED

        gap = section.get("quiet_gap_seconds", DEFAULT_QUIET_GAP_SECONDS)
        if isinstance(gap, bool) or not isinstance(gap, (int, float)) or gap < 0:
            problems.append(f"[{SECTION}] quiet_gap_seconds should be a number >= 0, got {gap!r}")
            gap = DEFAULT_QUIET_GAP_SECONDS

        agents_dir = section.get("agents_dir")
        if agents_dir is not None and (not isinstance(agents_dir, str) or not agents_dir.strip()):
            problems.append(f"[{SECTION}] agents_dir should be a path, got {agents_dir!r}")
            agents_dir = None

        raw_names = section.get("names", {})
        names: list[tuple[str, str]] = []
        if not isinstance(raw_names, dict):
            problems.append(f"[{SECTION}.names] should be a table of session id = name, got {raw_names!r}")
            raw_names = {}
        for sid, name in raw_names.items():
            if not isinstance(name, str) or not name.strip() or not str(sid).strip():
                problems.append(f"[{SECTION}.names] entry ignored: {sid!r} = {name!r}")
                continue
            names.append((str(sid).strip(), name.strip()))

        return cls(
            enabled=enabled,
            quiet_gap_seconds=float(gap),
            agents_dir=agents_dir.strip() if isinstance(agents_dir, str) else None,
            names=tuple(names),
            config_path=config_path,
            problems=tuple(problems),
        )

    def to_toml(self) -> str:
        lines = [
            "# auto-speech: name the desk only when the speaker changes (ADR-016).",
            f"[{SECTION}]",
            f"enabled = {'true' if self.enabled else 'false'}",
            f"quiet_gap_seconds = {self.quiet_gap_seconds:g}",
        ]
        if self.agents_dir:
            lines.append(f"agents_dir = {_toml_string(self.agents_dir)}")
        if self.names:
            lines.append("")
            lines.append(f"[{SECTION}.names]")
            for sid, name in self.names:
                lines.append(f"{_toml_string(sid)} = {_toml_string(name)}")
        return "\n".join(lines) + "\n"


class SpeakerStore:
    """Reads and writes speaker.toml. Missing file = defaults."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else default_config_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> SpeakerSettings:
        p = self._path
        if not p.is_file():
            return SpeakerSettings()
        try:
            raw = tomllib.loads(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            print(f"[speaker] could not parse {p}: {exc}; using defaults", file=sys.stderr)
            return SpeakerSettings(config_path=str(p), problems=(f"{p}: could not parse TOML ({exc})",))
        section = raw.get(SECTION) if isinstance(raw, dict) else None
        if section is None:
            return SpeakerSettings(config_path=str(p))
        return SpeakerSettings.from_mapping(section, config_path=str(p))

    def save(self, settings: SpeakerSettings) -> SpeakerSettings:
        p = self._path
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(settings.to_toml(), encoding="utf-8")
        os.replace(tmp, p)
        return replace(settings, config_path=str(p), problems=())

    def set_enabled(self, enabled: bool) -> SpeakerSettings:
        return self.save(replace(self.load(), enabled=bool(enabled)))

    def set_quiet_gap(self, seconds: float) -> SpeakerSettings:
        return self.save(replace(self.load(), quiet_gap_seconds=float(seconds)))

    def set_name(self, session_id: str, name: str) -> SpeakerSettings:
        current = self.load()
        kept = [(s, n) for s, n in current.names if s != session_id]
        kept.append((session_id, name))
        return self.save(replace(current, names=tuple(kept)))

    def forget_name(self, session_id: str) -> tuple[SpeakerSettings, bool]:
        current = self.load()
        kept = tuple((s, n) for s, n in current.names if s != session_id)
        return self.save(replace(current, names=kept)), len(kept) != len(current.names)


# --------------------------------------------------------------------------
# The rule
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    """What to say for one line. `text` is the line to synthesize; `prefix`
    is set when the desk is to be named ("<name> here,"); `clip_text` is the
    prefix as its own short utterance, for a line whose audio is already
    cached and cannot take the words in front."""

    text: str
    session_id: Optional[str]
    name: Optional[str]
    prefix: Optional[str]

    @property
    def clip_text(self) -> Optional[str]:
        return f"{self.name} here." if self.prefix else None


class Announcer:
    """Keeps the last speaker and decides when a line names its desk."""

    def __init__(
        self,
        store: SpeakerStore | None = None,
        names: DeskNames | None = None,
        *,
        now: Callable[[], float] = time.time,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._store = store or SpeakerStore()
        self._now = now
        self._log = log
        self._settings: Optional[SpeakerSettings] = None
        self._settings_mtime: Optional[float] = None
        self._names = names if names is not None else DeskNames(self._settings_now().agents_dir)
        self._names.set_overrides(dict(self._settings_now().names))
        self._last_session: Optional[str] = None
        self._last_name: Optional[str] = None
        self._last_at: Optional[float] = None

    # -- settings, re-read when the file changes ---------------------------

    def _settings_now(self) -> SpeakerSettings:
        try:
            mtime: Optional[float] = self._store.path.stat().st_mtime
        except OSError:
            mtime = None
        if self._settings is None or mtime != self._settings_mtime:
            self._settings = self._store.load()
            self._settings_mtime = mtime
            if getattr(self, "_names", None) is not None:
                self._names.set_overrides(dict(self._settings.names))
        return self._settings

    def settings(self) -> SpeakerSettings:
        return self._settings_now()

    # -- the rule -----------------------------------------------------------

    def plan(self, text: str, session_id: Optional[str]) -> Plan:
        sid = (session_id or "").strip() or None
        settings = self._settings_now()
        if not settings.enabled or sid is None:
            return Plan(text=text, session_id=sid, name=None, prefix=None)
        name = self._names.name_for(sid)
        known = set(self._names.all().values()) | {UNKNOWN_NAME}
        if name:
            known.add(name)
        if is_self_announced(text, known):
            return Plan(text=text, session_id=sid, name=name, prefix=None)
        now = self._now()
        changed = self._last_session != sid
        quiet = self._last_at is None or (now - self._last_at) > settings.quiet_gap_seconds
        if not (changed or quiet):
            return Plan(text=text, session_id=sid, name=name, prefix=None)
        spoken_name = name or UNKNOWN_NAME
        if name is None and self._log is not None:
            self._log(f"speaker: no desk note names session {sid}; saying {UNKNOWN_NAME!r}")
        prefix = f"{spoken_name} here,"
        return Plan(text=f"{prefix} {text}", session_id=sid, name=spoken_name, prefix=prefix)

    def name_for(self, session_id: Optional[str]) -> Optional[str]:
        """The desk name for a session, whether or not the line names it
        aloud (the speech history records who sent each line)."""
        return self._names.name_for(session_id)

    def spoken(self, session_id: Optional[str]) -> None:
        """The line for `session_id` was heard: it is now the last speaker.
        A line with no session moves nothing."""
        sid = (session_id or "").strip() or None
        if sid is None:
            return
        self._last_session = sid
        self._last_name = self._names.name_for(sid) or UNKNOWN_NAME
        self._last_at = self._now()

    def status(self) -> dict[str, Any]:
        settings = self._settings_now()
        return {
            "enabled": settings.enabled,
            "quiet_gap_seconds": settings.quiet_gap_seconds,
            "last_speaker": self._last_name,
            "last_session_id": self._last_session,
            "last_spoken_at": self._last_at,
        }


# --------------------------------------------------------------------------
# One verb for the CLI and the MCP tool
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    text: str


def _gap_text(seconds: float) -> str:
    if seconds >= 60 and seconds % 60 == 0:
        return f"{int(seconds // 60)} min"
    return f"{seconds:g} s"


def format_settings(settings: SpeakerSettings, names: DeskNames) -> str:
    lines = [
        f"speaker names: {'on' if settings.enabled else 'off'}",
        f"quiet gap: {_gap_text(settings.quiet_gap_seconds)} (a desk quiet that long names itself again)",
        f"desks known: {names.count()} (from {names.agents_dir})",
    ]
    if settings.names:
        lines.append("named by config:")
        for sid, name in settings.names:
            lines.append(f"  {sid} = {name}")
    if settings.config_path:
        lines.append(f"config: {settings.config_path}")
    for p in settings.problems:
        lines.append(f"warning: {p}")
    return "\n".join(lines)


def run_action(
    action: str | None,
    *,
    seconds: float | None = None,
    session_id: str | None = None,
    name: str | None = None,
    store: SpeakerStore | None = None,
    names: DeskNames | None = None,
) -> ActionResult:
    """`auto-speech speaker <action> …` and the MCP tool `speaker(action, …)`."""
    store = store or SpeakerStore()
    action = (action or "status").strip().lower()
    if action not in ACTIONS:
        return ActionResult(False, f"speaker: unknown action {action!r} (expected {' | '.join(ACTIONS)})")
    settings = store.load()
    if names is None:
        names = DeskNames(settings.agents_dir, overrides=dict(settings.names))
    else:
        names.set_overrides(dict(settings.names))
    sid = (session_id or "").strip()

    if action == "status":
        return ActionResult(True, format_settings(settings, names))
    if action in ("on", "off"):
        return ActionResult(True, format_settings(store.set_enabled(action == "on"), names))
    if action == "gap":
        if seconds is None or isinstance(seconds, bool):
            return ActionResult(False, "speaker gap: give the quiet gap in seconds (e.g. 900 for 15 minutes)")
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            return ActionResult(False, f"speaker gap: not a number: {seconds!r}")
        if value < 0:
            return ActionResult(False, "speaker gap: seconds must be >= 0")
        return ActionResult(True, format_settings(store.set_quiet_gap(value), names))
    if action == "name":
        if not sid or not (name or "").strip():
            return ActionResult(False, "speaker name: give a session id and the name to say for it")
        settings = store.set_name(sid, name.strip())
        names.set_overrides(dict(settings.names))
        return ActionResult(True, f"{sid} will be named {name.strip()!r}\n" + format_settings(settings, names))
    if action == "forget":
        if not sid:
            return ActionResult(False, "speaker forget: give the session id to forget")
        settings, dropped = store.forget_name(sid)
        names.set_overrides(dict(settings.names))
        head = f"forgot the name for {sid}" if dropped else f"nothing to forget: {sid} had no name in the config"
        return ActionResult(True, head + "\n" + format_settings(settings, names))
    # who
    if not sid:
        return ActionResult(False, "speaker who: give a session id (or run it from a Claude Code shell)")
    found = names.name_for(sid)
    if found:
        return ActionResult(True, f"{sid} speaks as {found!r}")
    return ActionResult(True, f"{sid} has no desk note; it would be announced as {UNKNOWN_NAME!r} (set one with: speaker name)")
