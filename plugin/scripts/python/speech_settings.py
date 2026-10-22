"""auto-speech's settings in the UI Utility's settings window (contract v1
rev 1.3, the settings piece; ADR-023).

The UI Utility shows the items and holds the values shown; this module says
what each item is, reads its value from where the setting lives, and applies
a change there:

  Speech
    mute          switch  ~/.claude/auto-speech.disabled (the global mute marker)
    autoplay_mode choice  autoplay.toml [autoplay] mode: verbatim | summary
    summary_size  choice  autoplay.toml [autoplay] summary_size: small | medium | large
    sound_yield   switch  sound-yield.toml [sound_yield] enabled
    rate          number  history.toml [history] rate, applied to the narrator now
    speaker       switch  speaker.toml [speaker] enabled
  Transcript
    show_list     switch  history.toml [history] show_list (the control's list at open)
    keep          number  history.toml [history] keep (lines the control lists)
    player        switch  history.toml [history] player (the control and this piece)
    clear_missed  action  the missed list, cleared (auto-speech missed --clear)

Each of those files is read again where it is used (the gate, the announcer,
the autoplay worker, the history), so a change written there is live. The
rate is the one hot-applied value: it goes to the narrator's sink too. This
module never writes the UI Utility's files.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import autoplay_config
from missed_speech import MissedList
from sound_yield import SoundYieldStore
from speaker import SpeakerStore
from speech_history import HistoryStore

SETTINGS = "settings"  # the piece, as hello names it
SETTINGS_ID = "auto-speech-settings"  # ids are one space across pieces: "auto-speech" is the control
SOURCE_NAME = "Speech"

RATE_MIN, RATE_MAX, RATE_STEP = 0.75, 2.0, 0.25
KEEP_MIN, KEEP_MAX, KEEP_STEP = 10, 1000, 10

MODES = (("verbatim", "Every word"), ("summary", "A summary"))
RAW_MODE = ("raw", "As written, no rewrite")  # shown only while autoplay.toml says raw
SIZES = (("small", "Small"), ("medium", "Medium"), ("large", "Large"))

ITEM_IDS = (
    "mute", "autoplay_mode", "summary_size", "sound_yield", "rate", "speaker",
    "show_list", "keep", "player", "clear_missed",
)


def default_autoplay_path() -> Path:
    """Where `auto-speech autoplay-mode` writes: the override, else the user file."""
    override = os.environ.get("AUTO_SPEECH_AUTOPLAY_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".config" / "auto-speech" / "autoplay.toml"


def default_mute_marker() -> Path:
    return Path.home() / ".claude" / "auto-speech.disabled"


def _clamp(value: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, value))


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    raise ValueError(f"cannot write {type(v).__name__} values back to TOML")


def _dump_toml(doc: dict[str, Any]) -> str:
    """Scalar keys and one level of tables: autoplay.toml's whole shape."""
    out: list[str] = [f"{k} = {_toml_value(v)}" for k, v in doc.items() if not isinstance(v, dict)]
    for name, table in doc.items():
        if not isinstance(table, dict):
            continue
        if out:
            out.append("")
        out.append(f"[{name}]")
        for k, v in table.items():
            if isinstance(v, dict):
                raise ValueError(f"[{name}.{k}] is a nested table; not rewritten")
            out.append(f"{k} = {_toml_value(v)}")
    return "\n".join(out) + "\n"


@dataclass
class SettingsPlaces:
    """Where each setting lives. Defaults are the live files; tests pass their own."""

    mute_marker: Path = field(default_factory=default_mute_marker)
    autoplay_path: Path = field(default_factory=default_autoplay_path)
    autoplay_default: Path = field(
        default_factory=lambda: autoplay_config._project_root() / "config" / "autoplay.toml.example"
    )
    sound_yield: SoundYieldStore = field(default_factory=SoundYieldStore)
    speaker: SpeakerStore = field(default_factory=SpeakerStore)
    history: HistoryStore = field(default_factory=HistoryStore)
    missed: MissedList = field(default_factory=MissedList)


class SettingError(Exception):
    """A change that could not be applied; the old value goes back."""


