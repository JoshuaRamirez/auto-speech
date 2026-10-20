"""Sound yield: hold speech while another app is playing sound.

By default auto-speech does not START speaking while some other process
is producing audio (music, a video, a call). The playback it is about to
start is held, in queue order, until the other sound stops; past a longest
wait it is set aside as missed (never dropped): the daemon keeps it in
the missed list for `auto-speech missed` / `missed --play`. See ADR-015.

Detection uses Core Audio's process-object list
(`kAudioHardwarePropertyProcessObjectList`, with each process's
`kAudioProcessPropertyIsRunningOutput` flag, macOS 14.2+) read through
ctypes. No audio is captured or tapped, and no permission is needed to
read which processes are outputting. When the process list is not
available the default output device's
`kAudioDevicePropertyDeviceIsRunningSomewhere` is the fallback: it can
only say THAT sound plays, not who, so the exception list cannot apply
there.

auto-speech's own playback never counts: the calling process, the mpv it
spawned, and any process whose command line (or an ancestor's) carries
"auto-speech" are ignored.

A meeting is a second kind of blocker (his 2026-10-20 12:24: "if I'm in a
meeting, I don't want you to talk"). A process on the meeting-apps list
whose process object reports `kAudioProcessPropertyIsRunningInput` (the
same property walk, one more flag per object; no audio is captured) puts
the gate in a meeting, and so does his own time-boxed `yield meeting on`
(a small state file beside the missed list). A meeting never stalls the
queue: the hold expires at once with reason "meeting", and the daemon sets
the line aside on the missed list, then speaks one summary line when the
meeting ends. A process holding the microphone that is not a meeting app
(his CrownTalk, the system's speech daemon) is listed and never holds.

Three collaborators, all injectable for tests:

  SoundYieldStore     the settings file (~/.config/auto-speech/sound-yield.toml)
  CoreAudioProbe      who is outputting right now
  ProcessTable        pid -> (ppid, command), for names and own-process checks

and the SoundYieldGate that combines them. The CLI (`auto-speech yield`),
the MCP tool (`sound_yield`) and the config file all speak the same
names: enabled / exceptions / max_wait_seconds / poll_seconds.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import struct
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from state_paths import state_path
from typing import Callable, Iterable, Optional

logger = logging.getLogger(__name__)

CONFIG_FILE_NAME = "sound-yield.toml"
CONFIG_ENV = "AUTO_SPEECH_SOUND_YIELD_CONFIG"
SECTION = "sound_yield"

DEFAULT_ENABLED = True
DEFAULT_MAX_WAIT_SECONDS = 180.0
DEFAULT_POLL_SECONDS = 0.5

# Whose sound holds speech (ADR-017). "players": only apps on the players
# list; an unknown app's open stream never holds, because Core Audio can say
# that a process is running output but not whether that output is silent.
# "any": every app except the exceptions (the rule before 0.6.0).
HOLD_FOR_PLAYERS = "players"
HOLD_FOR_ANY = "any"
HOLD_FOR = (HOLD_FOR_PLAYERS, HOLD_FOR_ANY)
DEFAULT_HOLD_FOR = HOLD_FOR_PLAYERS

# Apps that play sound a person listens to. A bundle id matches itself and
# its helpers ("com.google.Chrome" covers "com.google.Chrome.helper"); a bare
# name matches a process name or a bundle id's last component.
DEFAULT_PLAYERS = (
    "com.apple.Music",
    "com.apple.TV",
    "com.apple.podcasts",
    "com.apple.QuickTimePlayerX",
    "com.apple.Safari",
    "com.apple.WebKit.GPU",  # Safari and other WebKit apps play media here
    "com.apple.FaceTime",
    "com.spotify.client",
    "org.videolan.vlc",
    "com.colliderli.iina",
    "io.mpv",
    "mpv",
    "afplay",
    "ffplay",
    "com.google.Chrome",
    "company.thebrowser.Browser",  # Arc
    "com.microsoft.edgemac",
    "org.mozilla.firefox",
    "org.mozilla.plugincontainer",  # Firefox plays media here
    "com.brave.Browser",
    "us.zoom.xos",
    "com.microsoft.teams2",
    "com.openai.chat",
    "com.anthropic.claudefordesktop",
)

# Apps whose open microphone means a meeting (his 2026-10-20 12:32: the
# listed meeting apps, Discord included). A browser on the list covers Meet;
# any other app holding the microphone is "mic in use, not a meeting app".
DEFAULT_MEETING_HOLD = True
DEFAULT_MEETING_APPS = (
    "com.microsoft.teams2",
    "com.microsoft.teams",
    "us.zoom.xos",
    "com.apple.FaceTime",
    "com.google.Chrome",
    "com.apple.Safari",
    "com.apple.WebKit.GPU",
    "company.thebrowser.Browser",  # Arc
    "com.microsoft.edgemac",
    "org.mozilla.firefox",
    "org.mozilla.plugincontainer",
    "com.brave.Browser",
    "com.tinyspeck.slackmacgap",  # Slack huddles
    "com.cisco.webexmeetingsapp",
    "com.webex.meetingmanager",
    "com.hnc.Discord",
)
DEFAULT_MEETING_SUMMARY = True
DEFAULT_MEETING_MINUTES = 60  # `yield meeting on` without a duration
MAX_MEETING_MINUTES = 8 * 60  # `yield meeting for 9h` is refused
MEETING_FILE_ENV = "AUTO_SPEECH_MEETING_FILE"

# Why the gate holds (Assessment.reason / HoldOutcome.reason).
REASON_SOUND = "sound"  # another app plays sound: wait, then set aside
REASON_MEETING = "meeting"  # a meeting: set aside at once, never wait

OWN_COMMAND_MARKER = "auto-speech"
MPV_PID_PATH = state_path("auto-speech-mpv.pid")

# Hold outcomes (HoldOutcome.status).
CLEAR = "clear"  # nothing else was playing; no hold
RESUMED = "resumed"  # held, the other sound stopped, play now
EXPIRED = "expired"  # held to the longest wait; set aside as missed, not played
ABORTED = "aborted"  # the caller cancelled the hold (interrupt)
DISABLED = "disabled"  # the behaviour is switched off
UNAVAILABLE = "unavailable"  # no probe could answer; fail open

# Assessment modes.
MODE_PROCESS_LIST = "process-list"
MODE_DEVICE_FALLBACK = "device-fallback"
MODE_DISABLED = "disabled"
MODE_UNAVAILABLE = "unavailable"


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def _normalize_name(name: str) -> str:
    return name.strip().lower()


@dataclass(frozen=True)
class SoundYieldSettings:
    """The user's sound-yield settings. Immutable; `replace()` to change."""

    enabled: bool = DEFAULT_ENABLED
    exceptions: tuple[str, ...] = ()
    hold_for: str = DEFAULT_HOLD_FOR
    players: tuple[str, ...] = DEFAULT_PLAYERS
    max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS
    poll_seconds: float = DEFAULT_POLL_SECONDS
    meeting_hold: bool = DEFAULT_MEETING_HOLD
    meeting_apps: tuple[str, ...] = DEFAULT_MEETING_APPS
    meeting_summary: bool = DEFAULT_MEETING_SUMMARY
    config_path: Optional[str] = None
    problems: tuple[str, ...] = ()  # what was ignored while loading, for the doctor/status

    @classmethod
    def from_mapping(cls, section: object, config_path: Optional[str] = None) -> "SoundYieldSettings":
        """Build settings from a parsed [sound_yield] table. Bad values fall
        back to defaults and are listed in `problems`; nothing raises."""
        problems: list[str] = []
        if not isinstance(section, dict):
            section = {}
            if config_path is not None:
                problems.append(f"[{SECTION}] is not a table; using defaults")

        enabled = section.get("enabled", DEFAULT_ENABLED)
        if not isinstance(enabled, bool):
            problems.append(f"[{SECTION}] enabled should be true or false, got {enabled!r}")
            enabled = DEFAULT_ENABLED

        exceptions = cls._name_list(section, "exceptions", [], problems)
        players = cls._name_list(section, "players", list(DEFAULT_PLAYERS), problems)

        hold_for = section.get("hold_for", DEFAULT_HOLD_FOR)
        if not isinstance(hold_for, str) or hold_for.strip().lower() not in HOLD_FOR:
            problems.append(f"[{SECTION}] hold_for should be {' or '.join(HOLD_FOR)}, got {hold_for!r}")
            hold_for = DEFAULT_HOLD_FOR
        hold_for = hold_for.strip().lower()

        max_wait = section.get("max_wait_seconds", DEFAULT_MAX_WAIT_SECONDS)
        if isinstance(max_wait, bool) or not isinstance(max_wait, (int, float)) or max_wait < 0:
            problems.append(f"[{SECTION}] max_wait_seconds should be a number >= 0, got {max_wait!r}")
            max_wait = DEFAULT_MAX_WAIT_SECONDS

        poll = section.get("poll_seconds", DEFAULT_POLL_SECONDS)
        if isinstance(poll, bool) or not isinstance(poll, (int, float)) or poll <= 0:
            problems.append(f"[{SECTION}] poll_seconds should be a number > 0, got {poll!r}")
            poll = DEFAULT_POLL_SECONDS

        meeting_hold = section.get("meeting_hold", DEFAULT_MEETING_HOLD)
        if not isinstance(meeting_hold, bool):
            problems.append(f"[{SECTION}] meeting_hold should be true or false, got {meeting_hold!r}")
            meeting_hold = DEFAULT_MEETING_HOLD
        meeting_apps = cls._name_list(section, "meeting_apps", list(DEFAULT_MEETING_APPS), problems)
        meeting_summary = section.get("meeting_summary", DEFAULT_MEETING_SUMMARY)
        if not isinstance(meeting_summary, bool):
            problems.append(f"[{SECTION}] meeting_summary should be true or false, got {meeting_summary!r}")
            meeting_summary = DEFAULT_MEETING_SUMMARY

        known = {
            "enabled", "exceptions", "hold_for", "players", "max_wait_seconds", "poll_seconds",
            "meeting_hold", "meeting_apps", "meeting_summary",
        }
        for key in section:
            if key == "after_max_wait":
                problems.append(
                    f"[{SECTION}] after_max_wait is gone since 0.4.0: a held speech is never dropped; "
                    "past the longest wait it goes to the missed list (ignored)"
                )
            elif key not in known:
                problems.append(f"[{SECTION}] unknown key {key!r} (ignored)")

        return cls(
            enabled=enabled,
            exceptions=tuple(exceptions),
            hold_for=hold_for,
            players=tuple(players),
            max_wait_seconds=float(max_wait),
            poll_seconds=float(poll),
            meeting_hold=meeting_hold,
            meeting_apps=tuple(meeting_apps),
            meeting_summary=meeting_summary,
            config_path=config_path,
            problems=tuple(problems),
        )

    @staticmethod
    def _name_list(section: dict, key: str, default: list[str], problems: list[str]) -> list[str]:
        """A list of app names from the table: one string is one entry; a bad
        value falls back to `default` and is reported; duplicates (case-
        insensitive) and blanks are dropped."""
        raw = section.get(key, None)
        if raw is None:
            return list(default)
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            problems.append(f"[{SECTION}] {key} should be a list of app names, got {raw!r}")
            return list(default)
        out: list[str] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, str) or not item.strip():
                problems.append(f"[{SECTION}] {key} entry ignored: {item!r}")
                continue
            if _normalize_name(item) not in seen:
                seen.add(_normalize_name(item))
                out.append(item.strip())
        return out

    def to_toml(self) -> str:
        exceptions = ", ".join(_toml_string(e) for e in self.exceptions)
        players = ", ".join(_toml_string(e) for e in self.players)
        meeting_apps = ", ".join(_toml_string(e) for e in self.meeting_apps)
        return (
            f"[{SECTION}]\n"
            f"enabled = {'true' if self.enabled else 'false'}\n"
            f"hold_for = {_toml_string(self.hold_for)}\n"
            f"players = [{players}]\n"
            f"exceptions = [{exceptions}]\n"
            f"max_wait_seconds = {self.max_wait_seconds:g}\n"
            f"poll_seconds = {self.poll_seconds:g}\n"
            f"meeting_hold = {'true' if self.meeting_hold else 'false'}\n"
            f"meeting_apps = [{meeting_apps}]\n"
            f"meeting_summary = {'true' if self.meeting_summary else 'false'}\n"
        )

    def is_exception(self, proc: "AudioProcess") -> bool:
        """True when `proc` is on the exception list (see `_matches`)."""
        return _matches(self.exceptions, proc)

    def is_player(self, proc: "AudioProcess") -> bool:
        """True when `proc` is on the players list (see `_matches`)."""
        return _matches(self.players, proc)

    def is_meeting_app(self, proc: "AudioProcess") -> bool:
        """True when `proc` is on the meeting-apps list (see `_matches`)."""
        return _matches(self.meeting_apps, proc)


