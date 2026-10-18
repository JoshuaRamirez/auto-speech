"""Sound yield: hold speech while another app is playing sound.

By default auto-speech does not START speaking while some other process
is producing audio (music, a video, a call). The playback it is about to
start is held, in queue order, until the other sound stops; past a longest
wait it is dropped (or, by configuration, played anyway). See ADR-015.

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

Three collaborators, all injectable for tests:

  SoundYieldStore     the settings file (~/.config/auto-speech/sound-yield.toml)
  CoreAudioProbe      who is outputting right now
  ProcessTable        pid -> (ppid, command), for names and own-process checks

and the SoundYieldGate that combines them. The CLI (`auto-speech yield`),
the MCP tool (`sound_yield`) and the config file all speak the same
names: enabled / exceptions / max_wait_seconds / after_max_wait.
"""

from __future__ import annotations

import ctypes
import logging
import os
import struct
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable, Optional

logger = logging.getLogger(__name__)

CONFIG_FILE_NAME = "sound-yield.toml"
CONFIG_ENV = "AUTO_SPEECH_SOUND_YIELD_CONFIG"
SECTION = "sound_yield"

DEFAULT_ENABLED = True
DEFAULT_MAX_WAIT_SECONDS = 180.0
DEFAULT_POLL_SECONDS = 0.5
AFTER_MAX_WAIT_CHOICES = ("drop", "play")
DEFAULT_AFTER_MAX_WAIT = "drop"

OWN_COMMAND_MARKER = "auto-speech"
MPV_PID_PATH = Path("/tmp/auto-speech-mpv.pid")

# Hold outcomes (HoldOutcome.status).
CLEAR = "clear"  # nothing else was playing; no hold
RESUMED = "resumed"  # held, the other sound stopped, play now
EXPIRED_PLAY = "expired_play"  # held to the longest wait; policy says play anyway
EXPIRED_DROP = "expired_drop"  # held to the longest wait; policy says drop
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
    max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS
    after_max_wait: str = DEFAULT_AFTER_MAX_WAIT
    poll_seconds: float = DEFAULT_POLL_SECONDS
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

        raw_exc = section.get("exceptions", [])
        exceptions: list[str] = []
        if isinstance(raw_exc, str):
            raw_exc = [raw_exc]
        if not isinstance(raw_exc, list):
            problems.append(f"[{SECTION}] exceptions should be a list of app names, got {raw_exc!r}")
            raw_exc = []
        for item in raw_exc:
            if not isinstance(item, str) or not item.strip():
                problems.append(f"[{SECTION}] exceptions entry ignored: {item!r}")
                continue
            if _normalize_name(item) not in {_normalize_name(e) for e in exceptions}:
                exceptions.append(item.strip())

        max_wait = section.get("max_wait_seconds", DEFAULT_MAX_WAIT_SECONDS)
        if isinstance(max_wait, bool) or not isinstance(max_wait, (int, float)) or max_wait < 0:
            problems.append(f"[{SECTION}] max_wait_seconds should be a number >= 0, got {max_wait!r}")
            max_wait = DEFAULT_MAX_WAIT_SECONDS

        after = section.get("after_max_wait", DEFAULT_AFTER_MAX_WAIT)
        if not isinstance(after, str) or after.strip().lower() not in AFTER_MAX_WAIT_CHOICES:
            problems.append(
                f"[{SECTION}] after_max_wait must be one of {AFTER_MAX_WAIT_CHOICES}, got {after!r}"
            )
            after = DEFAULT_AFTER_MAX_WAIT
        after = after.strip().lower()

        poll = section.get("poll_seconds", DEFAULT_POLL_SECONDS)
        if isinstance(poll, bool) or not isinstance(poll, (int, float)) or poll <= 0:
            problems.append(f"[{SECTION}] poll_seconds should be a number > 0, got {poll!r}")
            poll = DEFAULT_POLL_SECONDS

        known = {"enabled", "exceptions", "max_wait_seconds", "after_max_wait", "poll_seconds"}
        for key in section:
            if key not in known:
                problems.append(f"[{SECTION}] unknown key {key!r} (ignored)")

        return cls(
            enabled=enabled,
            exceptions=tuple(exceptions),
            max_wait_seconds=float(max_wait),
            after_max_wait=after,
            poll_seconds=float(poll),
            config_path=config_path,
            problems=tuple(problems),
        )

    def to_toml(self) -> str:
        items = ", ".join(_toml_string(e) for e in self.exceptions)
        return (
            f"[{SECTION}]\n"
            f"enabled = {'true' if self.enabled else 'false'}\n"
            f"exceptions = [{items}]\n"
            f"max_wait_seconds = {self.max_wait_seconds:g}\n"
            f'after_max_wait = "{self.after_max_wait}"\n'
            f"poll_seconds = {self.poll_seconds:g}\n"
        )

    def is_exception(self, proc: "AudioProcess") -> bool:
        """True when `proc` is on the exception list, matched case-insensitively
        by bundle id ("com.apple.Music"), by its last component ("Music"), or
        by process name ("Music", "Spotify")."""
        if not self.exceptions:
            return False
        wanted = {_normalize_name(e) for e in self.exceptions}
        candidates = set()
        if proc.bundle_id:
            bid = _normalize_name(proc.bundle_id)
            candidates.add(bid)
            candidates.add(bid.rsplit(".", 1)[-1])
        if proc.name:
            candidates.add(_normalize_name(proc.name))
        return bool(wanted & candidates)


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
        have = {_normalize_name(e) for e in current.exceptions}
        added: list[str] = []
        merged = list(current.exceptions)
        for raw in names:
            name = str(raw).strip()
            if not name or _normalize_name(name) in have:
                continue
            merged.append(name)
            have.add(_normalize_name(name))
            added.append(name)
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


