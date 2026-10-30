"""Narrator service — long-running daemon.

Tails /tmp/auto-speech-narrator-events.jsonl, feeds events to a
PhaseClassifier, summarizes closed phases with the configured LLM,
and plays them in strict FIFO order through the existing TTS pipeline.

Singleton: only one instance runs per host. PID file at
/tmp/auto-speech-narrator-daemon.pid. Auto-shuts-down after
idle_shutdown_seconds with no new events.

The current FIFO depth is mirrored to /tmp/auto-speech-narration-depth
so the autoplay worker can wait for it to reach zero before reading
the end-of-turn response.
"""

from __future__ import annotations

import atexit
import inspect
import json
import logging
import os
import re
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

from line_ledger import DROP_FAILED, DROP_INTERRUPTED, DROP_STOPPED, DONE, LineLedger, clean_line_id, default_lines_path
from session_liveness import SessionLiveness
from state_paths import state_path
from collections.abc import Callable
from typing import Any, Optional

# Daemon runs a UNIX domain socket server (socket.AF_UNIX) via unix_ipc_server._DaemonSocketServer

from auto_speech_log import get_logger
from cache_entry import CacheEntry
from cache_store import CachePromotionError, CacheStore
from config_constants import DEFAULT_SPEED, DEFAULT_VOICE_ID, FALLBACK_CHARS_PER_SEC
from missed_speech import (
    REASON_INTERRUPTED,
    REASON_LONGEST_WAIT,
    REASON_MEETING,
    REASON_ADDED,
    REASON_RESTART,
    REASON_STALE,
    REASON_STOPPED,
    REASON_TIMEOUT,
    MissedLine,
    MissedList,
    new_id,
)
from native_audio_sink import NativeAudioSink, PlaybackError, PlaybackYielded
from narrator_config import load_config
from narrator_phase_classifier import Category, Phase, PhaseClassifier
from narrator_state import (
    IDLE_SHUTDOWN,
    NOT_RUNNING,
    RUNNING,
    SIGNAL_SHUTDOWN,
    STARTING,
    NarratorStateMachine,
)
from narrator_summarizer import Summarizer, load_summarizer
from sound_yield import ABORTED, DEFAULT_MAX_WAIT_SECONDS, REASON_MEETING as HOLD_REASON_MEETING, SoundYieldGate
from priority_arbiter import Priority, PriorityArbiter, QueueItem, QueueProxyFacade
from speaker import Announcer, Plan
from speech_history import SpeechHistory, clean_origin_file, estimate_word_timings
from speech_control import MISSED_PREFIX, ControlActions, ControlDriver, rfc3339
from speech_player import PlayerLauncher
from unix_ipc_server import _DaemonSocketServer, control_payload
from speech_priority import (
    DEFAULT_LOAD_SCALE_CAP,
    DEFAULT_RETRY_LOAD_PER_CORE,
    DEFAULT_RETRY_WAIT_SECONDS,
    LoadWatch,
    raise_own_priority,
    set_thread_qos_user_interactive,
)
from tts_executor import SynthesisTimeout, TTSExecutor
from resilient_synthesizer import ResilientSynthesizer
from tts_engine import TTSEngine
from voice_profile import VoiceProfile
from voice_profile_store import VoiceProfileStore

EVENTS_LOG = state_path("auto-speech-narrator-events.jsonl")
PID_FILE = state_path("auto-speech-narrator-daemon.pid")
LOG_FILE = state_path("auto-speech-narrator-daemon.log")
DEPTH_FILE = state_path("auto-speech-narration-depth")
# The worker's last step, for the doctor: {"at", "event", "depth", "expected_done_at"}.
# A queue that is non-empty while this stands still is a stalled narrator.
PROGRESS_FILE = state_path("auto-speech-narration-progress")
SYNTHESIS_TIMEOUT_FLOOR_SECONDS = 30.0
DEFAULT_TIMEOUT_RETRIES = 1          # ADR-019: a timed-out line is queued again this many times (one submission, at most one retry: #m1023-1717-fd7b)
RETRY_DIR = state_path("auto-speech-retry")   # the WAV of a cut-off line, kept for its replay
# Each line's synthesis WAV and its fragments: a directory of the daemon's
# own, so the clean-up glob after a line reads a few names, not the system
# temp dir (56,785 entries on 2026-10-20, seconds per line under load).
SYNTH_DIR = state_path("auto-speech-synth")
WATERMARK_FILE = state_path("auto-speech-narrator-daemon.watermark")
# His stop holds across a restart: the file says since when (ADR-024).
STOPPED_FILE = state_path("auto-speech-stopped.json")
# One line id, one play: every id seen in the last hour and its state (ADR-025).
# One line id, one play (ADR-025/026): kept beside the missed list so it outlives a reboot;
# the old /tmp file is read once when the new one is not there yet.
LINES_FILE = default_lines_path()
LEGACY_LINES_FILE = state_path("auto-speech-lines.json")
# On SIGTERM the line in flight finishes; past this it is cut and set aside.
# launchd's default exit timeout for a submitted job is 20 s.
SHUTDOWN_FINISH_S = 15.0
DEFAULT_SOCKET_PATH = state_path("auto-speech-daemon.sock")
SOCKET_FILE = Path(os.environ.get("AUTO_SPEECH_DAEMON_SOCK", str(DEFAULT_SOCKET_PATH)))

POLL_INTERVAL_S = 0.25
MEETING_POLL_S = 2.0  # how often the main loop asks the gate whether he is in a meeting

# Categories we never narrate. Single MCP calls and stray uncategorised
# tools land in OTHER and produce noise.
SUPPRESSED_CATEGORIES = {Category.OTHER}


# The daemon is long-lived, so its structured log needs MID-RUN rotation:
# a RotatingFileHandler that exclusively owns LOG_FILE. The start script
# sends the daemon's raw stdio to a separate .out file (rotated pre-spawn)
# precisely so it does NOT also write here and defeat this rotation.
_LOGGER = get_logger("auto-speech.narrator", LOG_FILE)


def _log(msg: str) -> None:
    _LOGGER.info(msg)


def _wav_duration_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
        return frames / rate if rate else 0.0
    except Exception:
        return 0.0


def _pid_cmdline(pid: int) -> str:
    """Best-effort command line of `pid` via `ps`; '' on any error."""
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def _pid_is_our_daemon(pid: int, cmdline_reader=_pid_cmdline) -> bool:
    """True iff `pid` is alive AND is THIS daemon.

    Guards against PID reuse: after a crash the OS may recycle the daemon's
    pid for an unrelated process, which still passes `kill -0`. Trusting the
    number alone would falsely block a restart (here) or kill the wrong
    process (the stop script), so we additionally match the daemon's command
    line. The leading slash in the signature avoids matching the test runner
    (`.../test_narrator_service.py`)."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return "/narrator_service.py" in cmdline_reader(pid)


def _pid_is_mpv(pid: int, cmdline_reader=_pid_cmdline) -> bool:
    """True iff `pid` is alive AND is an mpv process. Guards against PID reuse."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return "mpv" in cmdline_reader(pid)


def _existing_pid() -> int | None:
    """The pid of a LIVE instance of this daemon, or None when the pid file
    is absent, unreadable, points at a dead process, or — under PID reuse —
    points at a process that is not our daemon (and is thus reclaimable)."""
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _pid_is_our_daemon(pid) else None


def _release_pid_file(pid_file: Path, pid: int) -> None:
    """Remove the pid file only when it names `pid`: a second daemon that
    stops must not take the live one's pid file with it (#m1023-1717-fd7b:
    the doctor read "not running" for a live narrator)."""
    try:
        if int(pid_file.read_text().strip()) != pid:
            return
    except (OSError, ValueError):
        return
    try:
        pid_file.unlink()
    except OSError:
        pass


def _stuck_threads() -> list[threading.Thread]:
    """The live non-daemon threads besides this one: what would keep the
    interpreter from exiting (a worker abandoned inside a hung synthesis)."""
    me = threading.current_thread()
    return [t for t in threading.enumerate() if t is not me and not t.daemon and t.is_alive()
            and t is not threading.main_thread()]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_voice_profile_path() -> Path:
    return _project_root() / "config" / "voice_calibration.json"


def _load_profile_or_fallback(config: dict | None = None) -> VoiceProfile:
    config = config or {}
    custom_path = config.get("voice_profile_path")
    path = Path(custom_path) if custom_path else _default_voice_profile_path()
    store = VoiceProfileStore(path)
    loaded = store.load()
    if loaded is not None:
        voice_id = config.get("voice_id", loaded.voice_id)
        speed = float(config.get("speed", loaded.speed))
        if voice_id != loaded.voice_id or speed != loaded.speed:
            return VoiceProfile(
                voice_id=voice_id,
                speed=speed,
                chars_per_second=loaded.chars_per_second,
                calibrated_at=loaded.calibrated_at,
                calibration_source_chars=loaded.calibration_source_chars,
            )
        return loaded

    voice_id = config.get("voice_id", DEFAULT_VOICE_ID)
    speed = float(config.get("speed", DEFAULT_SPEED))
    _log(f"no voice profile found at {store.path}; using fallback voice={voice_id} speed={speed}")
    return VoiceProfile(
        voice_id=voice_id,
        speed=speed,
        chars_per_second=FALLBACK_CHARS_PER_SEC,
        calibrated_at="fallback",
        calibration_source_chars=0,
    )


def _replay_flags(payload: dict) -> tuple[str, bool]:
    """`missed_id` (the missed-list line this request replays) and `hold`
    (False = play over other apps' sound) from a socket request. Absent
    means an ordinary request: no id, hold as usual."""
    raw = payload.get("missed_id")
    missed_id = raw.strip() if isinstance(raw, str) else ""
    hold = payload.get("hold", True)
    return missed_id, (hold if isinstance(hold, bool) else True)


class _InProcessClient:
    """missed_speech's client, answered by this daemon in process: the
    control's play-item on a missed line queues it as `missed --play` does."""

    def __init__(self, service: NarratorService) -> None:
        self._service = service

    def speak(self, text: str, *, priority: int, source_hash: str | None = None, voice_id: str | None = None,
              speed: float = DEFAULT_SPEED, missed_id: str = "", hold: bool = True, line_id: str = "",
              session_id: str | None = None, origin_file: str | None = None) -> dict[str, Any]:
        return self._service.dispatch_json({
            "action": "speak", "text": text, "priority": int(priority), "source_hash": source_hash,
            "voice_id": voice_id, "speed": speed, "missed_id": missed_id, "hold": hold,
            "line_id": line_id, "session_id": session_id, "origin_file": origin_file,
        })

    def play_cache(self, source_hash: str, *, priority: int, missed_id: str = "", hold: bool = True) -> dict[str, Any]:
        return self._service.dispatch_json({
            "action": "play_cache", "source_hash": source_hash, "priority": int(priority),
            "missed_id": missed_id, "hold": hold,
        })