class SpeechSettings:
    def __init__(
        self,
        places: SettingsPlaces | None = None,
        *,
        set_rate: Callable[[float], float] | None = None,
    ) -> None:
        self.places = places or SettingsPlaces()
        # The narrator's rate, applied now; returns the rate in effect.
        self._set_rate = set_rate or (lambda rate: rate)

    # -- reading -------------------------------------------------------------------

    def _autoplay(self) -> dict[str, Any]:
        for p in (self.places.autoplay_path, self.places.autoplay_default):
            try:
                raw = tomllib.loads(p.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
                continue
            section = raw.get("autoplay") if isinstance(raw, dict) else None
            if isinstance(section, dict):
                return section
        return {}

    def _mode(self) -> str:
        section = self._autoplay()
        mode = str(section.get("mode", "summary")).lower()
        if mode in ("raw", "bypass", "as_is", "direct") or section.get("bypass_llm") or section.get("bypass_rewrite"):
            return "raw"
        return mode if mode in ("verbatim", "summary") else "summary"

    def _size(self) -> str:
        size = str(self._autoplay().get("summary_size", "small")).lower()
        return size if size in autoplay_config.VALID_SIZES else "small"

    def values(self) -> dict[str, Any]:
        h = self.places.history.load()
        return {
            "mute": self.places.mute_marker.exists(),
            "autoplay_mode": self._mode(),
            "summary_size": self._size(),
            "sound_yield": bool(self.places.sound_yield.load().enabled),
            "rate": _clamp(h.rate, RATE_MIN, RATE_MAX),
            "speaker": bool(self.places.speaker.load().enabled),
            "show_list": h.show_list,
            "keep": int(_clamp(h.keep, KEEP_MIN, KEEP_MAX)),
            "player": h.player,
        }

    def groups(self) -> list[dict[str, Any]]:
        v = self.values()
        modes = [*MODES, RAW_MODE] if v["autoplay_mode"] == "raw" else list(MODES)
        return [
            {"id": "speech", "title": "Speech", "items": [
                {"id": "mute", "label": "Mute", "kind": "switch", "value": v["mute"],
                 "help": "Holds every line, in every session"},
                {"id": "autoplay_mode", "label": "Autoplay reads", "kind": "choice", "value": v["autoplay_mode"],
                 "choices": [{"id": i, "label": label} for i, label in modes]},
                {"id": "summary_size", "label": "Summary size", "kind": "choice", "value": v["summary_size"],
                 "choices": [{"id": i, "label": label} for i, label in SIZES],
                 "help": "Used when autoplay reads a summary"},
                {"id": "sound_yield", "label": "Hold speech while another app plays sound", "kind": "switch",
                 "value": v["sound_yield"]},
                {"id": "rate", "label": "Rate", "kind": "number", "value": v["rate"],
                 "min": RATE_MIN, "max": RATE_MAX, "step": RATE_STEP},
                {"id": "speaker", "label": "Name the speaker", "kind": "switch", "value": v["speaker"],
                 "help": "Says which desk is speaking when the speaker changes"},
            ]},
            {"id": "transcript", "title": "Transcript", "items": [
                {"id": "show_list", "label": "Show the transcript when the control opens", "kind": "switch",
                 "value": v["show_list"]},
                {"id": "keep", "label": "Lines kept", "kind": "number", "value": v["keep"],
                 "min": KEEP_MIN, "max": KEEP_MAX, "step": KEEP_STEP,
                 "help": "How many of the newest heard lines the control lists"},
                {"id": "player", "label": "Speech control", "kind": "switch", "value": v["player"],
                 "help": "Off closes the control and these settings"},
                {"id": "clear_missed", "label": "Clear the missed list", "kind": "action"},
            ]},
        ]

    # -- applying ----------------------------------------------------------------

    def apply(self, item: str, value: Any) -> dict[str, Any]:
        """Apply one change where the setting lives. Returns the values to
        send back (what is in effect now). Raises SettingError when the change
        could not be applied; the caller sends the old value back."""
        if item not in ITEM_IDS:
            raise SettingError(f"no setting {item!r}")
        try:
            getattr(self, f"_apply_{item}")(value)
        except SettingError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise SettingError(f"{item}: {exc}") from exc
        if item == "clear_missed":
            return {}
        values = self.values()
        out = {item: values[item]}
        if item == "autoplay_mode":
            out["summary_size"] = values["summary_size"]
        return out

    @staticmethod
    def _switch(value: Any) -> bool:
        if not isinstance(value, bool):
            raise SettingError(f"a switch takes true or false, got {value!r}")
        return value

    @staticmethod
    def _number(value: Any, lo: float, hi: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
            raise SettingError(f"a number from {lo:g} to {hi:g}, got {value!r}")
        return float(value)

    def _apply_mute(self, value: Any) -> None:
        marker = self.places.mute_marker
        if self._switch(value):
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
        elif marker.exists():
            marker.unlink()

    def _write_autoplay(self, **changes: str) -> None:
        p = self.places.autoplay_path
        doc: dict[str, Any] = {}
        if p.is_file():
            doc = tomllib.loads(p.read_text(encoding="utf-8"))
        section = doc.get("autoplay")
        if not isinstance(section, dict):
            section = {"mode": self._mode(), "summary_size": self._size()}
            doc["autoplay"] = section
        section.update(changes)
        body = _dump_toml(doc)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, p)

    def _apply_autoplay_mode(self, value: Any) -> None:
        if value not in ("verbatim", "summary", "raw"):
            raise SettingError(f"autoplay reads verbatim or summary, got {value!r}")
        changes = {"mode": value}
        doc_section = self._autoplay()
        if value != "raw" and (doc_section.get("bypass_llm") or doc_section.get("bypass_rewrite")):
            changes["bypass_llm"] = False  # bypass would otherwise keep it raw
            if "bypass_rewrite" in doc_section:
                changes["bypass_rewrite"] = False
        self._write_autoplay(**changes)

    def _apply_summary_size(self, value: Any) -> None:
        if value not in autoplay_config.VALID_SIZES:
            raise SettingError(f"summary size is small, medium or large, got {value!r}")
        self._write_autoplay(summary_size=value)

    def _apply_sound_yield(self, value: Any) -> None:
        self.places.sound_yield.set_enabled(self._switch(value))

    def _apply_rate(self, value: Any) -> None:
        rate = self._number(value, RATE_MIN, RATE_MAX)
        in_effect = float(self._set_rate(rate))
        self.places.history.set_rate(_clamp(in_effect, RATE_MIN, RATE_MAX))

    def _apply_speaker(self, value: Any) -> None:
        self.places.speaker.set_enabled(self._switch(value))

    def _apply_show_list(self, value: Any) -> None:
        self.places.history.set_show_list(self._switch(value))

    def _apply_keep(self, value: Any) -> None:
        keep = self._number(value, KEEP_MIN, KEEP_MAX)
        if keep != int(keep):
            raise SettingError(f"lines kept is a whole number, got {value!r}")
        self.places.history.set_keep(int(keep))

    def _apply_player(self, value: Any) -> None:
        self.places.history.set_player(self._switch(value))

    def _apply_clear_missed(self, value: Any) -> None:
        self.places.missed.clear()