# --------------------------------------------------------------------------
# Who is playing: Core Audio probe + process table
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AudioProcess:
    pid: int
    bundle_id: str = ""
    name: str = ""
    running_output: bool = True

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
        """Every process Core Audio says is running output right now
        (bundle id when it has one; name is filled in by the gate)."""
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
            if not running:
                continue
            try:
                pid = self._get_u32(obj, _SEL_PROCESS_PID)
            except ProbeUnavailable:
                continue
            bundle = self._get_cfstring(obj, _SEL_PROCESS_BUNDLE_ID)
            out.append(AudioProcess(pid=int(pid), bundle_id=bundle, running_output=True))
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
    own: list[AudioProcess] = field(default_factory=list)  # auto-speech's own playback
    note: str = ""


@dataclass(frozen=True)
class HoldOutcome:
    status: str  # CLEAR | RESUMED | EXPIRED_PLAY | EXPIRED_DROP | ABORTED | DISABLED | UNAVAILABLE
    waited_seconds: float = 0.0
    blockers: tuple[str, ...] = ()

    @property
    def may_play(self) -> bool:
        return self.status in (CLEAR, RESUMED, EXPIRED_PLAY, DISABLED, UNAVAILABLE)


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
    ) -> None:
        self._store = store or SoundYieldStore()
        self._probe = probe or CoreAudioProbe()
        self._table = process_table or ProcessTable()
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
        if not settings.enabled:
            return Assessment(hold=False, mode=MODE_DISABLED, settings=settings)

        try:
            playing = self._probe.playing_processes()
        except ProbeUnavailable as exc:
            return self._assess_fallback(settings, str(exc))

        if not playing:
            return Assessment(hold=False, mode=MODE_PROCESS_LIST, settings=settings)

        table = self._table.snapshot()
        own_mpv = self._own_mpv_pid()
        result = Assessment(hold=False, mode=MODE_PROCESS_LIST, settings=settings)
        for proc in playing:
            named = replace(proc, name=ProcessTable.name_of(table, proc.pid)) if not proc.name else proc
            if self._is_own(named.pid, table, own_mpv):
                result.own.append(named)
            elif settings.is_exception(named):
                result.excepted.append(named)
            else:
                result.blockers.append(named)
        result.hold = bool(result.blockers)
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
            note=f"{reason}; exceptions cannot apply in device fallback",
        )

    # -- waiting ------------------------------------------------------------------

    def wait_until_clear(self, should_abort: Callable[[], bool] | None = None) -> HoldOutcome:
        """Block until speech may start. Returns how it ended; the caller
        decides what to do with EXPIRED_DROP / ABORTED."""
        settings = self._store.load()
        first = self.assess(settings)
        if first.mode == MODE_DISABLED:
            return HoldOutcome(DISABLED)
        if first.mode == MODE_UNAVAILABLE:
            return HoldOutcome(UNAVAILABLE)
        if not first.hold:
            return HoldOutcome(CLEAR)

        names = tuple(p.label() for p in first.blockers)
        self._log(
            f"sound-yield: holding speech while {', '.join(names)} plays "
            f"(max {settings.max_wait_seconds:g}s, then {settings.after_max_wait})"
        )
        started = self._clock()
        last_names = names
        while True:
            if should_abort is not None and should_abort():
                waited = self._clock() - started
                self._log(f"sound-yield: hold aborted after {waited:.1f}s")
                return HoldOutcome(ABORTED, waited, last_names)
            waited = self._clock() - started
            if waited >= settings.max_wait_seconds:
                if settings.after_max_wait == "play":
                    self._log(f"sound-yield: longest wait ({settings.max_wait_seconds:g}s) reached; playing anyway")
                    return HoldOutcome(EXPIRED_PLAY, waited, last_names)
                self._log(f"sound-yield: longest wait ({settings.max_wait_seconds:g}s) reached; dropping this speech")
                return HoldOutcome(EXPIRED_DROP, waited, last_names)
            self._sleep(min(settings.poll_seconds, max(0.0, settings.max_wait_seconds - waited)))
            # Re-read settings each poll so `yield off` / `yield add` release a hold in progress.
            settings = self._store.load()
            current = self.assess(settings)
            if current.mode == MODE_DISABLED:
                waited = self._clock() - started
                self._log(f"sound-yield: switched off during hold; playing after {waited:.1f}s")
                return HoldOutcome(DISABLED, waited, last_names)
            if current.mode == MODE_UNAVAILABLE:
                return HoldOutcome(UNAVAILABLE, self._clock() - started, last_names)
            if not current.hold:
                waited = self._clock() - started
                self._log(f"sound-yield: other sound stopped; playing after {waited:.1f}s")
                return HoldOutcome(RESUMED, waited, last_names)
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
    after = "play anyway" if settings.after_max_wait == "play" else "drop it"
    where = settings.config_path or "defaults (no config file yet)"
    lines = [
        f"sound yield: {state}",
        f"exceptions (apps whose sound does not hold speech): {exceptions}",
        f"longest hold: {settings.max_wait_seconds:g} s, then {after}",
        f"config: {where}",
    ]
    for problem in settings.problems:
        lines.append(f"warning: {problem}")
    return "\n".join(lines)