class NarratorService:
    @property
    def _cache(self) -> CacheStore:
        if "_cache_instance" not in self.__dict__:
            cache_root = _project_root() / "config" / "cache"
            self.__dict__["_cache_instance"] = CacheStore(cache_root)
        return self.__dict__["_cache_instance"]

    @_cache.setter
    def _cache(self, val: CacheStore) -> None:
        self.__dict__["_cache_instance"] = val

    @property
    def _tts_queue(self) -> Any:
        if "_custom_queue" in self.__dict__:
            return self.__dict__["_custom_queue"]
        if "_queue_proxy" in self.__dict__:
            return self.__dict__["_queue_proxy"]
        max_q = getattr(self, "_max_queue", 32)
        arbiter = getattr(self, "_priority_arbiter", None)
        if arbiter is None:
            arbiter = PriorityArbiter(maxsize=max_q)
            arbiter.register_change_callback(self._queue_changed)
            self._priority_arbiter = arbiter
        proxy = QueueProxyFacade(arbiter, maxsize=max_q)
        self.__dict__["_queue_proxy"] = proxy
        return proxy

    @_tts_queue.setter
    def _tts_queue(self, val: Any) -> None:
        if isinstance(val, queue.Queue):
            self.__dict__["_custom_queue"] = val
            self.__dict__.pop("_queue_proxy", None)
        else:
            self.__dict__["_queue_proxy"] = val
            self.__dict__.pop("_custom_queue", None)

    def __init__(
        self,
        *,
        sink: NativeAudioSink | None = None,
        tts_executor: TTSExecutor | None = None,
        profile: VoiceProfile | None = None,
        socket_path: Path | str | None = None,
        synth: ResilientSynthesizer | Any = None,
        engine: TTSEngine | Any = None,
        config: dict | None = None,
        sound_yield_gate: SoundYieldGate | Any | None = None,
        missed: MissedList | None = None,
        speaker: Announcer | None = None,
        history: SpeechHistory | None = None,
        player: PlayerLauncher | Any | None = None,
        control: ControlDriver | Any | None = None,
    ) -> None:
        self._config = config if config is not None else load_config()
        self._classifier = PhaseClassifier(silence_seconds=0.5, max_events_per_phase=1)
        # Bounded so a burst of phases can't grow the queue without limit
        # when the TTS worker (which blocks on playback) lags. See
        # _enqueue_phase for the drop-oldest backpressure policy.
        self._max_queue = int(self._config.get("max_queue_depth", 32))
        self._priority_arbiter = PriorityArbiter(maxsize=self._max_queue)
        self._priority_arbiter.register_change_callback(self._queue_changed)
        self.__dict__["_queue_proxy"] = QueueProxyFacade(self._priority_arbiter, maxsize=self._max_queue)
        self._dropped_phases = 0
        self._state_lock = threading.Lock()
        self._engine_state = "IDLE"
        self._active_priority: Optional[int] = None
        self._active_item: Optional[QueueItem] = None
        self._interrupted: bool = False
        self._preempted: bool = False
        self._start_time = time.time()
        self._summarizer: Summarizer | None = None
        self._summarizer_lock = threading.Lock()
        self._last_event_ts = time.time()
        self._idle_shutdown = float(self._config["idle_shutdown_seconds"])
        self._stop = threading.Event()
        # Records and guards the daemon lifecycle. Reflects reality; does
        # not replace the tail/queue/watermark logic.
        self._fsm = NarratorStateMachine()
        self._phases_this_turn = 0

        # The default sink holds speech while another app plays sound; its
        # gate logs to the daemon log so a hold is visible there. The daemon
        # keeps the gate too, for the longest-wait setting the stale-backlog
        # rule reads, and a missed list for every line it held.
        if sound_yield_gate is None and sink is None:
            sound_yield_gate = SoundYieldGate(log=_log)
        self._yield_gate = sound_yield_gate
        self._sink: NativeAudioSink = (
            sink if sink is not None else NativeAudioSink(sound_yield=sound_yield_gate)
        )
        self._missed: MissedList = missed if missed is not None else MissedList()
        # Names the desk only when the speaker changes (ADR-016).
        self._speaker: Announcer = speaker if speaker is not None else Announcer(log=_log)
        # Every line heard goes into the speech history as a WAV and a
        # sidecar, up to max_gb on disk (ADR-020). With `[history] player`
        # on, the UI Utility's speech control shows what plays, with the
        # history and the missed lines below it, never opened while a hold
        # stands (ADR-022). The per-line player panel (ADR-021) opens only
        # when one is passed in: his 2026-10-21 20:07, "not the actual page".
        self._history: SpeechHistory = history if history is not None else SpeechHistory()
        self._player = player
        self._control = (
            control
            if control is not None
            else ControlDriver(
                history=self._history,
                missed=self._missed,
                actions=self._control_actions(),
                holding=self._hold_stands,
                log=_log,
                names=self._sender_name,
            )
        )
        self._last_hold_ended_at: Optional[float] = None
        self._sink_takes_hold_kwargs: Optional[bool] = None
        # The meeting watch (ADR-018): the main loop asks the gate every
        # MEETING_POLL_S whether he is in a meeting, and when one ends with
        # lines set aside, queues one summary line. Which lines a summary has
        # counted is marked on the missed list itself, so a restart never
        # says the same summary again (#m1021-1615-4497).
        self._meeting_active: Optional[bool] = None
        self._meeting_checked_at: float = 0.0
        # His stop (ADR-024): nothing plays until a play or resume; it is
        # kept in STOPPED_FILE so a restart keeps it.
        self._stopped_since: Optional[float] = _read_stopped()
        self._shutdown_cut = False
        # Only sessions meant to speak are heard: a marker whose session is
        # not alive is ignored (ADR-024).
        self._liveness = SessionLiveness()
        # One line id, one play (ADR-025): a speak whose id is queued,
        # playing or done in the last ten minutes is not queued again.
        self._lines = LineLedger(LINES_FILE, legacy=LEGACY_LINES_FILE if LEGACY_LINES_FILE != LINES_FILE else None)
        if tts_executor is not None:
            self._tts_executor = tts_executor
        elif synth is not None or engine is not None:
            self._tts_executor = TTSExecutor(engine=engine, synth=synth)
        else:
            self._tts_executor = TTSExecutor()
        self._profile: VoiceProfile | None = profile

        if socket_path is not None:
            self._socket_path = Path(socket_path)
        else:
            self._socket_path = Path(
                os.environ.get("AUTO_SPEECH_DAEMON_SOCK", str(DEFAULT_SOCKET_PATH))
            )
        self._socket_server: _DaemonSocketServer | None = None
        self._socket_thread: threading.Thread | None = None
        self._queue_lock = threading.Lock()
        self._atexit_registered = False

    def run(self) -> int:
        self._fsm.transition(STARTING)  # NOT_RUNNING → STARTING
        PID_FILE.write_text(str(os.getpid()))
        self._fsm.transition(RUNNING)  # STARTING → RUNNING (pid file written)
        missed = getattr(self, "_missed", None)
        if missed is not None:
            try:
                settled = missed.settle_held(REASON_RESTART)
                if settled:
                    _log(f"missed: {settled} line(s) were still held when the last daemon stopped; now on the missed list")
                expired = missed.expire()
                if expired:
                    _log(f"missed: {expired} line(s) older than a day left the missed list")
            except Exception as exc:  # noqa: BLE001
                _log(f"missed list settle error: {exc!r}")
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)
        _log(f"started pid={os.getpid()} provider={self._config['provider']}")
        try:
            from native_audio_sink import reap_orphaned_player

            reaped = reap_orphaned_player()
            if reaped is not None:
                _log(f"player: stopped mpv pid={reaped}, left playing by a narrator that was killed outright")
        except Exception as exc:  # noqa: BLE001
            _log(f"player: orphan check error: {exc!r}")
        if getattr(self, "_stopped_since", None) is not None:
            _log("stopped: still stopped from before the restart; nothing plays until a play or resume")
        self._control_call("start")
        try:
            _log(f"priority: {raise_own_priority().describe()}")
        except Exception as exc:  # noqa: BLE001 — never let the boost stop the daemon
            _log(f"priority: not raised ({exc!r})")
        self._update_depth(0)
        _sweep_stale_session_markers()

        # Eagerly load the summarizer at boot so the first phase doesn't
        # eat a 3-6 s MLX-load latency at speak time. Failures here
        # silently fall back to Mock via load_summarizer's downgrade
        # path (the user gets robotic narration but no daemon crash).

        tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
        tts_thread.start()

        try:
            self._start_socket_server()
            self._tail_events()
        finally:
            self._stop_socket_server()
            self._close_player()
            self._control_call("close")
            # If neither shutdown path recorded a reason (e.g. tail_events
            # raised), treat it as a signal-style shutdown so the machine
            # reaches a legal pre-rest state before NOT_RUNNING.
            if self._fsm.can(SIGNAL_SHUTDOWN):
                self._fsm.transition(SIGNAL_SHUTDOWN)
            # Stop the worker. The queue is bounded, so never block the
            # shutdown path on a full queue — make room, then enqueue the
            # sentinel.
            try:
                self._tts_queue.put_nowait(None)
            except queue.Full:
                try:
                    self._tts_queue.get_nowait()
                    self._tts_queue.task_done()
                except queue.Empty:
                    pass
                try:
                    self._tts_queue.put_nowait(None)
                except queue.Full:
                    pass
            # The line in flight finishes (SIGTERM, ADR-024); the worker sets
            # everything still queued aside as it goes. Past the budget the
            # line is cut, and set aside too.
            tts_thread.join(timeout=SHUTDOWN_FINISH_S)
            if tts_thread.is_alive():
                _log(f"shutdown: the line in flight did not end in {SHUTDOWN_FINISH_S:g}s; cut and set aside")
                self._shutdown_cut = True
                if getattr(self, "_sink", None) is not None:
                    try:
                        self._sink.interrupt()
                    except Exception as exc:  # noqa: BLE001
                        _log(f"sink interrupt error: {exc!r}")
                tts_thread.join(timeout=3.0)
            self._set_aside_queued(REASON_RESTART)
            _release_pid_file(PID_FILE, os.getpid())
            self._fsm.transition(NOT_RUNNING)  # *_SHUTDOWN → NOT_RUNNING
            _log("shutdown")
        return 0

    def _on_signal(self, signum, frame):
        """The first signal lets the line in flight finish and sets the rest
        of the queue aside (ADR-024); a second one cuts the line now."""
        if self._stop.is_set():
            _log(f"signal {signum} again → cutting the line in flight")
            self._shutdown_cut = True
            if hasattr(self, "_sink") and self._sink is not None:
                self._sink.interrupt()
            return
        _log(f"signal {signum} → shutting down: the line in flight finishes, the queue goes on the missed list")
        if self._fsm.can(SIGNAL_SHUTDOWN):
            self._fsm.transition(SIGNAL_SHUTDOWN)  # RUNNING → SIGNAL_SHUTDOWN
        self._stop.set()

    def _start_socket_server(self) -> None:
        """Start the background UNIX socket listener thread."""
        if self._socket_server is not None:
            return

        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self._socket_path.exists() or self._socket_path.is_symlink():
                _log(f"unlinking stale socket file: {self._socket_path}")
                self._socket_path.unlink(missing_ok=True)
        except OSError as exc:
            _log(f"warning unlinking stale socket file: {exc}")

        try:
            self._socket_server = _DaemonSocketServer(
                self._socket_path,
                on_text=self.enqueue_text,
                dispatcher=self,
            )
            self._socket_thread = threading.Thread(
                target=self._socket_server.serve_forever,
                name="daemon-socket-server",
                daemon=True,
            )
            self._socket_thread.start()
            if not self._atexit_registered:
                atexit.register(self._cleanup_socket_file)
                self._atexit_registered = True
            _log(f"socket server listening at {self._socket_path}")
        except Exception as exc:
            _log(f"failed to start socket server at {self._socket_path}: {exc}")
            raise

    def _cleanup_socket_file(self) -> None:
        """Safe unlinking of socket file (can be called via atexit)."""
        try:
            if self._socket_path.exists() or self._socket_path.is_symlink():
                self._socket_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _stop_socket_server(self) -> None:
        """Stop socket server, close listening socket, and remove file."""
        server = self._socket_server
        self._socket_server = None
        if server is not None:
            try:
                server.shutdown()
            except Exception as exc:
                _log(f"error shutting down socket server: {exc}")
            try:
                server.server_close()
            except Exception as exc:
                _log(f"error closing socket server: {exc}")

        if self._socket_thread is not None:
            if self._socket_thread.is_alive():
                self._socket_thread.join(timeout=2.0)
            self._socket_thread = None

        self._cleanup_socket_file()
        if self._atexit_registered:
            try:
                atexit.unregister(self._cleanup_socket_file)
            except Exception:
                pass
            self._atexit_registered = False

    def enqueue_text(self, text: str) -> None:
        """Enqueue a text request from the UNIX domain socket into _tts_queue."""
        text = text.strip()
        if not text:
            return

        self._last_event_ts = time.time()
        self._enqueue_phase(text)
        self._update_depth(self._tts_queue.qsize())
        _log(f"enqueued socket text: {text[:40]!r} depth={self._tts_queue.qsize()}")

    def dispatch_legacy_text(self, text: str) -> None:
        """Dispatches legacy raw UTF-8 socket text (Priority 2)."""
        self.enqueue_text(text)

    def dispatch_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Dispatches structured JSON socket requests (RFC §4.1).

        The control is read from "action", or from "command" when that is
        what names a known control: a caller that writes
        {"command":"status"} gets the status back, the same as "action".
        """
        control = control_payload(payload)
        action = control["action"] if control is not None else payload.get("action")
        if action == "speak":
            text = str(payload.get("text", "")).strip()
            if not text:
                return {"status": "ok", "message": "empty text"}

            priority = int(payload.get("priority", Priority.EXPLICIT_MCP))
            source_hash = payload.get("source_hash")
            if source_hash:
                source_hash = str(source_hash).strip().lower()
            session_id = payload.get("session_id")
            voice_id = str(payload.get("voice_id") or DEFAULT_VOICE_ID)
            speed = float(payload.get("speed", DEFAULT_SPEED))
            missed_id, hold = _replay_flags(payload)
            origin_file = clean_origin_file(payload.get("origin_file"))
            line_id = clean_line_id(payload.get("line_id")) or self._line_ledger().for_missed(missed_id)
            if line_id and priority != Priority.USER_INTERRUPT:
                seen = self._line_ledger().admit(line_id, text)
                if seen is not None:
                    _log(f"speak: line {line_id} already {seen.state} (send {seen.sends}); not queued again")
                    if seen.state == DONE:
                        # Heard already: a copy still on the missed list goes (ADR-026).
                        self._forget_missed(line_id, missed_id)
                    return {
                        "status": "ok",
                        "action": "speak",
                        "duplicate": True,
                        "line_id": line_id,
                        "line": seen.public(),
                        "queue_depth": self._tts_queue.qsize(),
                    }

            cached_wav: Optional[Path] = None
            if source_hash and len(source_hash) == 64 and all(c in "0123456789abcdef" for c in source_hash):
                try:
                    hit = self._cache.lookup(source_hash)
                    if hit is not None:
                        cached_wav = hit[0]
                except Exception:
                    cached_wav = None

            if cached_wav is not None:
                q_item = QueueItem(
                    priority=priority,
                    payload=str(cached_wav),
                    source_hash=source_hash,
                    session_id=session_id,
                    voice_id=voice_id,
                    speed=speed,
                    missed_id=missed_id,
                    hold=hold,
                    origin_file=origin_file,
                    line_id=line_id,
                    line_text=text,
                )
                cache_hit = True
            else:
                q_item = QueueItem(
                    priority=priority,
                    payload=text,
                    source_hash=source_hash,
                    session_id=session_id,
                    voice_id=voice_id,
                    speed=speed,
                    missed_id=missed_id,
                    hold=hold,
                    origin_file=origin_file,
                    line_id=line_id,
                )
                cache_hit = False

            if priority == Priority.USER_INTERRUPT:
                self._handle_p1_interrupt()
                return {"status": "ok", "action": "interrupt"}
            elif priority == Priority.EXPLICIT_MCP:
                self._handle_p2_preemption(q_item)
            else:
                if hasattr(self, "_priority_arbiter"):
                    self._tts_queue.put(q_item)
                else:
                    self._tts_queue.put(q_item.payload)

            self._update_depth(self._tts_queue.qsize())
            self._last_event_ts = time.time()
            reply = {
                "status": "queued",
                "action": "speak",
                "cache_hit": cache_hit,
                "queue_depth": self._tts_queue.qsize(),
            }
            if line_id:
                reply["line_id"] = line_id
            return reply

        elif action == "play_cache":
            source_hash = payload.get("source_hash")
            if (
                not isinstance(source_hash, str)
                or len(source_hash) != 64
                or not all(c in "0123456789abcdef" for c in source_hash)
            ):
                return {
                    "status": "error",
                    "error_code": "INVALID_PAYLOAD",
                    "message": "Invalid or missing source_hash (expected 64-character lowercase hex string)",
                }

            try:
                hit = self._cache.lookup(source_hash)
            except Exception:
                hit = None

            if hit is not None:
                wav_path, _entry = hit
            else:
                entry_dir = self._cache.path_for(source_hash)
                fallback_wav = entry_dir / "full.wav"
                if fallback_wav.is_file() and fallback_wav.stat().st_size > 0:
                    wav_path = fallback_wav
                else:
                    return {
                        "status": "error",
                        "error_code": "CACHE_MISS",
                        "message": f"Cache miss for {source_hash}",
                    }
            priority = int(payload.get("priority", Priority.EXPLICIT_MCP))
            session_id = payload.get("session_id")
            missed_id, hold = _replay_flags(payload)
            q_item = QueueItem(
                priority=priority,
                payload=str(wav_path),
                source_hash=source_hash,
                session_id=session_id,
                missed_id=missed_id,
                hold=hold,
            )

            if priority == Priority.EXPLICIT_MCP:
                self._handle_p2_preemption(q_item)
            else:
                if hasattr(self, "_priority_arbiter"):
                    self._tts_queue.put(q_item)
                else:
                    self._tts_queue.put(str(wav_path))

            self._update_depth(self._tts_queue.qsize())
            self._last_event_ts = time.time()
            return {
                "status": "queued",
                "action": "play_cache",
                "cache_hit": True,
                "queue_depth": self._tts_queue.qsize(),
            }

        elif action == "interrupt":
            # playback_control stop, `auto-speech stop`, the web app's end:
            # his stop, which holds until a play or resume (ADR-024).
            by = str(payload.get("source") or payload.get("caller") or "").strip()[:80]
            self._stop_speech(by=f"a socket interrupt ({by})" if by else "a socket interrupt (playback_control stop, auto-speech stop or the web app)")
            return {"status": "ok", "action": "interrupt", "stopped": True}

        elif action == "resume":
            queued = self._resume_speech()
            return {"status": "ok", "action": "resume", "was_stopped": queued >= 0, "queued": max(queued, 0)}

        elif action == "missed_add":
            return self._missed_add(payload)

        elif action == "queue":
            return {"status": "ok", "action": "queue", "items": self._queue_view(), "playing": self._line_in_flight()}

        elif action == "status":
            asked = clean_line_id(payload.get("line_id"))
            depths = self._priority_arbiter.depths() if hasattr(self, "_priority_arbiter") else {}
            try:
                hold = self._sink.hold_status
            except Exception:  # noqa: BLE001 — a test sink may not have it
                hold = None
            return {
                "status": "ok",
                "daemon_state": self._engine_state,
                "active_priority": self._active_priority,
                "queue_depths": depths,
                "total_dropped_phases": self._dropped_phases,
                "uptime_seconds": round(time.time() - getattr(self, "_start_time", time.time()), 2),
                "sound_yield": hold if isinstance(hold, dict) else None,
                "missed": self._missed_counts(),
                "speaker": self._speaker_status(),
                "stopped": self._is_stopped(),
                "stopped_since": getattr(self, "_stopped_since", None),
                "queued": sum(int(v) for v in depths.values()) if isinstance(depths, dict) else 0,
                "line_in_flight": self._line_in_flight(),
                **({"line": self._line_ledger().answer(asked)} if asked else {}),
            }

        return {
            "status": "error",
            "error_code": "INVALID_ACTION",
            "message": f"Unknown action: {action}",
        }

    def _handle_p1_interrupt(self) -> None:
        """Handles Priority 1 User Barge-in: interrupts sink, transitions to PURGING, wipes P3/P4."""
        state_lock = getattr(self, "_state_lock", None)
        if state_lock:
            with state_lock:
                self._interrupted = True
                self._engine_state = "INTERRUPTED"
                self._active_item = None
                self._active_priority = None
        else:
            self._interrupted = True
            self._engine_state = "INTERRUPTED"
            self._active_item = None
            self._active_priority = None

        if hasattr(self, "_sink") and self._sink is not None:
            try:
                self._sink.interrupt()
            except Exception as exc:
                _log(f"sink interrupt error: {exc}")

        if state_lock:
            with state_lock:
                self._engine_state = "PURGING"
        else:
            self._engine_state = "PURGING"

        purged = 0
        if hasattr(self, "_priority_arbiter"):
            before = self._queued_line_ids()
            purged = self._priority_arbiter.purge_lower_queues()
            self._dropped_phases += purged
            gone = before - self._queued_line_ids()
            for line_id in gone:
                self._line_ledger().dropped(line_id, DROP_STOPPED if self._is_stopped() else DROP_INTERRUPTED)

        self._update_depth(self._tts_queue.qsize())

        if state_lock:
            with state_lock:
                self._engine_state = "IDLE"
        else:
            self._engine_state = "IDLE"

        _log(f"P1 User Barge-in completed: purged {purged} lower items")

    def _handle_p2_preemption(self, q_item: QueueItem) -> None:
        """Handles Priority 2 Explicit MCP requests: enqueues sequentially without interrupting active speech."""
        # Route through self._tts_queue.put so QueueProxyFacade increments _unfinished_tasks
        if hasattr(self, "_priority_arbiter"):
            self._tts_queue.put(q_item)
        else:
            self._tts_queue.put(q_item.payload)

    def _tail_events(self) -> None:
        # Resume from last watermark if present (lets us survive restarts
        # without re-narrating). Otherwise start from end-of-file.
        try:
            offset = int(WATERMARK_FILE.read_text().strip())
        except (OSError, ValueError):
            offset = EVENTS_LOG.stat().st_size if EVENTS_LOG.exists() else 0

        while not self._stop.is_set():
            if EVENTS_LOG.exists():
                try:
                    size = EVENTS_LOG.stat().st_size
                except OSError:
                    size = offset
                if size < offset:
                    # log was truncated/rotated externally; rewind
                    offset = 0
                if size > offset:
                    with EVENTS_LOG.open("rb") as f:
                        f.seek(offset)
                        chunk = f.read(size - offset)
                    # Consume only up to the last COMPLETE line. A trailing
                    # partial line means the hook is mid-append; advancing
                    # the offset past it would drop that event (its head
                    # fails to parse now) AND the next read's fragment (its
                    # tail fails to parse then), losing the event silently.
                    last_newline = chunk.rfind(b"\n")
                    if last_newline != -1:
                        consumed = last_newline + 1
                        offset += consumed
                        self._process_chunk(chunk[:consumed])
                        try:
                            WATERMARK_FILE.write_text(str(offset))
                        except OSError:
                            pass

            # The meeting watch: notices a meeting's end and queues the summary line.
            try:
                self._watch_meeting()
            except Exception as exc:  # noqa: BLE001 — never let the watch stop the loop
                _log(f"meeting watch error: {exc!r}")

            # Idle shutdown: ensure we only shut down when truly idle (no active audio and empty queue)
            is_busy = (
                (hasattr(self, "_sink") and self._sink is not None and getattr(self._sink, "is_playing", False))
                or (hasattr(self, "_tts_queue") and self._tts_queue.qsize() > 0)
                or getattr(self, "_engine_state", "IDLE") in ("PLAYING", "SYNTHESIZING")
            )
            if is_busy:
                self._last_event_ts = time.time()
            elif time.time() - self._last_event_ts > self._idle_shutdown:
                _log(f"idle for >{self._idle_shutdown}s → shutting down")
                if self._fsm.can(IDLE_SHUTDOWN):
                    self._fsm.transition(IDLE_SHUTDOWN)
                return

            # Wall-clock phase flush
            classifier = getattr(self, "_classifier", None)
            if classifier and hasattr(classifier, "_current") and classifier._current:
                for sid, current in list(classifier._current.items()):
                    silence_s = getattr(classifier, "_silence_seconds", 0.5)
                    if current.events and (time.time() - current.events[-1].ts > silence_s):
                        closed = classifier.flush(sid)
                        if closed:
                            self._maybe_enqueue(closed)

            self._stop.wait(POLL_INTERVAL_S)

    def _is_cron_tick(self, transcript_path: str) -> bool:
        if not transcript_path:
            _log("DEBUG is_cron_tick: False (no path)")
            return False
        try:
            if os.path.exists(transcript_path):
                with open(transcript_path, "r") as f:
                    lines = f.readlines()
                    for line in reversed(lines):
                        try:
                            record = json.loads(line)
                            if record.get("type") in ("USER_INPUT", "SYSTEM_MESSAGE"):
                                content = record.get("content", "")
                                if "[Message]" in content and "priority=" in content:
                                    _log("DEBUG is_cron_tick: True (found cron content)")
                                    return True
                                _log(
                                    f"DEBUG is_cron_tick: False (found non-cron content: {content[:50]})"
                                )
                                break
                        except Exception:
                            pass
        except Exception as e:
            _log(f"DEBUG is_cron_tick: exception {e}")
        _log("DEBUG is_cron_tick: False (fell through)")
        return False

    def _process_chunk(self, chunk: bytes) -> None:

        # Split on newlines, ignore partial trailing line (next read picks it up).
        text = chunk.decode("utf-8", errors="replace")
        lines = text.splitlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._last_event_ts = time.time()

            # Per-session marker recheck. The hook gates by CLAUDE_CODE_
            # SESSION_ID at fire time, but the daemon is global — without
            # this check, a stale event from a session whose marker has
            # since been removed would still narrate. Look up the
            # event's session_id (Claude Code includes it in every hook
            # payload) and require the corresponding marker to still
            # exist. The hook check is cheap; the speak path is not.
            payload = ev.get("payload") or {}
            session_id = payload.get("session_id") or ""
            if not session_id:
                continue
            session_marker = Path.home() / ".claude" / "auto-speech-narrate-sessions" / session_id
            if not session_marker.exists():
                continue
            if not self._marker_live(session_marker, session_id):
                continue
            # One id, taken in once (ADR-025): narrate_event's line_id.
            event_id = clean_line_id(ev.get("line_id"))
            if event_id:
                if self._line_ledger().admit(event_id, str(ev.get("event", ""))) is not None:
                    _log(f"event {event_id} already taken in; ignored")
                    continue
                self._line_ledger().done(event_id)

            # Stop event → flush in-flight phase but SUPPRESS narration.
            # The end-of-turn autoplay owns the final read; we don't want
            # the narrator's post-Stop flush competing with it (and
            # killing autoplay's mpv via _kill_prior_session).
            event_type = ev.get("event", "")
            if event_type == "UserPromptSubmit":
                if self._is_cron_tick(ev.get("payload", {}).get("transcriptPath")):
                    _log("Skipping UserPromptSubmit narration for background cron tick.")
                    continue

                # Preserve playback and queue for sessions enrolled in autoplay mode
                if session_id:
                    ap_marker = Path.home() / ".claude" / "auto-speech-autoplay-enabled" / session_id
                    if ap_marker.exists() and self._marker_live(ap_marker, session_id):
                        _log(f"Session {session_id} has autoplay enabled; preserving queue and playback on UserPromptSubmit")
                        self._classifier.flush(session_id)
                        self._phases_this_turn = 0
                        continue

                # Immediately interrupt any currently playing audio and purge lower queues!
                self._handle_p1_interrupt()
                try:
                    mpv_pid_file = state_path("auto-speech-mpv.pid")
                    if mpv_pid_file.is_file():
                        global_pid = int(mpv_pid_file.read_text().strip())
                        if _pid_is_mpv(global_pid):
                            os.kill(global_pid, signal.SIGTERM)
                        mpv_pid_file.unlink(missing_ok=True)
                except (OSError, ValueError, ProcessLookupError):
                    pass

                self._classifier.flush(session_id)
                self._phases_this_turn = 0
                try:
                    summarizer = self._get_summarizer()
                    if hasattr(summarizer, "generate_conversational"):
                        history = ev.get("payload", {}).get("conversation_history", "")
                        words = summarizer.generate_conversational(
                            history, "UserPromptSubmit", session_id=session_id
                        )
                        if words:
                            self._enqueue_phase(words)
                except Exception as e:
                    _log(f"Failed to generate start words: {e}")
                continue

            if event_type == "Stop":
                self._classifier.flush(session_id)
                fully_idle = ev.get("payload", {}).get("fullyIdle", False)
                if not fully_idle:
                    continue

                if self._is_cron_tick(ev.get("payload", {}).get("transcriptPath")):
                    _log("Skipping Stop narration for background cron tick.")
                    self._phases_this_turn = 0
                    continue

                # Yield to autoplay_worker.py if this session has opted into autoplay!
                # Autoplay handles Claude CLI rewrite and local response caching.
                # If autoplay is NOT enabled for this session, Narrator provides the conversational sign-off.
                if session_id:
                    ap_marker = Path.home() / ".claude" / "auto-speech-autoplay-enabled" / session_id
                    if ap_marker.exists() and self._marker_live(ap_marker, session_id):
                        _log(f"Session {session_id} has autoplay enabled; yielding Stop summary to autoplay_worker.")
                        self._phases_this_turn = 0
                        continue

                history = ev.get("payload", {}).get("conversation_history", "")
                self._enqueue_phase(
                    {
                        "type": "Stop",
                        "history": history,
                        "phases": self._phases_this_turn,
                        "session_id": session_id,
                    }
                )
                self._phases_this_turn = 0
                continue

            closed = self._classifier.feed(ev)
            if closed is not None:
                self._maybe_enqueue(closed)

    def _maybe_enqueue(self, phase: Phase) -> None:
        """Apply chattiness filters then enqueue. Drops phases that are
        too small or in a suppressed category."""
        if not phase.events:
            return
        if phase.category in SUPPRESSED_CATEGORIES:
            _log(f"skipping phase category={phase.category.value} (suppressed)")
            return
        min_events = int(self._config.get("min_events_per_phase", 1))
        if len(phase.events) < min_events:
            _log(
                f"skipping phase category={phase.category.value} "
                f"events={len(phase.events)} (< {min_events})"
            )
            return
        self._enqueue_phase(phase)
        self._phases_this_turn += 1
        self._update_depth(self._tts_queue.qsize())
        _log(
            f"enqueued phase={phase.category.value} "
            f"events={len(phase.events)} depth={self._tts_queue.qsize()}"
        )

    def _enqueue_phase(self, phase: Phase | str | dict) -> None:
        """Bounded put with drop-oldest backpressure.

        The TTS worker blocks on playback-to-completion to preserve true
        FIFO, so a burst of phases can outrun it. Rather than let the
        queue grow without bound (a slow memory leak under sustained tool
        use), cap it at max_queue_depth and shed the OLDEST queued phase
        when full — stale narration is the least worth speaking — counting
        every drop so the loss is observable in the daemon log.
        """
        lock = getattr(self, "_queue_lock", None)
        if lock is not None:
            with lock:
                self._enqueue_item(phase)
        else:
            self._enqueue_item(phase)

    def _enqueue_item(self, item: Phase | str | dict) -> None:
        try:
            self._tts_queue.put_nowait(item)
            return
        except queue.Full:
            pass
        try:
            if hasattr(self._tts_queue, "shed_oldest_low_priority"):
                dropped = self._tts_queue.shed_oldest_low_priority()
                # Note: QueueProxyFacade._on_purged automatically decrements
                # _unfinished_tasks; task_done() is not called here to avoid double-decrement.
            else:
                dropped = self._tts_queue.get_nowait()
                self._tts_queue.task_done()

            if dropped is not None:
                if getattr(dropped, "line_id", ""):
                    self._line_ledger().dropped(dropped.line_id, "shed: the queue was full")
                self._dropped_phases += 1
                cat = getattr(getattr(dropped, "category", None), "value", None)
                if cat is None:
                    cat = getattr(dropped, "tag", type(dropped).__name__)
                _log(
                    f"queue full (max={self._max_queue}); dropped oldest "
                    f"phase={cat} total_dropped={self._dropped_phases}"
                )
        except queue.Empty:
            pass
        try:
            self._tts_queue.put_nowait(item)
        except queue.Full:
            self._dropped_phases += 1
            _log(
                "queue still full after shed; dropped new phase "
                f"total_dropped={self._dropped_phases}"
            )

    def _update_depth(self, depth: int) -> None:
        try:
            DEPTH_FILE.write_text(str(depth))
        except OSError:
            pass

    def _progress(self, event: str, *, expect: float = 0.0) -> None:
        """Stamp the worker's step. `expect` is how long this step may run
        at most (its budget), so a long clip is not read as a stall."""
        now = time.time()
        stamp = {
            "at": now,
            "event": event,
            "depth": self._tts_queue.qsize() if hasattr(self, "_tts_queue") else 0,
            "expected_done_at": now + expect if expect > 0 else None,
        }
        try:
            PROGRESS_FILE.write_text(json.dumps(stamp))
        except OSError:
            pass

    # -- speech under load (ADR-019) ------------------------------------------

    def _cfg(self, key: str, default: float) -> float:
        cfg = getattr(self, "_config", None)
        if not isinstance(cfg, dict):
            return float(default)
        try:
            return float(cfg.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    @property
    def load_watch(self) -> LoadWatch:
        watch = getattr(self, "_load_watch", None)
        if watch is None:
            watch = LoadWatch()
            self._load_watch = watch
        return watch

    def _load_scale(self) -> float:
        """How much a budget stretches under load: the load per core, capped
        by load_timeout_scale_cap (default 4), never under 1."""
        return self.load_watch.scale(self._cfg("load_timeout_scale_cap", DEFAULT_LOAD_SCALE_CAP))

    def _await_load(self, item: QueueItem) -> bool:
        """A retried line waits for the load per core to fall under
        retry_load_per_core (up to retry_wait_seconds), then goes ahead.
        True: play it now. False: an interrupt or a preemption came first;
        the line is back on the queue, unchanged, for the next pass."""
        threshold = self._cfg("retry_load_per_core", DEFAULT_RETRY_LOAD_PER_CORE)
        deadline = self._cfg("retry_wait_seconds", DEFAULT_RETRY_WAIT_SECONDS)

        def should_stop() -> bool:
            return bool(getattr(self, "_interrupted", False) or getattr(self, "_preempted", False))

        self._progress("waiting for load", expect=deadline)
        outcome = self.load_watch.wait_until_below(threshold, deadline_seconds=deadline, should_stop=should_stop)
        load = self.load_watch.load_per_core()
        name = (item.line_text or str(item.payload))[:50]
        if outcome == "stopped":
            _log(f"retry {item.retries} of {name!r} waits again: interrupted or preempted while the load was {load:.1f}/core")
            try:
                self._tts_queue.put(item)
            except Exception as exc:  # noqa: BLE001
                _log(f"retry requeue error: {exc!r}")
            return False
        _log(f"retry {item.retries} of {name!r} goes ahead: load {load:.1f}/core ({outcome})")
        return True

    def _retry(self, text: str, item: Optional[QueueItem], *, reason: str, wav: Optional[Path] = None,
               held: Optional[MissedLine] = None) -> bool:
        """Queue a timed-out line again, whole (ADR-019): the words when its
        synthesis timed out, its finished WAV when its playback stalled, so a
        cut-off line replays from the start without a second synthesis.
        False when the line has used its retries (timeout_retries, default
        3) or came without an item: the caller sets it aside."""
        if item is None:
            return False
        limit = int(self._cfg("timeout_retries", DEFAULT_TIMEOUT_RETRIES))
        if item.retries >= limit:
            _log(f"retries spent ({item.retries} of {limit}) for {text[:50]!r}: {reason}")
            return False
        payload: Any = text
        if wav is not None:
            payload = str(self._keep_retry_wav(wav))
        again = QueueItem(
            priority=item.priority,
            payload=payload,
            source_hash=item.source_hash,
            session_id=item.session_id,
            voice_id=item.voice_id,
            speed=item.speed,
            enqueued_at=item.enqueued_at,
            request_id=item.request_id,
            missed_id=(held.id if held is not None else item.missed_id),
            hold=item.hold,
            retries=item.retries + 1,
            line_text=text,
            origin_file=item.origin_file,
            line_id=item.line_id,
        )
        try:
            self._tts_queue.put(again)
        except Exception as exc:  # noqa: BLE001
            _log(f"retry queue error: {exc!r}")
            return False
        if item.line_id:
            self._line_ledger().requeued(item.line_id, retry=True)
        _log(f"retry {again.retries} of {limit} queued for {text[:50]!r} ({reason}); it waits for the load to fall")
        return True

    def _keep_retry_wav(self, wav: Path) -> Path:
        """The finished WAV of a cut-off line, copied under RETRY_DIR so the
        synthesis temp file can go; a WAV already there is reused."""
        wav = Path(wav)
        try:
            if wav.parent == RETRY_DIR:
                return wav
            RETRY_DIR.mkdir(parents=True, exist_ok=True)
            kept = RETRY_DIR / f"{new_id()}.wav"
            shutil.copyfile(wav, kept)
            return kept
        except OSError as exc:
            _log(f"retry wav not kept ({exc!r}); the line is synthesized again")
            return wav

    def _drop_retry_wav(self, path: Path, item: Any) -> None:
        """A retry WAV is removed once its item is done with it: played, or
        set aside for good. A WAV queued again for one more retry stays."""
        try:
            if Path(path).parent != RETRY_DIR:
                return
            still_queued = any(
                isinstance(q, QueueItem) and str(q.payload) == str(path)
                for q in self._queued_items()
            )
            if not still_queued:
                Path(path).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    def _queued_items(self) -> list[Any]:
        arbiter = getattr(self, "_priority_arbiter", None)
        items: list[Any] = []
        if arbiter is not None:
            for band in ("_p1", "_p2", "_p3", "_p4"):
                items.extend(list(getattr(arbiter, band, ())))
            return items
        q = getattr(self, "_tts_queue", None)
        inner = getattr(q, "queue", None)
        if inner is not None:
            items.extend(list(inner))
        return items

    def _synthesis_timeout(self, text: str, profile: Optional[VoiceProfile]) -> Optional[float]:
        """The budget for one synthesis: the line's spoken length times
        synthesis_timeout_factor, never under the floor, stretched by the
        load scale (ADR-019); None when off."""
        factor = float(self._config.get("synthesis_timeout_factor", 3.0)) if isinstance(getattr(self, "_config", None), dict) else 3.0
        if factor <= 0:
            return None
        try:
            cps = float(getattr(profile, "chars_per_second", 0.0) or 0.0)
        except (TypeError, ValueError):   # a test's mock profile
            cps = 0.0
        if cps <= 0:
            cps = FALLBACK_CHARS_PER_SEC
        spoken = len(text) / cps
        budget = max(SYNTHESIS_TIMEOUT_FLOOR_SECONDS, spoken * factor) * self._load_scale()
        # An engine that times out its own process (AppleSayEngine: a try and
        # one retry) gets the room to do so, so it is the one that kills a hung
        # `say`; the executor's budget stays the backstop (#m1023-1717-fd7b).
        engine = getattr(getattr(self, "_tts_executor", None), "engine", None)
        worst = getattr(engine, "worst_case_seconds", None)
        if callable(worst):
            try:
                budget = max(budget, float(worst(text)) + 5.0)
            except (TypeError, ValueError):
                pass
        return budget

    def _submit_synthesis(self, synth: Any, text: str, profile: Any, out: Path, budget: Optional[float]) -> bool:
        """Run one synthesis on the executor, under its budget when the
        executor takes one (TTSExecutor); a test's executor may not."""
        submit = self._tts_executor.submit
        takes = getattr(self, "_executor_takes_timeout", None)
        if takes is None:
            try:
                takes = "timeout" in inspect.signature(submit).parameters
            except (TypeError, ValueError):
                takes = False
            self._executor_takes_timeout = takes
        if takes and budget is not None:
            return submit(synth.synthesize_one, text, profile, out, timeout=budget)
        return submit(synth.synthesize_one, text, profile, out)

    def _play_timeout(self, path: Path) -> Optional[float]:
        """The wall-clock budget for one play, used by the sink only while
        mpv's position cannot be read (ADR-019; a clip still advancing is
        never cut off): twice the clip's length plus
        play_timeout_margin_seconds, stretched by the load scale; None when
        off or the length is unknown."""
        margin = float(self._config.get("play_timeout_margin_seconds", 15.0)) if isinstance(getattr(self, "_config", None), dict) else 15.0
        if margin <= 0:
            return None
        duration = _wav_duration_seconds(path)
        if duration <= 0:
            return None
        return (duration * 2.0 + margin) * self._load_scale()

    def _get_summarizer(self) -> Summarizer:
        lock = getattr(self, "_summarizer_lock", None)
        if lock is None:
            self._summarizer_lock = lock = threading.Lock()
        with lock:
            if self._summarizer is None:
                _log("loading summarizer (first use)")
                t0 = time.time()
                self._summarizer = load_summarizer(self._config)
                _log(
                    f"summarizer loaded in {time.time() - t0:.1f}s: {type(self._summarizer).__name__}"
                )
            return self._summarizer

    def _eager_load(self) -> None:
        """Triggered at daemon boot. Pays the model-load cost once at
        startup so the first real phase doesn't lag. Errors are logged
        but never raised — load_summarizer downgrades to Mock on its
        own, and a failed eager load just means the lazy path runs
        later instead."""
        try:
            self._get_summarizer()
        except Exception as exc:  # noqa: BLE001 — boot must not die on load
            _log(f"eager-load failed (will retry lazily on first phase): {exc!r}")

    def _ensure_tts_initialized(self) -> None:
        if getattr(self, "_tts_initialized", False):
            return
        if getattr(self, "_tts_executor", None) is None:
            # Fallback for stress tests that bypass __init__
            from tts_executor import TTSExecutor
            self._tts_executor = TTSExecutor(
                engine=getattr(self, "_engine", None),
                synth=getattr(self, "_synth", None)
            )
                
        if self._profile is None:
            self._profile = _load_profile_or_fallback(self._config)
        self._tts_executor.ensure_loaded()
        self._tts_initialized = True

    def _tts_worker(self) -> None:
        _log("tts_worker thread started" + (" (QoS user-interactive)" if set_thread_qos_user_interactive() else ""))
        self._ensure_tts_initialized()
        while True:
            state_lock = getattr(self, "_state_lock", None)
            if state_lock:
                with state_lock:
                    self._engine_state = "IDLE"
                    self._active_priority = None
                    self._active_item = None

            self._progress("idle")
            phase = self._tts_queue.get()
            if phase is None:
                _log("tts_worker received sentinel; stopping")
                return
            self._progress("taken")

            arbiter = getattr(self, "_priority_arbiter", None)
            item_p = getattr(arbiter, "active_priority", Priority.TOOL_NARRATION) if arbiter else Priority.TOOL_NARRATION
            item_obj = getattr(arbiter, "active_item", None) if arbiter else None

            if isinstance(phase, QueueItem):
                item_p = phase.priority
                item_obj = phase
                phase = phase.payload
            line_id = getattr(item_obj, "line_id", "") if isinstance(item_obj, QueueItem) else ""
            if line_id:
                self._line_ledger().taken(line_id)

            stopping = getattr(self, "_stop", None)
            if stopping is not None and stopping.is_set():
                # Shutting down (ADR-024): what is still queued goes on the
                # missed list rather than playing past the restart.
                try:
                    self._set_aside_item(phase, item_obj, REASON_RESTART)
                except Exception as exc:  # noqa: BLE001
                    _log(f"shutdown set-aside error: {exc!r}")
                self._tts_queue.task_done()
                continue

            if isinstance(item_obj, QueueItem) and item_obj.retries > 0:
                if not self._await_load(item_obj):
                    continue   # put back for later: an interrupt or a preemption came first

            if state_lock:
                with state_lock:
                    self._active_priority = item_p
                    self._active_item = item_obj
                    self._engine_state = "SYNTHESIZING"
                    self._interrupted = False
                    self._preempted = False
            else:
                self._active_priority = item_p
                self._active_item = item_obj
                self._engine_state = "SYNTHESIZING"
                self._interrupted = False
                self._preempted = False

            try:
                # Check for cached WAV playback (from play_cache action)
                if (isinstance(phase, Path) or (isinstance(phase, str) and phase.endswith(".wav"))) and Path(phase).is_file():
                    if getattr(self, "_interrupted", False) or getattr(self, "_preempted", False):
                        _log("play_cache skipped: interrupted or preempted prior to play")
                    else:
                        if state_lock:
                            with state_lock:
                                if not (getattr(self, "_interrupted", False) or getattr(self, "_preempted", False)):
                                    self._engine_state = "PLAYING"
                        else:
                            self._engine_state = "PLAYING"
                        try:
                            self._play_cached(
                                Path(phase),
                                item_obj if isinstance(item_obj, QueueItem) else None,
                                text=getattr(item_obj, "line_text", "") or "",
                            )
                        except Exception as exc:  # noqa: BLE001
                            _log(f"play_cache error: {exc!r}")
                        finally:
                            self._drop_retry_wav(Path(phase), item_obj)
                            if state_lock:
                                with state_lock:
                                    if self._engine_state == "PLAYING":
                                        self._engine_state = "IDLE"
                            else:
                                if getattr(self, "_engine_state", "IDLE") == "PLAYING":
                                    self._engine_state = "IDLE"
                elif isinstance(phase, str):
                    s_hash = getattr(item_obj, "source_hash", None)
                    v_id = getattr(item_obj, "voice_id", None)
                    spd = getattr(item_obj, "speed", None)
                    self._speak(
                        phase,
                        source_hash=s_hash,
                        voice_id=v_id,
                        speed=spd,
                        item=item_obj if isinstance(item_obj, QueueItem) else None,
                    )
                elif isinstance(phase, dict):
                    _log(f"Dict received in worker: {phase}")
                    if phase.get("type") == "Stop":
                        summ = self._get_summarizer()
                        history = phase.get("history", "")
                        phases_count = phase.get("phases", 0)
                        session_id = phase.get("session_id", "")

                        # SILENCE INVISIBLE CRON TURNS: If the turn was purely a cron tick with no user input, do not announce completion.
                        if (
                            "<!-- cron tick -->" in history
                            and "User:" not in history.split("<!-- cron tick -->")[-1]
                        ):
                            _log("Silencing end-of-turn summary for invisible cron tick.")
                        else:
                            words = ""
                            if hasattr(summ, "generate_conversational"):
                                words = summ.generate_conversational(
                                    history,
                                    "Stop",
                                    phases_this_turn=phases_count,
                                    session_id=session_id,
                                    prompt_chars=0,
                                )
                            _log(f"OUTPUT: {words}")
                            if words:
                                self._speak(words, session_id=session_id)
                else:
                    _log(f"Phase received in worker: {phase}")
                    summ = self._get_summarizer()
                    line = summ.summarize(phase)
                    _log(f"Summarizer generated: {line}")
                    if line:
                        self._speak(line, session_id=getattr(phase, "session_id", None) or None)
            except Exception as exc:  # noqa: BLE001 — keep the worker loop alive
                _log(f"tts_worker error: {exc!r}")
            finally:
                is_preempted = getattr(self, "_preempted", False)
                if line_id:
                    # A line the pass neither played nor set aside nor queued
                    # again is dropped: cut by an interrupt, or failed (ADR-025).
                    if is_preempted:
                        self._line_ledger().requeued(line_id)
                    else:
                        cut = getattr(self, "_interrupted", False)
                        self._line_ledger().settle(line_id, DROP_INTERRUPTED if cut else DROP_FAILED)
                # When preempted, active item was re-queued; do not decrement _unfinished_tasks
                if not is_preempted:
                    self._tts_queue.task_done()
                self._update_depth(self._tts_queue.qsize())
                self._progress("done")
                if state_lock:
                    with state_lock:
                        self._engine_state = "IDLE"
                        self._active_priority = None
                        self._active_item = None
                        self._preempted = False
                        self._interrupted = False
                else:
                    self._engine_state = "IDLE"
                    self._active_priority = None
                    self._active_item = None
                    self._preempted = False
                    self._interrupted = False

    # -- missed list and sound-yield bookkeeping ---------------------------------

    def _missed_counts(self) -> Optional[dict[str, int]]:
        missed = getattr(self, "_missed", None)
        if missed is None:
            return None
        try:
            return missed.counts()
        except Exception:  # noqa: BLE001
            return None

    def _max_wait_seconds(self) -> float:
        gate = getattr(self, "_yield_gate", None)
        if gate is not None:
            try:
                return float(gate.settings().max_wait_seconds)
            except Exception:  # noqa: BLE001
                pass
        return DEFAULT_MAX_WAIT_SECONDS

    def _stale_backlog(self, item: QueueItem) -> tuple[bool, float]:
        """A line is stale backlog when a hold ended after it was queued (it
        sat behind the other sound) and it is now older than the longest
        wait. Such a line goes to the missed list instead of playing late;
        a line that is merely old because the queue was busy still plays."""
        queued = float(getattr(item, "enqueued_at", None) or time.time())
        age = max(0.0, time.time() - queued)
        ended = getattr(self, "_last_hold_ended_at", None)
        if ended is None or ended < queued:
            return False, age
        return age > self._max_wait_seconds(), age

    def _note_hold_ended(self, outcome: Any) -> None:
        if outcome is not None and float(getattr(outcome, "waited_seconds", 0.0) or 0.0) > 0:
            self._last_hold_ended_at = time.time()

    # -- the meeting watch (ADR-018) ----------------------------------------------

    def _meeting_now(self) -> tuple[str, ...]:
        """The names of what makes this a meeting right now (a meeting app
        holding the microphone, or his manual meeting mode); empty when he
        is not in one, when the gate cannot say, or when there is no gate."""
        gate = getattr(self, "_yield_gate", None)
        if gate is None:
            return ()
        try:
            assessment = gate.assess()
        except Exception:  # noqa: BLE001 — the gate fails open
            return ()
        if getattr(assessment, "reason", "") != HOLD_REASON_MEETING:
            return ()
        try:
            names = tuple(assessment.meeting_names())
        except Exception:  # noqa: BLE001
            names = ()
        return names or ("meeting",)

    def _meeting_summary_wanted(self) -> bool:
        gate = getattr(self, "_yield_gate", None)
        if gate is None:
            return False
        try:
            return bool(gate.settings().meeting_summary) is True
        except Exception:  # noqa: BLE001
            return True

    def _watch_meeting(self, now: Optional[float] = None) -> Optional[str]:
        """Called from the main loop. Notes when a meeting begins and ends;
        when one ends (or at the first look after a restart) with meeting
        lines no summary has counted yet, queues one summary line with their
        count, at the narration priority, through the normal gate. Each line
        is counted once, ever: the mark is on the missed list. Returns the
        summary text when one was queued, so a test can see it."""
        now = time.time() if now is None else now
        if now - float(getattr(self, "_meeting_checked_at", 0.0) or 0.0) < MEETING_POLL_S:
            return None
        self._meeting_checked_at = now
        active = bool(self._meeting_now())
        before = getattr(self, "_meeting_active", None)
        self._meeting_active = active
        if active:
            if before is not True:
                _log("meeting: began; lines are set aside as missed until it ends")
            return None
        if before is True:
            _log("meeting: ended")
        missed = getattr(self, "_missed", None)
        if missed is None or not self._meeting_summary_wanted():
            return None
        try:
            pending = missed.take_meeting_summary()
        except Exception as exc:  # noqa: BLE001
            _log(f"missed list read error: {exc!r}")
            return None
        if not pending:
            return None
        n = len(pending)
        text = (
            f"You missed {n} line{'s' if n != 1 else ''} during the meeting. "
            "Say auto-speech missed to hear them."
        )
        try:
            self.enqueue_text(text)
        except Exception as exc:  # noqa: BLE001
            _log(f"meeting: summary not queued: {exc!r}")
            return None
        _log(f"meeting: summary queued ({n} line{'s' if n != 1 else ''} set aside)")
        return text

    def _missed_line(self, text: str, item: Optional[QueueItem], *, existing: Optional[MissedLine] = None) -> MissedLine:
        if existing is not None:
            return existing
        if item is None:
            return MissedLine(id=new_id(), text=text or "", queued_at=time.time())
        return MissedLine(
            id=getattr(item, "missed_id", "") or new_id(),
            text=text or "",
            queued_at=float(getattr(item, "enqueued_at", None) or time.time()),
            priority=int(item.priority),
            source_hash=item.source_hash,
            session_id=item.session_id,
            voice_id=item.voice_id or "",
            speed=float(item.speed),
            line_id=getattr(item, "line_id", "") or "",
            origin_file=getattr(item, "origin_file", "") or None,
        )

    def _forget_missed(self, line_id: str, missed_id: str = "") -> None:
        """The line was heard: its entries leave the missed list, by its
        line id and by the missed id it was replayed under. Never raises."""
        missed = getattr(self, "_missed", None)
        if missed is None:
            return
        try:
            gone = missed.remove_line(line_id) if line_id else 0
            if missed_id and missed.remove(missed_id):
                gone += 1
        except Exception as exc:  # noqa: BLE001
            _log(f"missed list write error: {exc!r}")
            return
        if gone:
            self._control_call("list_changed")

    def _set_aside(
        self,
        text: str,
        item: Optional[QueueItem],
        *,
        reason: str,
        waited: float = 0.0,
        held_for: tuple[str, ...] = (),
        existing: Optional[MissedLine] = None,
    ) -> None:
        if getattr(item, "history_id", "") if item is not None else "":
            # A heard line played again from the control (play-item, play-all,
            # play-from) is in the history already: not set aside as missed (ADR-027).
            _log(f"replay not played ({reason}): {(text or '')[:50]!r} stays in the history, not on the missed list")
            return
        missed = getattr(self, "_missed", None)
        line = self._missed_line(text, item, existing=existing)
        line_id = getattr(item, "line_id", "") if item is not None else ""
        if missed is None:
            if line_id:
                self._line_ledger().dropped(line_id, reason)
            return
        try:
            # One entry per line id (ADR-026): set aside again, it is the same entry.
            line = missed.miss(
                line,
                reason=reason,
                waited_seconds=waited,
                held_for=held_for,
            )
        except Exception as exc:  # noqa: BLE001
            _log(f"missed list write error: {exc!r}")
        if line_id:
            self._line_ledger().dropped(line_id, reason, missed_id=line.id)
        self._control_call("list_changed")

    def _sink_play(self, path: Path, timeout: Optional[float] = None, **hold_kwargs: Any) -> None:
        """sink.play with the hold keywords when the sink knows them
        (NativeAudioSink), and the play budget when it knows that; a plain
        play(path) for a sink that knows neither."""
        play = self._sink.play
        takes = getattr(self, "_sink_takes_hold_kwargs", None)
        if takes is None:
            try:
                params = inspect.signature(play).parameters
                takes = ("on_hold" in params, "timeout" in params, "on_start" in params)
            except (TypeError, ValueError):
                takes = (False, False, False)
            self._sink_takes_hold_kwargs = takes
        if isinstance(takes, bool):   # an older test set the flag by hand
            takes = (takes, False, False)
        takes = (*takes, False, False)[:3]
        on_start = hold_kwargs.pop("on_start", None)
        kwargs: dict[str, Any] = {}
        if takes[0]:
            kwargs.update(hold_kwargs)
        if takes[1] and timeout is not None:
            kwargs["timeout"] = timeout
        if on_start is not None:
            if takes[2]:
                kwargs["on_start"] = on_start
            else:
                on_start()  # a sink that cannot say: the play starts now
        play(path, **kwargs)

    def _play_item(self, path: Path, item: Optional[QueueItem], *, text: str, session_id: Optional[str] = None) -> bool:
        """Play through the sink with the missed-list bookkeeping around the
        hold: held → on the list as held; longest wait or interrupted while
        held → stays on the list as missed; played → off the list. Returns
        True when the line was heard (so the speaker bookkeeping can move)."""
        age = 0.0
        hold = True
        if item is not None:
            age = max(0.0, time.time() - float(getattr(item, "enqueued_at", None) or time.time()))
            hold = bool(getattr(item, "hold", True))
        held: dict[str, MissedLine] = {}
        missed = getattr(self, "_missed", None)
        name = (text or Path(path).name)[:50]

        def on_hold(names: tuple[str, ...]) -> None:
            self._control_call("held")
            if missed is None:
                return
            try:
                held["line"] = missed.hold(self._missed_line(text, item), names)
            except Exception as exc:  # noqa: BLE001
                _log(f"missed list write error: {exc!r}")

        sid = session_id or (getattr(item, "session_id", None) if item is not None else None)

        def on_start() -> None:
            if item is not None and getattr(item, "line_id", ""):
                self._line_ledger().playing(item.line_id)
            # A heard line played again is its own row in the control (no new
            # row); a missed line playing is its missed row; a fresh line is
            # a now-row with its sender and its words, estimated from the
            # clip as the history does (contract 1.6).
            replayed = getattr(item, "history_id", "") if item is not None else ""
            missed_id = getattr(item, "missed_id", "") if item is not None else ""
            if replayed:
                self._control_call("playing", text, None, replayed)
            elif missed_id:
                self._control_call("playing", text, self._sender_name(sid), MISSED_PREFIX + missed_id,
                                   estimate_word_timings(text, _wav_duration_seconds(Path(path))))
            else:
                self._control_call("playing", text, self._sender_name(sid), None,
                                   estimate_word_timings(text, _wav_duration_seconds(Path(path))))

        heard = False
        try:
            if self._set_aside_if_stopped(text, item):
                return False
            heard = self._play_item_body(path, item, text=text, session_id=session_id, age=age, hold=hold,
                                         held=held, on_hold=on_hold, on_start=on_start, name=name)
            return heard
        finally:
            if not heard:
                self._control_call("ended", None)

    def _play_item_body(
        self,
        path: Path,
        item: QueueItem | None,
        *,
        text: str,
        session_id: str | None,
        age: float,
        hold: bool,
        held: dict[str, MissedLine],
        on_hold: Callable[[tuple[str, ...]], None],
        on_start: Callable[[], None],
        name: str,
    ) -> bool:
        missed = getattr(self, "_missed", None)
        budget = self._play_timeout(path)
        self._progress("playing", expect=(budget or 0.0) + self._max_wait_seconds())
        try:
            self._sink_play(path, hold=hold, already_waited=age, on_hold=on_hold, on_start=on_start, timeout=budget)
        except PlaybackError as exc:
            if "timed out" not in str(exc) and "stalled" not in str(exc):
                raise
            # mpv was killed by the sink. The whole line is queued again
            # (ADR-019); only past its retries is it set aside.
            if self._retry(text, item, reason=str(exc), wav=Path(path), held=held.get("line")):
                _log(f"playback cut off ({exc}); the whole line is queued again: {name!r}")
                return False
            self._set_aside(text, item, reason=REASON_TIMEOUT, waited=age, existing=held.get("line"))
            _log(f"missed: {exc}; playback abandoned, set aside: {name!r}")
            return False
        except PlaybackYielded as exc:
            outcome = exc.outcome
            self._note_hold_ended(outcome)
            in_meeting = getattr(outcome, "reason", "") == HOLD_REASON_MEETING
            self._set_aside(
                text,
                item,
                reason=REASON_MEETING if in_meeting else REASON_LONGEST_WAIT,
                waited=age + outcome.waited_seconds,
                held_for=tuple(outcome.blockers),
                existing=held.get("line"),
            )
            who = ", ".join(outcome.blockers) or "another app"
            if in_meeting:
                _log(f"missed: in a meeting ({who}), set aside: {name!r}")
            else:
                _log(f"missed: set aside after {outcome.waited_seconds:.0f}s held while {who} played: {name!r}")
            return False

        if getattr(self, "_shutdown_cut", False):
            self._set_aside(text, item, reason=REASON_RESTART, waited=age, existing=held.get("line"))
            _log(f"missed: cut by the shutdown, set aside: {name!r}")
            return False
        outcome = getattr(self._sink, "last_hold", None)
        self._note_hold_ended(outcome)
        line = held.get("line")
        if line is not None and getattr(outcome, "status", None) == ABORTED:
            self._set_aside(
                text,
                item,
                reason=REASON_INTERRUPTED,
                waited=age + float(outcome.waited_seconds),
                held_for=tuple(outcome.blockers),
                existing=line,
            )
            _log(f"missed: hold interrupted after {outcome.waited_seconds:.0f}s, set aside: {name!r}")
            return False
        if missed is not None:
            try:
                if line is not None:
                    missed.remove(line.id)
                elif item is not None and getattr(item, "missed_id", ""):
                    missed.remove(item.missed_id)
            except Exception as exc:  # noqa: BLE001
                _log(f"missed list write error: {exc!r}")
        if item is not None and getattr(item, "line_id", ""):
            self._line_ledger().done(item.line_id)
            self._forget_missed(item.line_id)  # any other copy of it on the list (ADR-026)
        self._heard(path, item, text=text, session_id=session_id)
        return True

    # -- the speech history (ADR-020) ---------------------------------------------

    def _close_player(self) -> None:
        """Stop the player launcher's worker and close its connection; the
        panel stays open for him (ADR-021). Never raises."""
        close = getattr(getattr(self, "_player", None), "close", None)
        if close is None:
            return
        try:
            close()
        except Exception as exc:  # noqa: BLE001
            _log(f"player close error: {exc!r}")

    # -- the speech control (ADR-022) ----------------------------------------------

    def _control_call(self, name: str, *args: Any) -> None:
        """Tell the speech control; never raises, never blocks speech."""
        method = getattr(getattr(self, "_control", None), name, None)
        if method is None:
            return
        try:
            method(*args)
        except Exception as exc:  # noqa: BLE001
            _log(f"control {name} error: {exc!r}")

    def _control_actions(self) -> ControlActions:
        return ControlActions(
            pause=lambda: self._sink_verb("pause"),
            resume=lambda: self._sink_verb("resume"),
            stop=lambda: self._stop_speech(by="the speech control's stop"),
            unstop=self._resume_speech,
            restart=lambda: self._sink_verb("restart"),
            skip=lambda: self._sink_verb("skip"),
            set_rate=self._set_rate,
            replay=self._replay_history_line,
            play_missed=self._play_missed_ids,
            begin_run=self._begin_control_run,
            end_run=self._end_control_run,
            seek=lambda seconds: self._sink_seek(seconds),
            position=self._sink_position,
            queue_items=self._queue_view,
            queue_clear=self._queue_clear,
            queue_remove=self._queue_remove,
            queue_move=self._queue_move,
        )

    def _sink_verb(self, name: str) -> bool:
        verb = getattr(getattr(self, "_sink", None), name, None)
        if verb is None:
            return False
        try:
            return bool(verb())
        except Exception as exc:  # noqa: BLE001
            _log(f"control: sink {name} error: {exc!r}")
            return False

    def _set_rate(self, rate: float) -> float:
        set_speed = getattr(getattr(self, "_sink", None), "set_speed", None)
        if set_speed is None:
            return 1.0
        rate = float(set_speed(rate))
        _log(f"control: playback rate {rate:g}")
        return rate

    def _replay_history_line(self, line: Any) -> None:
        """A heard line, played again from its WAV over any other sound (he
        asked); it is not exported to the history a second time. A play
        ends his stop."""
        self._resume_speech(play_set_aside=False)
        q_item = QueueItem(
            priority=Priority.EXPLICIT_MCP,
            payload=str(line.wav),
            line_text=line.text,
            history_id=line.id,
            hold=False,
            control_run=getattr(self, "_control_run_open", 0),
        )
        self._handle_p2_preemption(q_item)
        self._update_depth(self._tts_queue.qsize())
        self._last_event_ts = time.time()

    def _sink_seek(self, seconds: float) -> bool:
        seek = getattr(getattr(self, "_sink", None), "seek", None)
        if seek is None:
            return False
        try:
            return bool(seek(float(seconds)))
        except Exception as exc:  # noqa: BLE001
            _log(f"control: sink seek error: {exc!r}")
            return False

    def _sink_position(self) -> Optional[float]:
        position = getattr(getattr(self, "_sink", None), "position", None)
        if position is None:
            return None
        try:
            value = position()
        except Exception:  # noqa: BLE001
            return None
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    # -- the queue tab (contract 1.6, his 09:55) -----------------------------------

    def _queue_changed(self) -> None:
        """The arbiter's change hook: tell the control. Never blocks: the
        control only marks its queue to be sent and wakes its worker."""
        self._control_call("queue_changed")

    def _queue_item_text(self, q: QueueItem) -> str:
        if q.line_text:
            return q.line_text
        payload = q.payload
        if isinstance(payload, str) and not payload.endswith(".wav"):
            return payload
        if isinstance(payload, Path) or isinstance(payload, str):
            return Path(payload).name
        return "narration, still to be summarised"

    def _queue_item_source(self, q: QueueItem) -> Optional[str]:
        if q.history_id:
            names = self.__dict__.setdefault("_replay_senders", {})
            if q.history_id not in names:
                name = None
                history = getattr(self, "_history", None)
                try:
                    line = history.get(q.history_id) if history is not None else None
                    sender = line.sidecar.get("sender") if line is not None else None
                    name = sender.get("name") if isinstance(sender, dict) else None
                except Exception:  # noqa: BLE001
                    name = None
                names[q.history_id] = name
            return names[q.history_id]
        return self._sender_name(q.session_id)

    def _queue_view(self) -> list[dict[str, Any]]:
        """What waits, in the order it will play, as the control's queue tab
        lists it: the arbiter's own bands, never a copy kept beside them."""
        arbiter = getattr(self, "_priority_arbiter", None)
        if arbiter is None:
            return []
        out: list[dict[str, Any]] = []
        for q in arbiter.waiting():
            row: dict[str, Any] = {"id": q.queue_id, "text": self._queue_item_text(q)}
            stamp = rfc3339(float(q.enqueued_at or q.timestamp or 0.0))
            if stamp:
                row["time"] = stamp
            source = self._queue_item_source(q)
            if source:
                row["source"] = source
            out.append(row)
        return out

    def _queue_taken(self, removed: list[QueueItem], reason: str) -> None:
        """Lines he took out of the queue: not played, not on the missed list
        (he chose it). The ledger lets the same line id be sent again."""
        for q in removed:
            if q.line_id:
                self._line_ledger().dropped(q.line_id, reason)
        self._update_depth(self._tts_queue.qsize())

    def _queue_clear(self) -> int:
        arbiter = getattr(self, "_priority_arbiter", None)
        if arbiter is None:
            return 0
        removed = arbiter.remove_where(lambda q: q.payload is not None)
        self._queue_taken(removed, "cleared")
        _log(f"queue: cleared {len(removed)}")
        return len(removed)

    def _queue_remove(self, ids: list[str]) -> int:
        arbiter = getattr(self, "_priority_arbiter", None)
        wanted = {i for i in ids if isinstance(i, str) and i}
        if arbiter is None or not wanted:
            return 0
        removed = arbiter.remove_where(lambda q: q.queue_id in wanted and q.payload is not None)
        self._queue_taken(removed, "removed")
        _log(f"queue: removed {len(removed)} ({', '.join(q.queue_id for q in removed) or 'none waiting'})")
        return len(removed)

    def _queue_move(self, queue_id: str, to: int) -> Optional[int]:
        arbiter = getattr(self, "_priority_arbiter", None)
        if arbiter is None:
            return None
        moved = arbiter.move(queue_id, to)
        if moved is None:
            _log(f"queue: moved nothing ({queue_id} is not waiting)")
        else:
            _log(f"queue: moved {queue_id} to {moved}" + (f" (asked {to}; a line stays in its band)" if moved != to else ""))
        return moved

    def _begin_control_run(self) -> int:
        """A new play-from or play-all from the control: the replays still
        waiting from an earlier run are taken out of the queue, so a second
        press never stacks the day again behind the first (#m1030-0939-c4f9).
        Only a run's own replays go: the line playing now plays to its end,
        and a fresh line, a play-item and a missed line stay queued. Returns
        how many were taken out."""
        run = getattr(self, "_control_runs", 0) + 1
        self._control_runs = run
        self._control_run_open = run
        arbiter = getattr(self, "_priority_arbiter", None)
        if arbiter is None:
            return 0
        removed = arbiter.remove_where(lambda q: bool(q.control_run) and q.control_run != run and bool(q.history_id))
        if removed:
            self._update_depth(self._tts_queue.qsize())
        return len(removed)

    def _end_control_run(self) -> None:
        self._control_run_open = 0

    def _play_missed_ids(self, ids: list[str]) -> int:
        """Missed lines played through missed_speech's play, which takes each
        off the list once heard. A play ends his stop."""
        from missed_speech import play_ids

        self._resume_speech(play_set_aside=False)
        try:
            return play_ids(ids, missed=self._missed, client=_InProcessClient(self))
        except ConnectionError as exc:
            _log(f"control: missed lines not queued: {exc!r}")
            return 0

    # -- his stop (ADR-024) -----------------------------------------------------

    # -- one line id, one play (ADR-025) -------------------------------------------

    def _line_ledger(self) -> LineLedger:
        ledger = getattr(self, "_lines", None)
        if ledger is None:  # a service built without __init__ (tests)
            ledger = self._lines = LineLedger(None)
        return ledger

    def _queued_line_ids(self) -> set[str]:
        return {q.line_id for q in self._queued_items() if isinstance(q, QueueItem) and q.line_id}

    def _line_in_flight(self) -> Optional[dict[str, Any]]:
        """The ledger's record of the line the worker has in hand, when it has an id."""
        item = getattr(self, "_active_item", None)
        line_id = getattr(item, "line_id", "") if isinstance(item, QueueItem) else ""
        return self._line_ledger().answer(line_id) if line_id else None

    def _is_stopped(self) -> bool:
        return getattr(self, "_stopped_since", None) is not None

    def _stop_speech(self, by: str = "") -> None:
        """His stop: the line playing ends, the queue is cleared, and nothing
        plays until a play or resume. Lines that arrive meanwhile, from any
        caller, go on the missed list as `stopped`."""
        if not self._is_stopped():
            self._stopped_since = time.time()
            _write_stopped(self._stopped_since, by=by)
            _log("stopped" + (f" by {by}" if by else "") + ": nothing plays until a play or resume; lines that arrive go on the missed list")
        self._handle_p1_interrupt()
        # The barge-in clears only the narration bands. Replays of heard lines
        # (play-item, play-all, play-from) wait in the explicit band: his stop
        # takes them all out now; they are in the history already. A live line
        # still waiting is set aside on the missed list as `stopped` by the
        # worker, as ADR-024 says, and plays on his play.
        arbiter = getattr(self, "_priority_arbiter", None)
        if arbiter is not None:
            cleared = arbiter.remove_where(lambda q: bool(q.history_id))
            if cleared:
                self._update_depth(self._tts_queue.qsize())
                _log(f"stopped: cleared {len(cleared)} queued replays (heard lines, still in the history)")

    def _resume_speech(self, *, play_set_aside: bool = True) -> int:
        """Ends his stop. With play_set_aside, the lines that arrived while
        stopped are queued again, in order. Returns how many were queued;
        -1 when speech was not stopped."""
        since = getattr(self, "_stopped_since", None)
        if since is None:
            return -1
        self._stopped_since = None
        _write_stopped(None)
        if not play_set_aside:
            _log("resumed: his stop ended by a play")
            return 0
        missed = getattr(self, "_missed", None)
        ids: list[str] = []
        if missed is not None:
            try:
                ids = [m.id for m in missed.load() if m.reason == REASON_STOPPED and (m.missed_at or 0.0) >= since]
            except Exception as exc:  # noqa: BLE001
                _log(f"missed list read error: {exc!r}")
        queued = self._play_missed_ids(ids) if ids else 0
        _log(f"resumed: his stop ended; {queued} line(s) that arrived while stopped queued again")
        return queued

    def _set_aside_if_stopped(self, text: str, item: Optional[QueueItem]) -> bool:
        if not self._is_stopped():
            return False
        self._set_aside(text, item, reason=REASON_STOPPED)
        if not getattr(item, "history_id", ""):
            _log(f"missed: stopped, set aside: {(text or '')[:50]!r}")
        return True

    def _set_aside_queued(self, reason: str) -> int:
        """Every line still in the queue goes on the missed list (shutdown)."""
        n = 0
        while True:
            try:
                phase = self._tts_queue.get_nowait()
            except queue.Empty:
                break
            except Exception:  # noqa: BLE001 — a queue that cannot say is empty
                break
            if phase is None:
                continue
            arbiter = getattr(self, "_priority_arbiter", None)
            item = getattr(arbiter, "active_item", None) if arbiter else None
            if self._set_aside_item(phase, item, reason):
                n += 1
            try:
                self._tts_queue.task_done()
            except Exception:  # noqa: BLE001
                pass
        if n:
            _log(f"shutdown: {n} queued line(s) set aside on the missed list")
        return n

    def _set_aside_item(self, phase: Any, item: Any, reason: str) -> bool:
        """A queued phase, set aside without playing. False for what is not a line."""
        if isinstance(phase, QueueItem):
            item, phase = phase, phase.payload
        q_item = item if isinstance(item, QueueItem) else None
        if isinstance(phase, Path) or (isinstance(phase, str) and phase.endswith(".wav")):
            text = getattr(q_item, "line_text", "") or ""
        elif isinstance(phase, str):
            text = phase
        else:
            return False  # a phase still to be summarised: nothing to say yet
        if getattr(q_item, "history_id", ""):
            return False  # a replay of a heard line: it is in the history already
        self._set_aside(text, q_item, reason=reason)
        return True

    def _missed_add(self, payload: dict[str, Any]) -> dict[str, Any]:
        """`auto-speech missed add`: a caller puts a line it held or that was
        cut on the missed list, through the daemon, the one writer."""
        text = str(payload.get("text") or "").strip()
        if not text:
            return {"status": "error", "error_code": "INVALID_PAYLOAD", "message": "missed add: text is required"}
        source = str(payload.get("source") or "").strip() or None
        at = payload.get("at")
        try:
            queued_at = float(at) if at is not None else time.time()
        except (TypeError, ValueError):
            return {"status": "error", "error_code": "INVALID_PAYLOAD", "message": f"missed add: bad time {at!r}"}
        entry_id = str(payload.get("id") or "").strip() or new_id()
        # The speech line's own id (ADR-026): a line already heard is not
        # added, and one the narrator already holds is the same entry.
        speech_id = clean_line_id(payload.get("line_id"))
        origin_file = clean_origin_file(payload.get("origin_file")) or None
        if speech_id and self._line_ledger().is_done(speech_id):
            _log(f"missed: line {speech_id} was heard already; not added for {source or 'a caller'}")
            return {"status": "ok", "action": "missed_add", "duplicate": True, "line_id": speech_id,
                    "line": self._line_ledger().answer(speech_id)}
        missed = getattr(self, "_missed", None)
        if missed is None:
            return {"status": "error", "error_code": "NO_MISSED_LIST", "message": "missed add: no missed list"}
        try:
            held = missed.find_line(speech_id)
            if held is not None:
                _log(f"missed: line {speech_id} is on the list already; {source or 'a caller'}'s copy is the same entry")
                return {"status": "ok", "action": "missed_add", "id": held.id, "line_id": speech_id, "duplicate": True}
            stored = missed.miss(
                MissedLine(id=entry_id, text=text, queued_at=queued_at, source=source,
                           line_id=speech_id, origin_file=origin_file),
                reason=REASON_ADDED,
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error_code": "WRITE_FAILED", "message": f"missed add: {exc!r}"}
        _log(f"missed: added by {source or 'a caller'}: {text[:50]!r}" + (f" (line {speech_id})" if speech_id else ""))
        self._control_call("list_changed")
        reply = {"status": "ok", "action": "missed_add", "id": stored.id}
        if speech_id:
            reply["line_id"] = speech_id
        return reply

    def _marker_live(self, marker: Path, session_id: str) -> bool:
        """An enrolment marker counts only while its session is alive (ADR-024)."""
        liveness = getattr(self, "_liveness", None)
        if liveness is None:
            return True
        try:
            alive = liveness.alive(marker, session_id)
        except Exception:  # noqa: BLE001 — a check that cannot say keeps the marker
            return True
        if not alive:
            logged = self.__dict__.setdefault("_stale_logged", set())
            if (marker.parent.name, session_id) not in logged:
                logged.add((marker.parent.name, session_id))
                _log(f"session {session_id}: its marker {marker.parent.name} is stale (no live session); ignored")
        return alive

    def _hold_stands(self) -> bool:
        """True while a meeting or another app's sound holds speech; the
        player never pops up then. No gate: nothing holds."""
        gate = getattr(self, "_yield_gate", None)
        if gate is None:
            return False
        return bool(getattr(gate.assess(), "hold", False))

    def _sender_name(self, session_id: Optional[str]) -> Optional[str]:
        speaker = getattr(self, "_speaker", None)
        if speaker is None or not session_id:
            return None
        try:
            return speaker.name_for(session_id)
        except Exception:  # noqa: BLE001
            return None

    def _heard(self, path: Path, item: Optional[QueueItem], *, text: str, session_id: Optional[str] = None) -> None:
        """The line was heard: export it to the history, then hand it to the
        player launcher. Never raises; a failure is logged and speech goes on."""
        history = getattr(self, "_history", None)
        replayed = getattr(item, "history_id", "") if item is not None else ""
        if history is None or replayed:
            # A history line played again from the control: already in the
            # history, so not exported twice (ADR-022).
            line = None
            if history is not None and replayed:
                try:
                    line = history.get(replayed)
                except Exception:  # noqa: BLE001
                    line = None
            self._control_call("ended", line)
            return
        sid = session_id or (getattr(item, "session_id", None) if item is not None else None)
        priority = getattr(item, "priority", None) if item is not None else getattr(self, "_active_priority", None)
        profile = getattr(self, "_profile", None)
        voice_id = (getattr(item, "voice_id", None) if item is not None else None) or getattr(profile, "voice_id", "") or ""
        speed = getattr(item, "speed", None) if item is not None else None
        if speed is None:
            speed = getattr(profile, "speed", 1.0) if profile is not None else 1.0
        try:
            line = history.export(
                path,
                text=text,
                session_id=sid,
                sender=self._sender_name(sid),
                priority=int(priority) if priority is not None else None,
                voice_id=str(voice_id),
                speed=float(speed),
                source_hash=getattr(item, "source_hash", None) if item is not None else None,
                origin_file=getattr(item, "origin_file", None) if item is not None else None,
            )
        except Exception as exc:  # noqa: BLE001
            _log(f"history export error: {exc!r}")
            self._control_call("ended", None)
            return
        self._control_call("ended", line)
        if line is None:
            return
        player = getattr(self, "_player", None)
        if player is None:
            return
        try:
            player.after_line(line)
        except Exception as exc:  # noqa: BLE001
            _log(f"player launch error: {exc!r}")

    # -- speaker names: "<desk> here," only when who is talking changes -------

    def _speaker_status(self) -> Optional[dict[str, Any]]:
        speaker = getattr(self, "_speaker", None)
        if speaker is None:
            return None
        try:
            return speaker.status()
        except Exception:  # noqa: BLE001
            return None

    def _speaker_plan(self, text: str, session_id: Optional[str]) -> Optional[Plan]:
        speaker = getattr(self, "_speaker", None)
        if speaker is None:
            return None
        try:
            return speaker.plan(text, session_id)
        except Exception as exc:  # noqa: BLE001
            _log(f"speaker plan error: {exc!r}")
            return None

    def _speaker_spoken(self, session_id: Optional[str]) -> None:
        speaker = getattr(self, "_speaker", None)
        if speaker is None or not session_id:
            return
        try:
            speaker.spoken(session_id)
        except Exception as exc:  # noqa: BLE001
            _log(f"speaker bookkeeping error: {exc!r}")

    def _speaker_clip(self, plan: Plan) -> Optional[Path]:
        """The "<name> here." clip as its own short wav, synthesized once per
        name under the state dir; None when it cannot be made."""
        text = plan.clip_text
        if not text:
            return None
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "desk"
        clip_dir = state_path("auto-speech-speaker")
        clip = Path(clip_dir) / f"{slug}.wav"
        try:
            if clip.is_file() and clip.stat().st_size > 0:
                return clip
            clip.parent.mkdir(parents=True, exist_ok=True)
            synth = getattr(getattr(self, "_tts_executor", None), "synth", None) or getattr(self, "_synth", None)
            profile = getattr(self, "_profile", None)
            if synth is None or profile is None:
                return None
            tmp = clip.with_suffix(".partial.wav")
            ok = synth.synthesize_one(text, profile, tmp)
            if ok and tmp.is_file() and tmp.stat().st_size > 0:
                os.replace(tmp, clip)
                return clip
            tmp.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            _log(f"speaker clip error: {exc!r}")
        return None

    def _play_cached(self, path: Path, item: Optional[QueueItem], *, text: str = "", plan: Optional[Plan] = None) -> bool:
        """Cached audio cannot take "<name> here," in front of its words, so
        when the desk is to be named the clip plays first, then the line."""
        if self._set_aside_if_stopped(text, item):
            return False
        sid = getattr(item, "session_id", None) if item is not None else None
        if plan is None:
            plan = self._speaker_plan(text, sid)
        if plan is not None and plan.prefix:
            clip = self._speaker_clip(plan)
            if clip is not None:
                hold = bool(getattr(item, "hold", True)) if item is not None else True
                try:
                    self._sink_play(clip, hold=hold, already_waited=0.0, on_hold=None)
                except Exception as exc:  # noqa: BLE001
                    _log(f"speaker clip playback error: {exc!r}")
                    return False
        heard = self._play_item(path, item, text=text, session_id=sid)
        if heard:
            self._speaker_spoken(sid)
        return heard

    def _speak(
        self,
        line: str,
        source_hash: Optional[str] = None,
        *,
        voice_id: Optional[str] = None,
        speed: Optional[float] = None,
        item: Optional[QueueItem] = None,
        session_id: Optional[str] = None,
    ) -> None:
        line = line.strip()
        if not line:
            return

        if item is not None:
            stale, age = self._stale_backlog(item)
            if stale:
                self._set_aside(line, item, reason=REASON_STALE, waited=age)
                _log(f"missed: stale backlog ({age:.0f}s old when its turn came), set aside: {line[:50]!r}")
                return

        # His stop holds every line, from any caller, before synthesis.
        if self._set_aside_if_stopped(line, item):
            return

        # In a meeting the line is set aside before synthesis: no TTS call,
        # no stall. An explicit replay (`missed --play`, hold=False) plays.
        if item is None or bool(getattr(item, "hold", True)):
            meeting = self._meeting_now()
            if meeting:
                self._set_aside(line, item, reason=REASON_MEETING, held_for=meeting)
                _log(f"missed: in a meeting ({', '.join(meeting)}), set aside without synthesis: {line[:50]!r}")
                return

        # Name the desk only when the speaker changes (ADR-016). A cache hit
        # keeps its words and gets the name as a clip in front instead.
        sid = session_id or (getattr(item, "session_id", None) if item is not None else None)
        plan = self._speaker_plan(line, sid)
        spoken_line = plan.text if plan is not None else line

        _log(f"speak: {spoken_line} (source_hash={source_hash})")
        self._ensure_tts_initialized()
        if self._sink is None or self._profile is None:
            _log(f"tts engine not available; dropped narration: {line[:50]!r}")
            return

        profile = self._profile
        if profile is not None and (
            (voice_id and voice_id != profile.voice_id)
            or (speed is not None and speed != profile.speed)
        ):
            profile = VoiceProfile(
                voice_id=voice_id or profile.voice_id,
                speed=speed if speed is not None else profile.speed,
                chars_per_second=profile.chars_per_second,
                calibrated_at=profile.calibrated_at,
                calibration_source_chars=profile.calibration_source_chars,
            )

        # Check cache if valid source_hash provided
        if source_hash and len(source_hash) == 64 and all(c in "0123456789abcdef" for c in source_hash):
            try:
                hit = self._cache.lookup(source_hash)
                if hit is not None:
                    cached_wav = hit[0]
                    _log(f"cache hit for {source_hash[:16]}; playing {cached_wav}")
                    state_lock = getattr(self, "_state_lock", None)
                    if state_lock:
                        with state_lock:
                            if getattr(self, "_interrupted", False) or getattr(self, "_preempted", False):
                                _log("playback aborted under state_lock: interrupted or preempted")
                                return
                            self._engine_state = "PLAYING"
                    else:
                        self._engine_state = "PLAYING"
                    try:
                        self._play_cached(cached_wav, item, text=line, plan=plan)
                    finally:
                        if state_lock:
                            with state_lock:
                                if self._engine_state == "PLAYING":
                                    self._engine_state = "IDLE"
                        else:
                            if getattr(self, "_engine_state", "IDLE") == "PLAYING":
                                self._engine_state = "IDLE"
                    return
            except Exception as exc:
                _log(f"cache lookup error for {source_hash}: {exc}")

        try:
            SYNTH_DIR.mkdir(parents=True, exist_ok=True)
            synth_dir: Optional[Path] = SYNTH_DIR
        except OSError as exc:
            _log(f"synth dir {SYNTH_DIR} unavailable ({exc!r}); using the system temp dir")
            synth_dir = None
        with tempfile.NamedTemporaryFile(prefix="narrator_", suffix=".wav", delete=False, dir=synth_dir) as f:
            temp_wav = Path(f.name)

        try:
            # Pre-synthesis check
            if getattr(self, "_interrupted", False) or getattr(self, "_preempted", False):
                _log("synthesis skipped: interrupted or preempted prior to start")
                return

            budget = self._synthesis_timeout(spoken_line, profile)
            self._progress("synthesizing", expect=budget or 0.0)
            if item is not None and item.line_id:
                self._line_ledger().synth_started(item.line_id)
            if hasattr(self, "_tts_executor") and self._tts_executor is not None:
                synth = getattr(self._tts_executor, "synth", getattr(self, "_synth", None))
                if synth is not None and hasattr(self._tts_executor, "submit"):
                    try:
                        has_audio = self._submit_synthesis(synth, spoken_line, profile, temp_wav, budget)
                    except SynthesisTimeout as exc:
                        if getattr(exc, "final", False):
                            # The engine killed the hung process and retried
                            # once already: the line is set aside, the queue moves on.
                            self._set_aside(line, item, reason=REASON_TIMEOUT)
                            _log(f"missed: {exc}; the hung synthesis was killed, set aside: {line[:50]!r}")
                            return
                        # Queued again to synthesize once the load falls (ADR-019);
                        # set aside for him to replay only past its retries.
                        if self._retry(line, item, reason=str(exc)):
                            _log(f"synthesis timed out ({exc}); the line is queued again: {line[:50]!r}")
                            return
                        self._set_aside(line, item, reason=REASON_TIMEOUT)
                        _log(f"missed: {exc}; synthesis abandoned, set aside: {line[:50]!r}")
                        return
                elif synth is not None:
                    has_audio = synth.synthesize_one(spoken_line, profile, temp_wav)
                else:
                    has_audio = False
            elif hasattr(self, "_synth") and self._synth is not None:
                has_audio = self._synth.synthesize_one(spoken_line, profile, temp_wav)
            else:
                _log(f"no synthesizer available for: {line[:50]!r}")
                return

            # Post-synthesis check
            if getattr(self, "_interrupted", False) or getattr(self, "_preempted", False):
                _log("playback skipped: interrupted or preempted during synthesis")
                return

            if has_audio and temp_wav.exists() and temp_wav.stat().st_size > 0:
                play_target = temp_wav
                if source_hash and len(source_hash) == 64 and all(c in "0123456789abcdef" for c in source_hash):
                    try:
                        duration = _wav_duration_seconds(temp_wav)
                        cps = (
                            (len(line) / duration)
                            if duration > 0
                            else (profile.chars_per_second if profile else FALLBACK_CHARS_PER_SEC)
                        )
                        entry = CacheEntry(
                            source_hash=source_hash,
                            voice_id=profile.voice_id if profile else DEFAULT_VOICE_ID,
                            speed=profile.speed if profile else DEFAULT_SPEED,
                            char_count=len(line),
                            duration_seconds=duration,
                            created_at=datetime.now(timezone.utc)
                            .isoformat(timespec="seconds")
                            .replace("+00:00", "Z"),
                            chars_per_second_at_creation=cps,
                        )
                        promoted_wav = self._cache.promote(source_hash, temp_wav, entry)
                        play_target = promoted_wav
                        _log(f"promoted to cache: {promoted_wav}")
                    except CachePromotionError as exc:
                        _log(f"cache promotion error: {exc}; falling back to staging wav")
                        play_target = temp_wav
                    except Exception as exc:
                        _log(f"unexpected cache promotion error: {exc}; falling back to staging wav")
                        play_target = temp_wav

                state_lock = getattr(self, "_state_lock", None)
                if state_lock:
                    with state_lock:
                        if getattr(self, "_interrupted", False) or getattr(self, "_preempted", False):
                            _log("playback aborted under state_lock: interrupted or preempted")
                            return
                        self._engine_state = "PLAYING"
                else:
                    self._engine_state = "PLAYING"

                _log(f"playing audio ({play_target.stat().st_size} bytes)...")
                if self._play_item(play_target, item, text=spoken_line, session_id=sid):
                    self._speaker_spoken(sid)
            else:
                _log(f"no speakable audio generated for: {line[:50]!r}")
        except Exception as exc:
            _log(f"speak error: {exc!r}")
        finally:
            state_lock = getattr(self, "_state_lock", None)
            if state_lock:
                with state_lock:
                    if self._engine_state == "PLAYING":
                        self._engine_state = "IDLE"
            else:
                if getattr(self, "_engine_state", "IDLE") == "PLAYING":
                    self._engine_state = "IDLE"
            temp_wav.unlink(missing_ok=True)
            temp_wav.with_suffix(temp_wav.suffix + ".partial").unlink(missing_ok=True)
            for frag in temp_wav.parent.glob(f"{temp_wav.stem}*"):
                frag.unlink(missing_ok=True)


def _read_stopped() -> Optional[float]:
    """Since when he stopped speech, kept across a restart; None: not stopped."""
    try:
        data = json.loads(STOPPED_FILE.read_text(encoding="utf-8"))
        since = data.get("since") if isinstance(data, dict) else None
        return float(since) if since is not None else None
    except (OSError, ValueError, TypeError):
        return None


def _write_stopped(since: Optional[float], by: str = "") -> None:
    try:
        if since is None:
            STOPPED_FILE.unlink(missing_ok=True)
            return
        STOPPED_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STOPPED_FILE.with_name(STOPPED_FILE.name + ".tmp")
        rec: dict[str, Any] = {"since": since}
        if by:
            rec["by"] = by   # who stopped it, for the doctor and the desk
        tmp.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        os.replace(tmp, STOPPED_FILE)
    except OSError as exc:
        _log(f"stopped state not kept: {exc!r}")


_STALE_MARKER_DAYS = 30


def _sweep_stale_session_markers() -> None:
    """Remove per-session marker files older than _STALE_MARKER_DAYS in
    both narrate-sessions and autoplay-sessions dirs. Each new Claude
    Code session creates a marker if its user opts in; they accumulate
    over time without this. 30-day window is conservative — sessions
    that old are almost certainly gone."""
    cutoff = time.time() - (_STALE_MARKER_DAYS * 86400)
    for sub in (
        "auto-speech-narrate-sessions",
        "auto-speech-autoplay-enabled",
        # Pre-inversion opt-out markers. No longer consulted by anything;
        # swept so they age out instead of lingering forever.
        "auto-speech-autoplay-sessions",
    ):
        d = Path.home() / ".claude" / sub
        if not d.is_dir():
            continue
        removed = 0
        for marker in d.iterdir():
            try:
                if marker.is_file() and marker.stat().st_mtime < cutoff:
                    marker.unlink()
                    removed += 1
            except OSError:
                pass
        if removed:
            _log(f"swept {removed} stale marker(s) from {d}")


def main() -> int:
    existing = _existing_pid()
    if existing is not None:
        print(f"narrator daemon already running (pid={existing})", file=sys.stderr)
        return 1

    svc = NarratorService()
    rc = svc.run()
    # A worker abandoned inside a hung synthesis would hold the interpreter's
    # exit for as long as it hangs (2026-10-23: 18 h as a husk with no socket
    # and no pid file). Give threads a moment, then leave without them.
    deadline = time.monotonic() + 5.0
    while _stuck_threads() and time.monotonic() < deadline:
        time.sleep(0.1)
    stuck = _stuck_threads()
    if stuck:
        _log(f"exit: {len(stuck)} thread(s) still busy ({', '.join(t.name for t in stuck)}); leaving without them")
        logging.shutdown()
        os._exit(rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
