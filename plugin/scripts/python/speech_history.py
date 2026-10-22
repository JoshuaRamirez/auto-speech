"""The speech history: every line auto-speech played, as a WAV and a sidecar.

His 13:38, 2026-10-20 (the Master Desk #m1020-1339-68ee): "Each time the
Desk speaks, a small player pops up and plays the line's WAV … A history
button shows everything said before. Only the last N WAVs are kept (N
configurable), so the disk never fills." His 14:50 (#m1020-1451-d9e7): "I
want you to save WAV files up to, I don't know, the size of a small,
medium-sized video game."

Auto-speech owns the history; the player only shows what it is handed. It
is the one WAV store: Spoken Transcript reads it for its transcript page
and never writes it. Three parts, each testable on its own:

- HistorySettings / HistoryStore: ~/.config/auto-speech/history.toml
  ([history] enabled, max_gb, keep, player). `max_gb` bounds the store on
  disk (20 GB by default), oldest pruned first; `keep` is only how many of
  the newest lines the player's history view and `history list` show. Each
  has the same name in the config, the CLI (`auto-speech history max-gb
  20`, `history keep 200`) and the MCP tool (`history(action="max-gb",
  gb=20)`, `history(action="keep", count=200)`). ADR-020.
- SpeechHistory: the store on disk. One line is two files under the
  history dir, `<stamp>-<id>.wav` and `<stamp>-<id>.json` (the sidecar);
  the sidecar is written last, so a line with no sidecar is not in the
  history. After each export the oldest lines are deleted until the store
  is within `max_gb`; the prune reads only file names and sizes, never the
  sidecars, so it stays cheap at tens of thousands of lines.
- estimate_word_timings: `say -o` gives no word timings, so each word's
  span is estimated from its length and the pause after it, and the
  sidecar says so (`word_timings.source = "estimate"`).

Only a line that was heard is exported: the daemon calls `export()` after
the sink played it, so a line the meeting hold or the sound yield set
aside has nothing exported until it plays. `run_action` is the one verb
behind `auto-speech history …` and the MCP tool `history`. See
docs/decisions/ADR-020.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import shutil
import sys
import time
import tomllib
import wave
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

CONFIG_FILE_NAME = "history.toml"
CONFIG_ENV = "AUTO_SPEECH_HISTORY_CONFIG"
DIR_ENV = "AUTO_SPEECH_HISTORY_DIR"
SECTION = "history"

DEFAULT_ENABLED = True
DEFAULT_MAX_GB = 20.0  # ADR-020: "a small, medium-sized video game"; about 21,000 lines at the measured mean clip
DEFAULT_KEEP = 200  # the player's view: the newest 200 lines, about the last 4 h of his busiest day
BYTES_PER_GB = 1_000_000_000
DEFAULT_PLAYER = False  # the player opens after a line only when he turns it on
MIN_KEEP = 1
DEFAULT_RATE = 1.0  # the narrator's playback rate; kept here so a restart keeps it (ADR-023)
MIN_RATE, MAX_RATE = 0.25, 4.0  # what mpv's --speed is clamped to
DEFAULT_SHOW_LIST = False  # whether the control opens with its list shown

SIDECAR_VERSION = 1
TIMING_ESTIMATE = "estimate"
TIMING_SYNTHESIZER = "synthesizer"
ESTIMATE_METHOD = "word length in characters, plus a pause after commas and sentence ends, spread over the clip's duration"

ORPHAN_GRACE_SECONDS = 60.0  # a WAV without its sidecar, or a .tmp, this young may be an export still being written

PRIORITY_NAMES = {1: "user interrupt", 2: "explicit", 3: "autoplay", 4: "narration"}

ACTIONS = ("list", "status", "on", "off", "max-gb", "keep", "player-on", "player-off")


def default_config_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override)
    return Path.home() / ".config" / "auto-speech" / CONFIG_FILE_NAME


def default_history_dir() -> Path:
    override = os.environ.get(DIR_ENV)
    if override:
        return Path(override)
    return Path.home() / ".local" / "state" / "auto-speech" / "history"


# --------------------------------------------------------------------------
# Settings file
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class HistorySettings:
    enabled: bool = DEFAULT_ENABLED
    max_gb: float = DEFAULT_MAX_GB
    keep: int = DEFAULT_KEEP
    player: bool = DEFAULT_PLAYER
    rate: float = DEFAULT_RATE
    show_list: bool = DEFAULT_SHOW_LIST
    config_path: Optional[str] = None
    problems: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, section: object, config_path: Optional[str] = None) -> "HistorySettings":
        problems: list[str] = []
        if not isinstance(section, dict):
            section = {}
            if config_path is not None:
                problems.append(f"[{SECTION}] is not a table; using defaults")

        enabled = section.get("enabled", DEFAULT_ENABLED)
        if not isinstance(enabled, bool):
            problems.append(f"[{SECTION}] enabled should be true or false, got {enabled!r}")
            enabled = DEFAULT_ENABLED

        max_gb = section.get("max_gb", DEFAULT_MAX_GB)
        if isinstance(max_gb, bool) or not isinstance(max_gb, (int, float)) or max_gb <= 0:
            problems.append(f"[{SECTION}] max_gb should be a number of gigabytes > 0, got {max_gb!r}")
            max_gb = DEFAULT_MAX_GB

        keep = section.get("keep", DEFAULT_KEEP)
        if isinstance(keep, bool) or not isinstance(keep, int) or keep < MIN_KEEP:
            problems.append(f"[{SECTION}] keep should be a whole number >= {MIN_KEEP}, got {keep!r}")
            keep = DEFAULT_KEEP

        player = section.get("player", DEFAULT_PLAYER)
        if not isinstance(player, bool):
            problems.append(f"[{SECTION}] player should be true or false, got {player!r}")
            player = DEFAULT_PLAYER

        rate = section.get("rate", DEFAULT_RATE)
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not MIN_RATE <= rate <= MAX_RATE:
            problems.append(f"[{SECTION}] rate should be a number from {MIN_RATE:g} to {MAX_RATE:g}, got {rate!r}")
            rate = DEFAULT_RATE

        show_list = section.get("show_list", DEFAULT_SHOW_LIST)
        if not isinstance(show_list, bool):
            problems.append(f"[{SECTION}] show_list should be true or false, got {show_list!r}")
            show_list = DEFAULT_SHOW_LIST

        return cls(
            enabled=enabled,
            max_gb=float(max_gb),
            keep=int(keep),
            player=player,
            rate=float(rate),
            show_list=show_list,
            config_path=config_path,
            problems=tuple(problems),
        )

    def to_toml(self) -> str:
        lines = [
            "# auto-speech: every line played, kept as a WAV and a sidecar, up to max_gb on disk (ADR-020).",
            f"[{SECTION}]",
            f"enabled = {'true' if self.enabled else 'false'}",
            f"max_gb = {self.max_gb:g}",
            f"keep = {self.keep}",
            f"player = {'true' if self.player else 'false'}",
            f"rate = {self.rate:g}",
            f"show_list = {'true' if self.show_list else 'false'}",
        ]
        return "\n".join(lines) + "\n"


class HistoryStore:
    """Reads and writes history.toml. Missing file = defaults."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else default_config_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> HistorySettings:
        p = self._path
        if not p.is_file():
            return HistorySettings()
        try:
            raw = tomllib.loads(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            print(f"[history] could not parse {p}: {exc}; using defaults", file=sys.stderr)
            return HistorySettings(config_path=str(p), problems=(f"{p}: could not parse TOML ({exc})",))
        section = raw.get(SECTION) if isinstance(raw, dict) else None
        if section is None:
            return HistorySettings(config_path=str(p))
        return HistorySettings.from_mapping(section, config_path=str(p))

    def save(self, settings: HistorySettings) -> HistorySettings:
        p = self._path
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(settings.to_toml(), encoding="utf-8")
        os.replace(tmp, p)
        return replace(settings, config_path=str(p), problems=())

    def set_enabled(self, enabled: bool) -> HistorySettings:
        return self.save(replace(self.load(), enabled=bool(enabled)))

    def set_max_gb(self, max_gb: float) -> HistorySettings:
        return self.save(replace(self.load(), max_gb=float(max_gb)))

    def set_keep(self, keep: int) -> HistorySettings:
        return self.save(replace(self.load(), keep=int(keep)))

    def set_player(self, player: bool) -> HistorySettings:
        return self.save(replace(self.load(), player=bool(player)))

    def set_rate(self, rate: float) -> HistorySettings:
        rate = float(rate)
        if not MIN_RATE <= rate <= MAX_RATE:
            raise ValueError(f"rate must be from {MIN_RATE:g} to {MAX_RATE:g}, got {rate:g}")
        return self.save(replace(self.load(), rate=rate))

    def set_show_list(self, show_list: bool) -> HistorySettings:
        return self.save(replace(self.load(), show_list=bool(show_list)))


# --------------------------------------------------------------------------
# Word timings
# --------------------------------------------------------------------------

_WORD = re.compile(r"\S+")
_SENTENCE_END = re.compile(r"[.!?…:;]+[\"'”’)\]]*$")
_CLAUSE_END = re.compile(r"[,—–]+[\"'”’)\]]*$")
SENTENCE_PAUSE_CHARS = 6.0  # a sentence end weighs like six more letters of speech
CLAUSE_PAUSE_CHARS = 3.0


# `say`'s embedded commands, e.g. [[rate 180]] or [[slnc 600]]: heard as
# speed or silence, never as words.
_SAY_COMMAND = re.compile(r"\[\[\s*([A-Za-z]+)\s*([^\]]*?)\s*\]\]")


def strip_say_markup(text: str) -> str:
    """`text` as heard: `say`'s [[…]] commands removed and the spaces
    they leave closed up. Text without a command is returned as it is."""
    text = text or ""
    if not _SAY_COMMAND.search(text):
        return text
    out = _SAY_COMMAND.sub(" ", text)
    out = re.sub(r"[ \t]*\n[ \t]*", "\n", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def _words_and_silences(text: str) -> tuple[list[str], dict[int, float]]:
    """The words of `text` without `say` commands, and the seconds of
    [[slnc N]] silence after word i (-1: before the first word)."""
    words: list[str] = []
    silences: dict[int, float] = {}
    pos = 0
    for m in _SAY_COMMAND.finditer(text or ""):
        words += _WORD.findall(text[pos:m.start()])
        pos = m.end()
        if m.group(1).lower() == "slnc":
            try:
                ms = float(m.group(2))
            except ValueError:
                continue
            if ms > 0:
                key = len(words) - 1
                silences[key] = silences.get(key, 0.0) + ms / 1000.0
    words += _WORD.findall((text or "")[pos:])
    return words, silences


def estimate_word_timings(text: str, duration: float) -> list[dict[str, Any]]:
    """Each word of `text` with an estimated start and end in seconds, in
    the UI Utility's shape (`{"w", "start", "end"}`). A word's share of the
    clip is its letters plus the pause after it; the pause is silence, so a
    word ends before the next one starts. `say` commands are not words: a
    [[slnc N]] is N ms of silence at its place, the rest are dropped. Empty
    when there is no text or no duration."""
    words, silences = _words_and_silences(text)
    if not words or not duration or duration <= 0:
        return []
    silences = {i: sec for i, sec in silences.items() if i < len(words) - 1}  # trailing silence ends the clip anyway
    fixed = sum(silences.values())
    if fixed >= duration * 0.9:  # silences that leave no room to speak: do not trust them
        silences, fixed = {}, 0.0
    spans: list[tuple[str, float, float]] = []
    for w in words:
        speech = float(max(1, len(re.sub(r"\W", "", w)) or len(w)))
        if _SENTENCE_END.search(w):
            pause = SENTENCE_PAUSE_CHARS
        elif _CLAUSE_END.search(w):
            pause = CLAUSE_PAUSE_CHARS
        else:
            pause = 0.0
        spans.append((w, speech, pause))
    spans[-1] = (spans[-1][0], spans[-1][1], 0.0)  # nothing to pause before after the last word
    total = sum(s + p for _, s, p in spans)
    scale = (float(duration) - fixed) / total
    out: list[dict[str, Any]] = []
    t = silences.get(-1, 0.0)
    for i, (w, speech, pause) in enumerate(spans):
        start = t
        end = start + speech * scale
        out.append({"w": w, "start": round(start, 3), "end": round(end, 3)})
        t = end + pause * scale + silences.get(i, 0.0)
    out[-1]["end"] = round(float(duration), 3)
    return out


def clean_origin_file(raw: Any) -> str:
    """The caller's origin file as a bare name (desk-say's
    `2026-10-20-150900.674-desk.txt`), or "" when there is none or it is not
    a usable name: a path is cut to its last part; empty, "." or "..",
    longer than 255 characters, or holding a control character is dropped."""
    if not isinstance(raw, str):
        return ""
    name = Path(raw.strip()).name
    if name in ("", ".", "..") or len(name) > 255 or any(ord(c) < 32 for c in name):
        return ""
    return name


def wav_duration_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            return w.getnframes() / float(rate) if rate else 0.0
    except (OSError, EOFError, wave.Error):
        return 0.0


# --------------------------------------------------------------------------
# The history on disk
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoryLine:
    """One exported line: its sidecar, read back."""

    id: str
    stem: str  # "<stamp>-<id>": the name of both files without the suffix
    sidecar: dict[str, Any]
    wav: Path
    json: Path

    @property
    def text(self) -> str:
        return str(self.sidecar.get("text") or "")

    @property
    def spoken_at(self) -> float:
        try:
            return float(self.sidecar.get("spoken_at_epoch") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def to_dict(self) -> dict[str, Any]:
        return {**self.sidecar, "wav_path": str(self.wav), "sidecar_path": str(self.json)}


class SpeechHistory:
    """The history dir. The daemon is the writer; `max-gb` from the CLI or
    MCP prunes too, under the same file lock. Every write is atomic (tmp +
    rename), WAV first and sidecar last."""

    def __init__(
        self,
        directory: Path | str | None = None,
        *,
        store: HistoryStore | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._dir = Path(directory) if directory is not None else default_history_dir()
        self._store = store or HistoryStore()
        self._clock = clock

    @property
    def directory(self) -> Path:
        return self._dir

    def settings(self) -> HistorySettings:
        return self._store.load()

    # -- locking ---------------------------------------------------------------

    def _locked(self, fn: Callable[[], Any]) -> Any:
        self._dir.mkdir(parents=True, exist_ok=True)
        with open(self._dir / ".lock", "a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                return fn()
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    # -- export ----------------------------------------------------------------

    def export(
        self,
        wav: Path | str,
        *,
        text: str,
        session_id: Optional[str] = None,
        sender: Optional[str] = None,
        priority: Optional[int] = None,
        voice_id: str = "",
        speed: float = 1.0,
        source_hash: Optional[str] = None,
        word_timings: Optional[list[dict[str, Any]]] = None,
        spoken_at: Optional[float] = None,
        origin_file: Optional[str] = None,
    ) -> Optional[HistoryLine]:
        """Copy the played WAV into the history with its sidecar, then prune
        to `max_gb`. None when the history is off. `word_timings` from a
        synthesizer that gives them are kept as given; otherwise they are
        estimated and marked so. `origin_file` is the file the caller says
        the line came from (desk-say's pending file), its name only."""
        settings = self._store.load()
        if not settings.enabled:
            return None
        src = Path(wav)
        when = float(spoken_at) if spoken_at is not None else self._clock()
        line_id = secrets.token_hex(4)
        local = datetime.fromtimestamp(when)
        # The name is in UTC so names sort in time order across a clock change
        # (the hour repeated in autumn); the sidecar keeps the local time.
        utc = datetime.fromtimestamp(when, timezone.utc)
        stem = f"{utc.strftime('%Y%m%d-%H%M%S')}-{int((when % 1) * 1000):03d}-{line_id}"
        duration = wav_duration_seconds(src)
        if word_timings is not None:
            timings = {"source": TIMING_SYNTHESIZER, "words": list(word_timings)}
        else:
            timings = {
                "source": TIMING_ESTIMATE,
                "method": ESTIMATE_METHOD,
                "words": estimate_word_timings(text, duration),
            }
        p = int(priority) if priority is not None else None
        sidecar = {
            "version": SIDECAR_VERSION,
            "id": line_id,
            "text": strip_say_markup(text),
            "spoken_at": local.isoformat(timespec="milliseconds"),
            "spoken_at_epoch": round(when, 3),
            "sender": {"session_id": session_id or None, "name": sender or None},
            "priority": p,
            "priority_name": PRIORITY_NAMES.get(p) if p is not None else None,
            "duration_seconds": round(duration, 3),
            "word_timings": timings,
            "wav": f"{stem}.wav",
            "voice_id": voice_id or "",
            "speed": float(speed),
            "source_hash": source_hash or None,
            "origin_file": clean_origin_file(origin_file) or None,
        }

        def go() -> HistoryLine:
            dst_wav = self._dir / f"{stem}.wav"
            dst_json = self._dir / f"{stem}.json"
            tmp_wav = self._dir / f".{stem}.wav.tmp"
            shutil.copyfile(src, tmp_wav)
            os.replace(tmp_wav, dst_wav)
            tmp_json = self._dir / f".{stem}.json.tmp"
            tmp_json.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp_json, dst_json)
            self._prune_unlocked(settings.max_gb)
            return HistoryLine(id=line_id, stem=stem, sidecar=sidecar, wav=dst_wav, json=dst_json)

        return self._locked(go)

    # -- reads -------------------------------------------------------------------

    def _scan_unlocked(self) -> tuple[list[tuple[str, int]], list[tuple[Path, float]]]:
        """One directory listing, no sidecar read: (stem, bytes of its WAV
        and sidecar) for every complete line, oldest first; and the leftovers
        of an export cut short (a WAV with no sidecar, a .tmp) with their
        mtimes."""
        if not self._dir.is_dir():
            return [], []
        sizes: dict[str, int] = {}
        mtimes: dict[str, float] = {}
        stems: set[str] = set()
        leftovers: list[tuple[Path, float]] = []
        with os.scandir(self._dir) as it:
            for e in it:
                name = e.name
                if name.startswith("."):
                    if name.endswith(".tmp"):
                        try:
                            leftovers.append((Path(e.path), e.stat(follow_symlinks=False).st_mtime))
                        except OSError:
                            pass
                    continue
                if not (name.endswith(".json") or name.endswith(".wav")):
                    continue
                stem, _, ext = name.rpartition(".")
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                sizes[stem] = sizes.get(stem, 0) + st.st_size
                if ext == "json":
                    stems.add(stem)
                else:
                    mtimes[stem] = st.st_mtime
        for stem, mtime in mtimes.items():
            if stem not in stems:
                leftovers.append((self._dir / f"{stem}.wav", mtime))
        return [(stem, sizes.get(stem, 0)) for stem in sorted(stems)], leftovers

    def _entries_unlocked(self) -> list[tuple[str, int]]:
        return self._scan_unlocked()[0]

    def _line(self, stem: str) -> Optional[HistoryLine]:
        js = self._dir / f"{stem}.json"
        try:
            data = json.loads(js.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return HistoryLine(
            id=str(data.get("id") or stem.rsplit("-", 1)[-1]),
            stem=stem,
            sidecar=data,
            wav=self._dir / f"{stem}.wav",
            json=js,
        )

    def load(self, limit: Optional[int] = None) -> list[HistoryLine]:
        """The lines in the history, oldest first; with `limit`, only the
        newest `limit` of them (and only those sidecars are read)."""
        stems = [stem for stem, _ in self._entries_unlocked()]
        if limit is not None:
            stems = stems[-max(0, int(limit)):] if limit > 0 else []
        return [line for line in (self._line(stem) for stem in stems) if line is not None]

    def count(self) -> int:
        return len(self._entries_unlocked())

    def get(self, line_id: str) -> Optional[HistoryLine]:
        for stem, _ in reversed(self._entries_unlocked()):
            if stem == line_id or stem.rsplit("-", 1)[-1] == line_id:
                return self._line(stem)
        return None

    def size_bytes(self) -> int:
        return sum(size for _, size in self._entries_unlocked())

    # -- pruning ---------------------------------------------------------------

    def _prune_unlocked(self, max_gb: float) -> int:
        """Delete the oldest lines until the store is within `max_gb`, and
        any WAV left without its sidecar (an export cut short). The newest
        line always stays. Returns how many lines went."""
        entries, leftovers = self._scan_unlocked()
        limit = float(max_gb) * BYTES_PER_GB
        total = sum(size for _, size in entries)
        removed = 0
        for stem, size in entries[:-1]:
            if total <= limit:
                break
            (self._dir / f"{stem}.wav").unlink(missing_ok=True)
            (self._dir / f"{stem}.json").unlink(missing_ok=True)
            total -= size
            removed += 1
        now = self._clock()
        for path, mtime in leftovers:
            if now - mtime > ORPHAN_GRACE_SECONDS:
                path.unlink(missing_ok=True)
        return removed

    def prune(self, max_gb: Optional[float] = None) -> int:
        gb = float(max_gb) if max_gb is not None else self._store.load().max_gb
        if not self._dir.is_dir():
            return 0
        return self._locked(lambda: self._prune_unlocked(gb))


# --------------------------------------------------------------------------
# One verb for the CLI and the MCP tool
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    text: str


def _mb(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB"


def _gb(gb: float) -> str:
    return f"{gb:g} GB"


def format_status(settings: HistorySettings, history: SpeechHistory, player_note: str = "") -> str:
    n = history.count()
    out = [
        f"speech history: {'on' if settings.enabled else 'off'}",
        f"max_gb: {_gb(settings.max_gb)} on disk, the oldest pruned first",
        f"held now: {n} line{'s' if n != 1 else ''}, {_mb(history.size_bytes())}",
        f"keep: the player's view shows the newest {settings.keep} line{'s' if settings.keep != 1 else ''}",
        f"speech control: {'on' if settings.player else 'off'}" + (f" ({player_note})" if player_note else ""),
        f"dir: {history.directory}",
    ]
    if settings.config_path:
        out.append(f"config: {settings.config_path}")
    for p in settings.problems:
        out.append(f"warning: {p}")
    return "\n".join(out)


def _hms(epoch: float) -> str:
    try:
        return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "----------- --:--:--"


def format_lines(lines: list[HistoryLine], total: int) -> str:
    if not lines:
        return "speech history: nothing played yet"
    head = f"speech history: the last {len(lines)} of {total} line{'s' if total != 1 else ''}, newest first"
    out = [head]
    for line in lines:
        sc = line.sidecar
        who = (sc.get("sender") or {}).get("name") or "-"
        dur = float(sc.get("duration_seconds") or 0.0)
        est = " (timings estimated)" if (sc.get("word_timings") or {}).get("source") == TIMING_ESTIMATE else ""
        out.append(f"  {line.id}  {_hms(line.spoken_at)}  {dur:.1f} s  {who}{est}")
        out.append(f"      {line.text}")
        out.append(f"      {line.wav}")
    return "\n".join(out)


def run_action(
    action: str | None,
    *,
    count: int | None = None,
    gb: float | None = None,
    as_json: bool = False,
    store: HistoryStore | None = None,
    history: SpeechHistory | None = None,
    player_note: Callable[[HistorySettings], str] | None = None,
) -> ActionResult:
    """`auto-speech history <action> …` and the MCP tool `history(action, …)`.
    `count` is the number for `keep`, and how many lines `list` shows (the
    newest `keep` by default); `gb` is the number for `max-gb`."""
    store = store or HistoryStore()
    history = history or SpeechHistory(store=store)
    action = (action or "list").strip().lower()
    if action not in ACTIONS:
        return ActionResult(False, f"history: unknown action {action!r} (expected {' | '.join(ACTIONS)})")

    def status(settings: HistorySettings, head: str = "") -> ActionResult:
        note = player_note(settings) if player_note is not None else ""
        body = format_status(settings, history, note)
        return ActionResult(True, (head + "\n" if head else "") + body)

    if action == "list":
        limit = store.load().keep
        if count is not None:
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                return ActionResult(False, f"history list: count must be a whole number >= 1, got {count!r}")
            limit = count
        total = history.count()
        shown = list(reversed(history.load(limit=limit)))
        if as_json:
            return ActionResult(True, json.dumps({"total": total, "lines": [x.to_dict() for x in shown]}, indent=2, ensure_ascii=False))
        return ActionResult(True, format_lines(shown, total))
    if action == "status":
        return status(store.load())
    if action in ("on", "off"):
        return status(store.set_enabled(action == "on"))
    if action in ("player-on", "player-off"):
        return status(store.set_player(action == "player-on"))
    if action == "max-gb":
        if gb is None or isinstance(gb, bool) or not isinstance(gb, (int, float)):
            return ActionResult(False, f"history max-gb: give the most the store may hold, in GB (default {DEFAULT_MAX_GB:g})")
        if gb <= 0:
            return ActionResult(False, f"history max-gb: must be > 0, got {gb:g}")
        settings = store.set_max_gb(float(gb))
        removed = history.prune(float(gb))
        head = f"pruned the {removed} oldest line{'s' if removed != 1 else ''}" if removed else ""
        return status(settings, head)
    # keep: the player's view only; nothing is deleted
    if count is None or isinstance(count, bool) or not isinstance(count, int):
        return ActionResult(False, f"history keep: give how many of the newest lines the player shows (default {DEFAULT_KEEP})")
    if count < MIN_KEEP:
        return ActionResult(False, f"history keep: must be >= {MIN_KEEP}, got {count}")
    return status(store.set_keep(count))