def format_assessment(assessment: Assessment) -> str:
    lines: list[str] = []
    s = assessment.settings
    if assessment.mode == MODE_DISABLED:
        lines.append("sound yield is OFF; speech would play now")
        return "\n".join(lines)
    if assessment.mode == MODE_UNAVAILABLE:
        lines.append(f"no audio probe available ({assessment.note}); speech would play now")
        return "\n".join(lines)
    playing = assessment.blockers + assessment.excepted + assessment.own
    if not playing:
        lines.append("nothing else is playing sound; speech would play now")
    else:
        for p in assessment.blockers:
            lines.append(f"playing: {p.label()} — holds speech")
        for p in assessment.excepted:
            lines.append(f"playing: {p.label()} — on the exception list, does not hold")
        for p in assessment.own:
            lines.append(f"playing: {p.label()} — auto-speech's own, does not hold")
        if assessment.hold:
            after = "play anyway" if s.after_max_wait == "play" else "drop it"
            lines.append(f"speech would HOLD (up to {s.max_wait_seconds:g} s, then {after})")
        else:
            lines.append("speech would play now")
    if assessment.mode == MODE_DEVICE_FALLBACK:
        lines.append("detection: default output device only (process list unavailable); exceptions cannot apply")
    else:
        lines.append("detection: Core Audio process list")
    return "\n".join(lines)


ACTIONS = ("status", "on", "off", "add", "remove", "clear", "check")


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    text: str


def run_action(
    action: str | None,
    apps: Iterable[str] = (),
    *,
    store: SoundYieldStore | None = None,
    gate: SoundYieldGate | None = None,
) -> ActionResult:
    """One verb for the CLI (`auto-speech yield <action> [apps…]`) and the
    MCP tool (`sound_yield(action, apps)`), so both say the same thing."""
    store = store or SoundYieldStore()
    action = (action or "status").strip().lower()
    apps = [str(a).strip() for a in apps if str(a).strip()]
    if action not in ACTIONS:
        return ActionResult(False, f"yield: unknown action {action!r} (expected {' | '.join(ACTIONS)})")
    if action == "status":
        return ActionResult(True, format_settings(store.load()))
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
    # check
    gate = gate or SoundYieldGate(store=store)
    return ActionResult(True, format_assessment(gate.assess()))


if __name__ == "__main__":
    gate = SoundYieldGate()
    print(format_settings(gate.settings()))
    print(format_assessment(gate.assess()))