def _matches(entries: Iterable[str], proc: "AudioProcess") -> bool:
    """Case-insensitive app match: an entry equals the bundle id
    ("com.apple.Music"), or is a prefix of it at a dot ("com.google.Chrome"
    covers "com.google.Chrome.helper"), or equals the bundle id's last
    component ("Music"), or the process name ("Music", "afplay")."""
    wanted = {_normalize_name(e) for e in entries if e and e.strip()}
    if not wanted:
        return False
    candidates = set()
    bid = _normalize_name(proc.bundle_id) if proc.bundle_id else ""
    if bid:
        candidates.add(bid)
        candidates.add(bid.rsplit(".", 1)[-1])
    if proc.name:
        candidates.add(_normalize_name(proc.name))
    if wanted & candidates:
        return True
    return any(bid.startswith(w + ".") for w in wanted if "." in w) if bid else False


def _toml_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def default_config_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override)
    return Path.home() / ".config" / "auto-speech" / CONFIG_FILE_NAME


class SoundYieldStore:
    """Reads and writes the settings file. Missing file = defaults."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else default_config_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> SoundYieldSettings:
        p = self._path
        if not p.is_file():
            return SoundYieldSettings()
        try:
            raw = tomllib.loads(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            print(f"[sound-yield] could not parse {p}: {exc}; using defaults", file=sys.stderr)
            return SoundYieldSettings(config_path=str(p), problems=(f"{p}: could not parse TOML ({exc})",))
        section = raw.get(SECTION) if isinstance(raw, dict) else None
        if section is None:
            return SoundYieldSettings(config_path=str(p))
        return SoundYieldSettings.from_mapping(section, config_path=str(p))

    def save(self, settings: SoundYieldSettings) -> SoundYieldSettings:
        p = self._path
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(settings.to_toml(), encoding="utf-8")
        os.replace(tmp, p)
        return replace(settings, config_path=str(p), problems=())

    def set_enabled(self, enabled: bool) -> SoundYieldSettings:
        return self.save(replace(self.load(), enabled=bool(enabled)))

    def add_exceptions(self, names: Iterable[str]) -> tuple[SoundYieldSettings, list[str]]:
        current = self.load()
        merged, added = _merge_names(current.exceptions, names)
        return self.save(replace(current, exceptions=tuple(merged))), added

    def remove_exceptions(self, names: Iterable[str]) -> tuple[SoundYieldSettings, list[str]]:
        current = self.load()
        drop = {_normalize_name(str(n)) for n in names if str(n).strip()}
        kept: list[str] = []
        removed: list[str] = []
        for e in current.exceptions:
            (removed if _normalize_name(e) in drop else kept).append(e)
        return self.save(replace(current, exceptions=tuple(kept))), removed

    def clear_exceptions(self) -> SoundYieldSettings:
        return self.save(replace(self.load(), exceptions=()))

    def set_hold_for(self, hold_for: str) -> SoundYieldSettings:
        value = str(hold_for).strip().lower()
        if value not in HOLD_FOR:
            raise ValueError(f"hold_for should be {' or '.join(HOLD_FOR)}, got {hold_for!r}")
        return self.save(replace(self.load(), hold_for=value))

    def add_players(self, names: Iterable[str]) -> tuple[SoundYieldSettings, list[str]]:
        current = self.load()
        merged, added = _merge_names(current.players, names)
        return self.save(replace(current, players=tuple(merged))), added

    def remove_players(self, names: Iterable[str]) -> tuple[SoundYieldSettings, list[str]]:
        current = self.load()
        kept, removed = _drop_names(current.players, names)
        return self.save(replace(current, players=tuple(kept))), removed

    def reset_players(self) -> SoundYieldSettings:
        return self.save(replace(self.load(), players=DEFAULT_PLAYERS))


def _merge_names(have_names: Iterable[str], names: Iterable[str]) -> tuple[list[str], list[str]]:
    """Add names not already covered: a name is covered when it equals an
    entry or an entry's last component ("Music" is covered by
    "com.apple.Music"), case-insensitively."""
    merged = list(have_names)
    added: list[str] = []

    def covered(name: str) -> bool:
        key = _normalize_name(name)
        for e in merged:
            have = _normalize_name(e)
            if key == have or key == have.rsplit(".", 1)[-1]:
                return True
        return False

    for raw in names:
        name = str(raw).strip()
        if not name or covered(name):
            continue
        merged.append(name)
        added.append(name)
    return merged, added


def _drop_names(have_names: Iterable[str], names: Iterable[str]) -> tuple[list[str], list[str]]:
    """Drop entries named exactly (case-insensitive) or by a bundle id's last
    component ("Chrome" drops "com.google.Chrome")."""
    drop = {_normalize_name(str(n)) for n in names if str(n).strip()}
    kept: list[str] = []
    removed: list[str] = []
    for e in have_names:
        key = _normalize_name(e)
        hit = key in drop or key.rsplit(".", 1)[-1] in drop
        (removed if hit else kept).append(e)
    return kept, removed


# --------------------------------------------------------------------------
# Who is playing: Core Audio probe + process table
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AudioProcess:
    pid: int
    bundle_id: str = ""
    name: str = ""
    running_output: bool = True
    running_input: bool = False  # the process holds the microphone (a meeting, or CrownTalk)

    def label(self) -> str:
        parts = [self.name or "?"]
        if self.bundle_id:
            parts.append(self.bundle_id)
        parts.append(f"pid {self.pid}")
        return " ".join(f"({p})" if i else p for i, p in enumerate(parts))


class ProbeUnavailable(RuntimeError):
    """The probe cannot answer on this system (not macOS, old macOS, or a
    Core Audio error). Callers fail open: no hold."""


def _fourcc(code: str) -> int:
    return struct.unpack(">I", code.encode("ascii"))[0]


_SEL_PROCESS_OBJECT_LIST = "prs#"  # kAudioHardwarePropertyProcessObjectList
_SEL_PROCESS_PID = "ppid"  # kAudioProcessPropertyPID
_SEL_PROCESS_BUNDLE_ID = "pbid"  # kAudioProcessPropertyBundleID
_SEL_PROCESS_IS_RUNNING_OUTPUT = "piro"  # kAudioProcessPropertyIsRunningOutput (14.2+)
_SEL_PROCESS_IS_RUNNING_INPUT = "piri"  # kAudioProcessPropertyIsRunningInput (14.2+): holds the microphone
_SEL_DEFAULT_OUTPUT_DEVICE = "dOut"  # kAudioHardwarePropertyDefaultOutputDevice
_SEL_DEVICE_IS_RUNNING_SOMEWHERE = "gone"  # kAudioDevicePropertyDeviceIsRunningSomewhere
_SCOPE_GLOBAL = "glob"
_ELEMENT_MAIN = 0
_SYSTEM_OBJECT = 1
_CF_UTF8 = 0x08000100


class _PropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


class CoreAudioProbe:
    """Reads which processes are outputting audio, via Core Audio (ctypes).

    Loads the frameworks lazily on first use; any failure to load or to
    read raises ProbeUnavailable, never anything else."""

    CORE_AUDIO = "/System/Library/Frameworks/CoreAudio.framework/CoreAudio"
    CORE_FOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"

    def __init__(self) -> None:
        self._ca = None
        self._cf = None
        self._load_error: Optional[str] = None

    # -- loading ------------------------------------------------------------

    def _load(self) -> None:
        if self._ca is not None:
            return
        if self._load_error is not None:
            raise ProbeUnavailable(self._load_error)
        if sys.platform != "darwin":
            self._load_error = f"Core Audio is macOS only (platform={sys.platform})"
            raise ProbeUnavailable(self._load_error)
        try:
            ca = ctypes.CDLL(self.CORE_AUDIO)
            cf = ctypes.CDLL(self.CORE_FOUNDATION)
            ca.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
            ca.AudioObjectGetPropertyDataSize.argtypes = [
                ctypes.c_uint32,
                ctypes.POINTER(_PropertyAddress),
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint32),
            ]
            ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
            ca.AudioObjectGetPropertyData.argtypes = [
                ctypes.c_uint32,
                ctypes.POINTER(_PropertyAddress),
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.c_void_p,
            ]
            ca.AudioObjectHasProperty.restype = ctypes.c_bool
            ca.AudioObjectHasProperty.argtypes = [ctypes.c_uint32, ctypes.POINTER(_PropertyAddress)]
            cf.CFStringGetCString.restype = ctypes.c_bool
            cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
            cf.CFRelease.restype = None
            cf.CFRelease.argtypes = [ctypes.c_void_p]
        except (OSError, AttributeError) as exc:
            self._load_error = f"Core Audio not loadable: {exc}"
            raise ProbeUnavailable(self._load_error) from exc
        self._ca, self._cf = ca, cf

    @staticmethod
    def _addr(selector: str) -> _PropertyAddress:
        return _PropertyAddress(_fourcc(selector), _fourcc(_SCOPE_GLOBAL), _ELEMENT_MAIN)

    # -- primitive reads ----------------------------------------------------

    def _get_u32(self, obj: int, selector: str) -> int:
        addr = self._addr(selector)
        value = ctypes.c_uint32(0)
        size = ctypes.c_uint32(4)
        status = self._ca.AudioObjectGetPropertyData(obj, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(value))
        if status != 0:
            raise ProbeUnavailable(f"AudioObjectGetPropertyData({selector!r}) on {obj} failed: {status}")
        return value.value

    def _get_u32_list(self, obj: int, selector: str) -> list[int]:
        addr = self._addr(selector)
        size = ctypes.c_uint32(0)
        status = self._ca.AudioObjectGetPropertyDataSize(obj, ctypes.byref(addr), 0, None, ctypes.byref(size))
        if status != 0:
            raise ProbeUnavailable(f"AudioObjectGetPropertyDataSize({selector!r}) failed: {status}")
        count = size.value // 4
        if count == 0:
            return []
        arr = (ctypes.c_uint32 * count)()
        status = self._ca.AudioObjectGetPropertyData(obj, ctypes.byref(addr), 0, None, ctypes.byref(size), arr)
        if status != 0:
            raise ProbeUnavailable(f"AudioObjectGetPropertyData({selector!r}) failed: {status}")
        return list(arr)[: size.value // 4]

    def _get_cfstring(self, obj: int, selector: str) -> str:
        addr = self._addr(selector)
        ref = ctypes.c_void_p(None)
        size = ctypes.c_uint32(ctypes.sizeof(ctypes.c_void_p))
        status = self._ca.AudioObjectGetPropertyData(obj, ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(ref))
        if status != 0 or not ref.value:
            return ""
        buf = ctypes.create_string_buffer(1024)
        try:
            ok = self._cf.CFStringGetCString(ref, buf, 1024, _CF_UTF8)
        finally:
            self._cf.CFRelease(ref)
        return buf.value.decode("utf-8", "replace") if ok else ""

    # -- public ---------------------------------------------------------------

    def playing_processes(self) -> list[AudioProcess]:
        """Every process Core Audio says is running output or input right
        now (bundle id when it has one; name is filled in by the gate).
        `running_input` is a property read on the same process object, not
        a tap: nothing is captured and no permission is asked."""
        self._load()
        try:
            objects = self._get_u32_list(_SYSTEM_OBJECT, _SEL_PROCESS_OBJECT_LIST)
        except ProbeUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 — ctypes surprises become "unavailable"
            raise ProbeUnavailable(f"process list read failed: {exc!r}") from exc
        out: list[AudioProcess] = []
        for obj in objects:
            try:
                running = self._get_u32(obj, _SEL_PROCESS_IS_RUNNING_OUTPUT)
            except ProbeUnavailable:
                # One stale object can vanish between the list read and
                # the flag read; skip it rather than give up on the list.
                continue
            try:
                recording = self._get_u32(obj, _SEL_PROCESS_IS_RUNNING_INPUT)
            except ProbeUnavailable:
                recording = 0  # an older macOS without the input flag: no meeting detection
            if not running and not recording:
                continue
            try:
                pid = self._get_u32(obj, _SEL_PROCESS_PID)
            except ProbeUnavailable:
                continue
            bundle = self._get_cfstring(obj, _SEL_PROCESS_BUNDLE_ID)
            out.append(
                AudioProcess(pid=int(pid), bundle_id=bundle, running_output=bool(running), running_input=bool(recording))
            )
        return out

    def device_running_somewhere(self) -> bool:
        """Fallback: is the default output device running for anyone?"""
        self._load()
        try:
            device = self._get_u32(_SYSTEM_OBJECT, _SEL_DEFAULT_OUTPUT_DEVICE)
            if device == 0:
                raise ProbeUnavailable("no default output device")
            return bool(self._get_u32(device, _SEL_DEVICE_IS_RUNNING_SOMEWHERE))
        except ProbeUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProbeUnavailable(f"device read failed: {exc!r}") from exc


class ProcessTable:
    """One `ps` snapshot: pid -> (ppid, command). Names and ancestry come
    from here so the gate issues a single ps per assessment."""

    def __init__(self, runner: Callable[..., "subprocess.CompletedProcess[str]"] | None = None) -> None:
        self._run = runner or subprocess.run

    def snapshot(self) -> dict[int, tuple[int, str]]:
        try:
            res = self._run(
                ["ps", "-axo", "pid=,ppid=,command="],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return {}
        table: dict[int, tuple[int, str]] = {}
        for line in (res.stdout or "").splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) < 2:
                continue
            try:
                pid, ppid = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            table[pid] = (ppid, parts[2] if len(parts) > 2 else "")
        return table

    @staticmethod
    def name_of(table: dict[int, tuple[int, str]], pid: int) -> str:
        entry = table.get(pid)
        if not entry:
            return ""
        command = entry[1].strip()
        if not command:
            return ""
        executable = command.split(" -", 1)[0].split()[0] if command else ""
        return Path(executable).name if executable else ""


# --------------------------------------------------------------------------
# His own meeting mode: a time-boxed flag file
# --------------------------------------------------------------------------


def default_meeting_path() -> Path:
    override = os.environ.get(MEETING_FILE_ENV)
    if override:
        return Path(override)
    return Path.home() / ".local" / "state" / "auto-speech" / "meeting.json"


def parse_meeting_minutes(words: Iterable[str]) -> int:
    """`45m`, `45min`, `2h`, `1h30m`, `90` (minutes), `for 45m`. Raises
    ValueError with the reason when the words are not a duration, are under
    one minute, or run past the 8 h cap."""
    text = " ".join(str(w).strip().lower() for w in words if str(w).strip())
    if text.startswith("for "):
        text = text[4:].strip()
    if not text:
        raise ValueError("say how long, like 45m or 2h")
    if text.isdigit():
        minutes = int(text)
    else:
        import re

        m = re.fullmatch(r"(?:(\d+)\s*h(?:ours?|rs?)?)?\s*(?:(\d+)\s*m(?:in(?:utes?)?)?)?", text)
        if not m or not (m.group(1) or m.group(2)):
            raise ValueError(f"not a duration: {text!r} (say 45m, 2h or 1h30m)")
        minutes = int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    if minutes < 1:
        raise ValueError("a meeting lasts at least one minute")
    if minutes > MAX_MEETING_MINUTES:
        raise ValueError(f"the longest meeting mode is {MAX_MEETING_MINUTES // 60} h (say yield meeting on again later)")
    return minutes


@dataclass(frozen=True)
class MeetingState:
    """His manual meeting mode while it is on."""

    until: float  # epoch seconds
    set_at: float
    note: str = ""

    def minutes_left(self, now: float) -> int:
        return max(0, int((self.until - now + 59) // 60))

    def describe(self, now: float) -> str:
        end = datetime.fromtimestamp(self.until).strftime("%H:%M")
        return f"on until {end} (manual, {self.minutes_left(now)} min left)"


class MeetingFlag:
    """`~/.local/state/auto-speech/meeting.json` (AUTO_SPEECH_MEETING_FILE
    overrides it): {until, set_at, note}. Past `until` the flag reads as
    off, so a forgotten `meeting on` never mutes him for good."""

    def __init__(self, path: Path | str | None = None, *, clock: Callable[[], float] = time.time) -> None:
        self._path = Path(path) if path is not None else default_meeting_path()
        self._clock = clock

    @property
    def path(self) -> Path:
        return self._path

    def now(self) -> float:
        return self._clock()

    def read(self) -> Optional[MeetingState]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            data = json.loads(raw) if raw.strip() else {}
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        try:
            until = float(data.get("until", 0.0))
            set_at = float(data.get("set_at", 0.0))
        except (TypeError, ValueError):
            return None
        if until <= self._clock():
            return None
        return MeetingState(until=until, set_at=set_at, note=str(data.get("note") or ""))

    def set(self, minutes: int, note: str = "") -> MeetingState:
        minutes = max(1, min(int(minutes), MAX_MEETING_MINUTES))
        now = self._clock()
        state = MeetingState(until=now + minutes * 60.0, set_at=now, note=note)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps({"until": state.until, "set_at": state.set_at, "note": state.note}) + "\n", encoding="utf-8")
        os.replace(tmp, self._path)
        return state

    def clear(self) -> bool:
        """True when a meeting mode was on."""
        was_on = self.read() is not None
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            return was_on
        return was_on

    def describe(self) -> str:
        state = self.read()
        return "meeting mode: " + (state.describe(self._clock()) if state is not None else "off")


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


@dataclass
class Assessment:
    """What the gate found at one instant."""

    hold: bool
    mode: str
    settings: SoundYieldSettings
    blockers: list[AudioProcess] = field(default_factory=list)  # other apps that hold speech
    excepted: list[AudioProcess] = field(default_factory=list)  # playing, but on the exception list
    others: list[AudioProcess] = field(default_factory=list)  # running output, not a player: does not hold
    own: list[AudioProcess] = field(default_factory=list)  # auto-speech's own playback
    meeting: list[AudioProcess] = field(default_factory=list)  # meeting apps holding the microphone
    mic: list[AudioProcess] = field(default_factory=list)  # holding the microphone, not a meeting app: does not hold
    manual_meeting: Optional[MeetingState] = None  # his `yield meeting on`, while it lasts
    reason: str = ""  # REASON_MEETING | REASON_SOUND | "" when nothing holds
    note: str = ""

    @property
    def in_meeting(self) -> bool:
        return self.reason == REASON_MEETING

    def meeting_names(self) -> tuple[str, ...]:
        names = [p.label() for p in self.meeting]
        if self.manual_meeting is not None:
            names.append("meeting mode (manual)")
        return tuple(names)


@dataclass(frozen=True)
class HoldOutcome:
    status: str  # CLEAR | RESUMED | EXPIRED | ABORTED | DISABLED | UNAVAILABLE
    waited_seconds: float = 0.0
    blockers: tuple[str, ...] = ()
    reason: str = ""  # REASON_SOUND | REASON_MEETING for a hold; "" otherwise

    @property
    def in_meeting(self) -> bool:
        return self.reason == REASON_MEETING

    @property
    def may_play(self) -> bool:
        return self.status in (CLEAR, RESUMED, DISABLED, UNAVAILABLE)


class SoundYieldGate:
    """Decides whether speech may start now, and waits when it may not."""

    def __init__(
        self,
        *,
        store: SoundYieldStore | None = None,
        probe: CoreAudioProbe | None = None,
        process_table: ProcessTable | None = None,
        own_pid: Callable[[], int] = os.getpid,
        mpv_pid_path: Path = MPV_PID_PATH,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] | None = None,
        meeting_flag: MeetingFlag | None = None,
    ) -> None:
        self._store = store or SoundYieldStore()
        self._probe = probe or CoreAudioProbe()
        self._table = process_table or ProcessTable()
        self._meeting_flag = meeting_flag or MeetingFlag()
        self._own_pid = own_pid
        self._mpv_pid_path = mpv_pid_path
        self._clock = clock
        self._sleep = sleeper
        self._log = log or (lambda msg: logger.info(msg))
        self._unavailable_logged = False

    @property
    def store(self) -> SoundYieldStore:
        return self._store

    def settings(self) -> SoundYieldSettings:
        return self._store.load()

    @property
    def meeting_flag(self) -> MeetingFlag:
        return self._meeting_flag

    def manual_meeting(self) -> Optional[MeetingState]:
        try:
            return self._meeting_flag.read()
        except Exception:  # noqa: BLE001 — a flag that cannot be read is off
            return None

    # -- own-process rule -----------------------------------------------------

    def _own_mpv_pid(self) -> Optional[int]:
        try:
            return int(self._mpv_pid_path.read_text().strip())
        except (OSError, ValueError):
            return None

    def _is_own(self, pid: int, table: dict[int, tuple[int, str]], own_mpv: Optional[int]) -> bool:
        mine = self._own_pid()
        if pid == mine or (own_mpv is not None and pid == own_mpv):
            return True
        cur = pid
        for _ in range(6):  # pid itself + up to five ancestors
            entry = table.get(cur)
            if entry is None:
                return False
            ppid, command = entry
            if OWN_COMMAND_MARKER in command:
                return True
            if ppid == mine:
                return True
            if ppid <= 1 or ppid == cur:
                return False
            cur = ppid
        return False

    # -- assessment -------------------------------------------------------------

    def assess(self, settings: SoundYieldSettings | None = None) -> Assessment:
        settings = settings if settings is not None else self._store.load()
        manual = self.manual_meeting()
        if not settings.enabled:
            # `yield off`: no probe, no sound hold, no mic-detected meeting; his
            # own `yield meeting on` still holds, since he set it by hand.
            result = Assessment(hold=False, mode=MODE_DISABLED, settings=settings, manual_meeting=manual)
            if manual is not None:
                result.hold, result.reason = True, REASON_MEETING
            return result

        try:
            playing = self._probe.playing_processes()
        except ProbeUnavailable as exc:
            return self._finish(self._assess_fallback(settings, str(exc)), manual)

        result = Assessment(hold=False, mode=MODE_PROCESS_LIST, settings=settings)
        if not playing:
            return self._finish(result, manual)

        table = self._table.snapshot()
        own_mpv = self._own_mpv_pid()
        for proc in playing:
            named = replace(proc, name=ProcessTable.name_of(table, proc.pid)) if not proc.name else proc
            if self._is_own(named.pid, table, own_mpv):
                result.own.append(named)
            elif named.running_input and settings.meeting_hold and settings.is_meeting_app(named):
                # Before the exceptions: Chrome on the exception list still
                # means a meeting when Meet holds the microphone.
                result.meeting.append(named)
            elif named.running_input and not named.running_output:
                result.mic.append(named)  # CrownTalk, the speech daemon: not a meeting
            elif settings.is_exception(named):
                result.excepted.append(named)
            elif settings.hold_for == HOLD_FOR_PLAYERS and not settings.is_player(named):
                result.others.append(named)
            else:
                result.blockers.append(named)
        return self._finish(result, manual)

    @staticmethod
    def _finish(result: Assessment, manual: Optional[MeetingState]) -> Assessment:
        """Settle hold and reason: a meeting (detected or manual) wins over
        sound, since a meeting sets the line aside at once; sound waits."""
        result.manual_meeting = manual
        if result.meeting or manual is not None:
            result.hold, result.reason = True, REASON_MEETING
        elif result.blockers:
            result.hold, result.reason = True, REASON_SOUND
        else:
            result.hold, result.reason = False, ""
        return result

    def _assess_fallback(self, settings: SoundYieldSettings, reason: str) -> Assessment:
        try:
            running = self._probe.device_running_somewhere()
        except ProbeUnavailable as exc:
            if not self._unavailable_logged:
                self._log(f"sound-yield: no audio probe available ({reason}; {exc}); speech will not hold")
                self._unavailable_logged = True
            return Assessment(hold=False, mode=MODE_UNAVAILABLE, settings=settings, note=str(exc))
        if not running:
            return Assessment(hold=False, mode=MODE_DEVICE_FALLBACK, settings=settings, note=reason)
        # The device is busy. If it is our own mpv, that is not a hold.
        own_mpv = self._own_mpv_pid()
        if own_mpv is not None and _pid_alive(own_mpv):
            return Assessment(
                hold=False,
                mode=MODE_DEVICE_FALLBACK,
                settings=settings,
                own=[AudioProcess(pid=own_mpv, name="mpv")],
                note=reason,
            )
        return Assessment(
            hold=True,
            mode=MODE_DEVICE_FALLBACK,
            settings=settings,
            blockers=[AudioProcess(pid=0, name="another app (device busy)")],
            note=f"{reason}; the players list and exceptions cannot apply in device fallback",
        )

    # -- waiting ------------------------------------------------------------------

    def wait_until_clear(
        self,
        should_abort: Callable[[], bool] | None = None,
        *,
        already_waited: float = 0.0,
        on_hold: Callable[[tuple[str, ...]], None] | None = None,
    ) -> HoldOutcome:
        """Block until speech may start. Returns how it ended; the caller
        decides what to do with EXPIRED / ABORTED.

        `already_waited` is how long this speech has already waited in the
        queue (behind an earlier hold, say); it counts against the longest
        wait, so a line is never held past `max_wait_seconds` of age.
        `on_hold` is called once, with the blockers' names, when a hold
        begins, so the caller can record the line as held."""
        settings = self._store.load()
        first = self.assess(settings)
        if first.in_meeting:
            # A meeting never waits: set aside now, no synthesis, no stall.
            names = first.meeting_names()
            self._log(f"sound-yield: in a meeting ({', '.join(names)}); setting this speech aside as missed")
            return HoldOutcome(EXPIRED, 0.0, names, REASON_MEETING)
        if first.mode == MODE_DISABLED:
            return HoldOutcome(DISABLED)
        if first.mode == MODE_UNAVAILABLE:
            return HoldOutcome(UNAVAILABLE)
        if not first.hold:
            return HoldOutcome(CLEAR)

        names = tuple(p.label() for p in first.blockers)
        already = max(0.0, float(already_waited))
        budget = max(0.0, settings.max_wait_seconds - already)
        used = f", {already:.0f}s of it already spent in the queue" if already > 0 else ""
        self._log(
            f"sound-yield: holding speech while {', '.join(names)} plays "
            f"(up to {budget:g}s more{used}; then set aside as missed)"
        )
        if on_hold is not None:
            on_hold(names)
        started = self._clock()
        last_names = names
        while True:
            if should_abort is not None and should_abort():
                waited = self._clock() - started
                self._log(f"sound-yield: hold aborted after {waited:.1f}s")
                return HoldOutcome(ABORTED, waited, last_names, REASON_SOUND)
            waited = self._clock() - started
            budget = max(0.0, settings.max_wait_seconds - already)
            if waited >= budget:
                self._log(
                    f"sound-yield: longest wait ({settings.max_wait_seconds:g}s) reached; "
                    "setting this speech aside as missed"
                )
                return HoldOutcome(EXPIRED, waited, last_names, REASON_SOUND)
            self._sleep(min(settings.poll_seconds, max(0.0, budget - waited)))
            # Re-read settings each poll so `yield off` / `yield add` release a hold in progress.
            settings = self._store.load()
            current = self.assess(settings)
            if current.in_meeting:
                waited = self._clock() - started
                names = current.meeting_names()
                self._log(f"sound-yield: a meeting began during the hold ({', '.join(names)}); setting this speech aside as missed")
                return HoldOutcome(EXPIRED, waited, names, REASON_MEETING)
            if current.mode == MODE_DISABLED:
                waited = self._clock() - started
                self._log(f"sound-yield: switched off during hold; playing after {waited:.1f}s")
                return HoldOutcome(DISABLED, waited, last_names, REASON_SOUND)
            if current.mode == MODE_UNAVAILABLE:
                return HoldOutcome(UNAVAILABLE, self._clock() - started, last_names, REASON_SOUND)
            if not current.hold:
                waited = self._clock() - started
                self._log(f"sound-yield: other sound stopped; playing after {waited:.1f}s")
                return HoldOutcome(RESUMED, waited, last_names, REASON_SOUND)
            last_names = tuple(p.label() for p in current.blockers)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# --------------------------------------------------------------------------
# Shared text for CLI and MCP (same words in both)
# --------------------------------------------------------------------------


def format_settings(settings: SoundYieldSettings) -> str:
    state = "ON — speech holds while another app plays sound" if settings.enabled else "OFF — speech plays over other apps' sound"
    exceptions = ", ".join(settings.exceptions) if settings.exceptions else "none"
    players = ", ".join(settings.players) if settings.players else "none"
    where = settings.config_path or "defaults (no config file yet)"
    rule = (
        "players only (an unknown app's open stream never holds)"
        if settings.hold_for == HOLD_FOR_PLAYERS
        else "any app that plays sound, except the exceptions"
    )
    meeting_apps = ", ".join(settings.meeting_apps) if settings.meeting_apps else "none"
    meeting = (
        "ON — speech is set aside as missed while a meeting app holds the microphone"
        if settings.meeting_hold
        else "OFF — an open microphone never holds; only `yield meeting on` does"
    )
    lines = [
        f"sound yield: {state}",
        f"holds for: {rule}",
        f"players (apps whose sound holds speech): {players}",
        f"exceptions (apps whose sound does not hold speech): {exceptions}",
        f"longest hold: {settings.max_wait_seconds:g} s, then set aside as missed (auto-speech missed)",
        f"meeting hold: {meeting}",
        f"meeting apps (an open microphone here is a meeting): {meeting_apps}",
        f"after a meeting: {'one spoken line says how many lines were missed' if settings.meeting_summary else 'no summary line'}",
        f"config: {where}",
    ]
    for problem in settings.problems:
        lines.append(f"warning: {problem}")
    return "\n".join(lines)


def _playing(proc: AudioProcess) -> str:
    """A process that also holds the microphone says so, so `yield check`
    shows why it is not a meeting (it is not on the meeting-apps list)."""
    return "playing, microphone open" if proc.running_input else "playing"


def format_assessment(assessment: Assessment, *, now: float | None = None) -> str:
    lines: list[str] = []
    s = assessment.settings
    manual = assessment.manual_meeting
    if manual is not None:
        lines.append(f"meeting mode: {manual.describe(time.time() if now is None else now)} — speech is set aside as missed")
    if assessment.mode == MODE_DISABLED:
        lines.append("sound yield is OFF; " + ("speech is set aside (meeting mode)" if manual is not None else "speech would play now"))
        return "\n".join(lines)
    if assessment.mode == MODE_UNAVAILABLE:
        lines.append(f"no audio probe available ({assessment.note}); " + ("speech is set aside (meeting mode)" if manual is not None else "speech would play now"))
        return "\n".join(lines)
    playing = (
        assessment.meeting + assessment.mic + assessment.blockers + assessment.excepted + assessment.others + assessment.own
    )
    if not playing and manual is None:
        lines.append("nothing else is playing sound; speech would play now")
    else:
        for p in assessment.meeting:
            lines.append(f"in a meeting: {p.label()} holds the microphone — speech is set aside as missed")
        for p in assessment.mic:
            lines.append(f"microphone in use: {p.label()} — not a meeting app, does not hold")
        for p in assessment.blockers:
            lines.append(f"{_playing(p)}: {p.label()} — holds speech")
        for p in assessment.excepted:
            lines.append(f"{_playing(p)}: {p.label()} — on the exception list, does not hold")
        for p in assessment.others:
            lines.append(f"{_playing(p)}: {p.label()} — not a meeting app nor a player, does not hold")
        for p in assessment.own:
            lines.append(f"playing: {p.label()} — auto-speech's own, does not hold")
        if assessment.in_meeting:
            lines.append("speech would be SET ASIDE at once (a meeting; auto-speech missed lists it; one summary line follows the meeting)")
        elif assessment.hold:
            lines.append(f"speech would HOLD (up to {s.max_wait_seconds:g} s, then set aside as missed)")
        else:
            lines.append("speech would play now")
    if assessment.mode == MODE_DEVICE_FALLBACK:
        lines.append(
            "detection: default output device only (process list unavailable); "
            "the players list and exceptions cannot apply"
        )
    else:
        lines.append("detection: Core Audio process list")
    return "\n".join(lines)


ACTIONS = (
    "status", "on", "off", "add", "remove", "clear", "check",
    "hold-for", "player-add", "player-remove", "player-reset",
    "meeting",
)
MEETING_WORDS = ("status", "on", "off", "for")


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    text: str


def run_meeting_action(words: Iterable[str], *, flag: MeetingFlag) -> ActionResult:
    """`yield meeting [status | on | off | for <duration>]` (the MCP tool:
    action "meeting", the words in `apps`). `on` is 60 minutes; `for 45m`,
    `for 2h`, `for 1h30m` up to 8 h; past the end the mode is off by itself."""
    words = [str(w).strip().lower() for w in words if str(w).strip()]
    verb = words[0] if words else "status"
    if verb == "status":
        if len(words) > 1:
            return ActionResult(False, "yield meeting status takes no other word")
        return ActionResult(True, flag.describe())
    if verb == "off":
        was_on = flag.clear()
        return ActionResult(True, ("meeting mode: off (was on)" if was_on else "meeting mode: off (it was not on)"))
    if verb == "on" and len(words) == 1:
        state = flag.set(DEFAULT_MEETING_MINUTES, note="yield meeting on")
        return ActionResult(True, f"meeting mode: {state.describe(flag.now())}; speech is set aside as missed until then")
    if verb in ("on", "for") or words[0][0].isdigit():
        rest = words[1:] if verb in ("on", "for") else words
        try:
            minutes = parse_meeting_minutes(rest)
        except ValueError as exc:
            return ActionResult(False, f"yield meeting: {exc}")
        state = flag.set(minutes, note=f"yield meeting for {' '.join(rest)}")
        return ActionResult(True, f"meeting mode: {state.describe(flag.now())}; speech is set aside as missed until then")
    return ActionResult(False, f"yield meeting: say {' | '.join(MEETING_WORDS)} <duration>, not {verb!r}")


def run_action(
    action: str | None,
    apps: Iterable[str] = (),
    *,
    store: SoundYieldStore | None = None,
    gate: SoundYieldGate | None = None,
    meeting_flag: MeetingFlag | None = None,
) -> ActionResult:
    """One verb for the CLI (`auto-speech yield <action> [apps…]`) and the
    MCP tool (`sound_yield(action, apps)`), so both say the same thing."""
    store = store or SoundYieldStore()
    action = (action or "status").strip().lower()
    apps = [str(a).strip() for a in apps if str(a).strip()]
    if action not in ACTIONS:
        return ActionResult(False, f"yield: unknown action {action!r} (expected {' | '.join(ACTIONS)})")
    if action == "meeting":
        flag = meeting_flag or (gate.meeting_flag if gate is not None else MeetingFlag())
        return run_meeting_action(apps, flag=flag)
    if action == "status":
        flag = meeting_flag or (gate.meeting_flag if gate is not None else MeetingFlag())
        return ActionResult(True, format_settings(store.load()) + "\n" + flag.describe())
    if action in ("on", "off"):
        settings = store.set_enabled(action == "on")
        return ActionResult(True, format_settings(settings))
    if action == "add":
        if not apps:
            return ActionResult(False, "yield add: name at least one app (bundle id like com.apple.Music, or name like Music)")
        settings, added = store.add_exceptions(apps)
        head = f"added: {', '.join(added)}" if added else "added: nothing new (already on the list)"
        return ActionResult(True, head + "\n" + format_settings(settings))
    if action == "remove":
        if not apps:
            return ActionResult(False, "yield remove: name at least one app to remove")
        settings, removed = store.remove_exceptions(apps)
        head = f"removed: {', '.join(removed)}" if removed else "removed: nothing (not on the list)"
        return ActionResult(True, head + "\n" + format_settings(settings))
    if action == "clear":
        settings = store.clear_exceptions()
        return ActionResult(True, "exceptions cleared\n" + format_settings(settings))
    if action == "hold-for":
        if len(apps) != 1 or apps[0].lower() not in HOLD_FOR:
            return ActionResult(False, f"yield hold-for: say {' or '.join(HOLD_FOR)}")
        return ActionResult(True, format_settings(store.set_hold_for(apps[0])))
    if action == "player-add":
        if not apps:
            return ActionResult(False, "yield player-add: name at least one app (bundle id like com.apple.Music, or name like Music)")
        settings, added = store.add_players(apps)
        head = f"players added: {', '.join(added)}" if added else "players added: nothing new (already on the list)"
        return ActionResult(True, head + "\n" + format_settings(settings))
    if action == "player-remove":
        if not apps:
            return ActionResult(False, "yield player-remove: name at least one app to remove")
        settings, removed = store.remove_players(apps)
        head = f"players removed: {', '.join(removed)}" if removed else "players removed: nothing (not on the list)"
        return ActionResult(True, head + "\n" + format_settings(settings))
    if action == "player-reset":
        settings = store.reset_players()
        return ActionResult(True, "players reset to the defaults\n" + format_settings(settings))
    # check
    gate = gate or SoundYieldGate(store=store, meeting_flag=meeting_flag)
    return ActionResult(True, format_assessment(gate.assess()))


if __name__ == "__main__":
    gate = SoundYieldGate()
    print(format_settings(gate.settings()))
    print(format_assessment(gate.assess()))
