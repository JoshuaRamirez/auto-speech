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
import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Daemon runs a UNIX domain socket server (socket.AF_UNIX) via unix_ipc_server._DaemonSocketServer
from auto_speech_log import get_logger
from cache_entry import CacheEntry
from cache_store import CachePromotionError, CacheStore
from config_constants import DEFAULT_SPEED, DEFAULT_VOICE_ID, FALLBACK_CHARS_PER_SEC
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
from native_audio_sink import NativeAudioSink
from priority_arbiter import Priority, PriorityArbiter, QueueItem, QueueProxyFacade
from resilient_synthesizer import ResilientSynthesizer
from tts_engine import TTSEngine
from tts_executor import TTSExecutor
from unix_ipc_server import _DaemonSocketServer
from voice_profile import VoiceProfile
from voice_profile_store import VoiceProfileStore

EVENTS_LOG = Path("/tmp/auto-speech-narrator-events.jsonl")
PID_FILE = Path("/tmp/auto-speech-narrator-daemon.pid")
LOG_FILE = Path("/tmp/auto-speech-narrator-daemon.log")
DEPTH_FILE = Path("/tmp/auto-speech-narration-depth")
WATERMARK_FILE = Path("/tmp/auto-speech-narrator-daemon.watermark")
DEFAULT_SOCKET_PATH = Path("/tmp/auto-speech-daemon.sock")
SOCKET_FILE = Path(os.environ.get("AUTO_SPEECH_DAEMON_SOCK", str(DEFAULT_SOCKET_PATH)))

POLL_INTERVAL_S = 0.25

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
    except Exception:  # noqa: BLE001 — unreadable wav reports zero duration
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


def _existing_pid() -> int | None:
    """The pid of a LIVE instance of this daemon, or None when the pid file
    is absent, unreadable, points at a dead process, or — under PID reuse —
    points at a process that is not our daemon (and is thus reclaimable)."""
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _pid_is_our_daemon(pid) else None


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
    ) -> None:
        self._config = config if config is not None else load_config()
        self._classifier = PhaseClassifier(silence_seconds=0.5, max_events_per_phase=1)
        # Bounded so a burst of phases can't grow the queue without limit
        # when the TTS worker (which blocks on playback) lags. See
        # _enqueue_phase for the drop-oldest backpressure policy.
        self._max_queue = int(self._config.get("max_queue_depth", 32))
        self._priority_arbiter = PriorityArbiter(maxsize=self._max_queue)
        self.__dict__["_queue_proxy"] = QueueProxyFacade(self._priority_arbiter, maxsize=self._max_queue)
        self._dropped_phases = 0
        self._state_lock = threading.Lock()
        self._engine_state = "IDLE"
        self._active_priority: int | None = None
        self._active_item: QueueItem | None = None
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

        self._sink: NativeAudioSink = sink if sink is not None else NativeAudioSink()
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
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)
        _log(f"started pid={os.getpid()} provider={self._config['provider']}")
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
            tts_thread.join(timeout=5.0)
            try:
                PID_FILE.unlink()
            except OSError:
                pass
            self._fsm.transition(NOT_RUNNING)  # *_SHUTDOWN → NOT_RUNNING
            _log("shutdown")
        return 0

    def _on_signal(self, signum, frame):
        _log(f"signal {signum} → shutting down")
        if self._fsm.can(SIGNAL_SHUTDOWN):
            self._fsm.transition(SIGNAL_SHUTDOWN)  # RUNNING → SIGNAL_SHUTDOWN
        self._stop.set()
        if hasattr(self, "_sink") and self._sink is not None:
            self._sink.interrupt()

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
            except Exception as exc:  # noqa: BLE001 — shutdown must finish
                _log(f"error shutting down socket server: {exc}")
            try:
                server.server_close()
            except Exception as exc:  # noqa: BLE001 — close must finish
                _log(f"error closing socket server: {exc}")

        if self._socket_thread is not None:
            if self._socket_thread.is_alive():
                self._socket_thread.join(timeout=2.0)
            self._socket_thread = None

        self._cleanup_socket_file()
        if self._atexit_registered:
            try:
                atexit.unregister(self._cleanup_socket_file)
            except Exception as exc:  # noqa: BLE001 — atexit cleanup must not raise
                _log(f"error unregistering socket cleanup: {exc}")
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
        """Dispatches structured JSON socket requests (RFC §4.1)."""
        action = payload.get("action")
        if action == "speak":
            text = str(payload.get("text", "")).strip()
            if not text:
                return {"status": "ok", "message": "empty text"}

            priority = int(payload.get("priority", Priority.EXPLICIT_MCP))
            source_hash = payload.get("source_hash")
            if source_hash:
                source_hash = str(source_hash).strip().lower()
            session_id = payload.get("session_id")
            voice_id = str(payload.get("voice_id", DEFAULT_VOICE_ID))
            speed = float(payload.get("speed", DEFAULT_SPEED))

            cached_wav: Path | None = None
            if source_hash and len(source_hash) == 64 and all(c in "0123456789abcdef" for c in source_hash):
                try:
                    hit = self._cache.lookup(source_hash)
                    if hit is not None:
                        cached_wav = hit[0]
                except Exception:  # noqa: BLE001 — cache lookup failure leaves no wav
                    cached_wav = None

            if cached_wav is not None:
                q_item = QueueItem(
                    priority=priority,
                    payload=str(cached_wav),
                    source_hash=source_hash,
                    session_id=session_id,
                    voice_id=voice_id,
                    speed=speed,
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
            return {
                "status": "queued",
                "action": "speak",
                "cache_hit": cache_hit,
                "queue_depth": self._tts_queue.qsize(),
            }

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
            except Exception:  # noqa: BLE001 — cache lookup failure is a miss
                hit = None

            if hit is None:
                return {
                    "status": "error",
                    "error_code": "CACHE_MISS",
                    "message": f"Cache miss for {source_hash}",
                }

            wav_path, _entry = hit
            priority = int(payload.get("priority", Priority.EXPLICIT_MCP))
            session_id = payload.get("session_id")
            q_item = QueueItem(
                priority=priority,
                payload=str(wav_path),
                source_hash=source_hash,
                session_id=session_id,
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
            self._handle_p1_interrupt()
            return {"status": "ok", "action": "interrupt"}

        elif action == "status":
            depths = self._priority_arbiter.depths() if hasattr(self, "_priority_arbiter") else {}
            return {
                "status": "ok",
                "daemon_state": self._engine_state,
                "active_priority": self._active_priority,
                "queue_depths": depths,
                "total_dropped_phases": self._dropped_phases,
                "uptime_seconds": round(time.time() - getattr(self, "_start_time", time.time()), 2),
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
            except Exception as exc:  # noqa: BLE001 — interrupt must not raise
                _log(f"sink interrupt error: {exc}")

        if state_lock:
            with state_lock:
                self._engine_state = "PURGING"
        else:
            self._engine_state = "PURGING"

        purged = 0
        if hasattr(self, "_priority_arbiter"):
            purged = self._priority_arbiter.purge_lower_queues()
            self._dropped_phases += purged

        self._update_depth(self._tts_queue.qsize())

        if state_lock:
            with state_lock:
                self._engine_state = "IDLE"
        else:
            self._engine_state = "IDLE"

        _log(f"P1 User Barge-in completed: purged {purged} lower items")

    def _handle_p2_preemption(self, q_item: QueueItem) -> None:
        """Handles Priority 2 Explicit MCP Preemption: interrupts active P3/P4 without purging."""
        state_lock = getattr(self, "_state_lock", None)
        preempted = False
        active_item = None

        if state_lock:
            with state_lock:
                if self._engine_state in ("PLAYING", "SYNTHESIZING") and self._active_priority in (
                    Priority.AUTOPLAY,
                    Priority.TOOL_NARRATION,
                ):
                    preempted = True
                    active_item = self._active_item
                    self._active_item = None
                    self._active_priority = None
                    self._preempted = True
                    self._engine_state = "PREEMPTING"
        else:
            if getattr(self, "_engine_state", "IDLE") in ("PLAYING", "SYNTHESIZING") and getattr(
                self, "_active_priority", None
            ) in (Priority.AUTOPLAY, Priority.TOOL_NARRATION):
                preempted = True
                active_item = getattr(self, "_active_item", None)
                self._active_item = None
                self._active_priority = None
                self._preempted = True
                self._engine_state = "PREEMPTING"

        if preempted:
            if hasattr(self, "_sink") and self._sink is not None:
                try:
                    self._sink.interrupt()
                except Exception as exc:  # noqa: BLE001 — preemption must not raise
                    _log(f"sink interrupt during preemption error: {exc}")

            if active_item is not None and hasattr(self, "_priority_arbiter"):
                self._priority_arbiter.requeue_at_head(active_item)

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

            # Idle shutdown
            if time.time() - self._last_event_ts > self._idle_shutdown:
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
                        except Exception:  # noqa: BLE001, S110 — skip malformed transcript lines
                            pass
        except Exception as e:  # noqa: BLE001 — cron check must not raise
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

            # Stop event → flush in-flight phase but SUPPRESS narration.
            # The end-of-turn autoplay owns the final read; we don't want
            # the narrator's post-Stop flush competing with it (and
            # killing autoplay's mpv via _kill_prior_session).
            event_type = ev.get("event", "")
            if event_type == "UserPromptSubmit":
                if self._is_cron_tick(ev.get("payload", {}).get("transcriptPath")):
                    _log("Skipping UserPromptSubmit narration for background cron tick.")
                    continue

                # Immediately interrupt any currently playing audio and purge lower queues!
                self._handle_p1_interrupt()
                try:
                    global_pid = int(Path("/tmp/auto-speech-mpv.pid").read_text().strip())
                    os.kill(global_pid, signal.SIGTERM)
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
                except Exception as e:  # noqa: BLE001 — start-words failure must not stop the loop
                    _log(f"Failed to generate start words: {e}")
                continue

            if event_type == "Stop":
                self._classifier.flush()
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
                    if ap_marker.exists():
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

    def _get_summarizer(self) -> Summarizer:
        with self._summarizer_lock:
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
        _log("tts_worker thread started")
        self._ensure_tts_initialized()
        while True:
            state_lock = getattr(self, "_state_lock", None)
            if state_lock:
                with state_lock:
                    self._engine_state = "IDLE"
                    self._active_priority = None
                    self._active_item = None

            phase = self._tts_queue.get()
            if phase is None:
                _log("tts_worker received sentinel; stopping")
                return

            arbiter = getattr(self, "_priority_arbiter", None)
            item_p = getattr(arbiter, "active_priority", Priority.TOOL_NARRATION) if arbiter else Priority.TOOL_NARRATION
            item_obj = getattr(arbiter, "active_item", None) if arbiter else None

            if isinstance(phase, QueueItem):
                item_p = phase.priority
                item_obj = phase
                phase = phase.payload

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
                            self._sink.play(phase)
                        except Exception as exc:  # noqa: BLE001
                            _log(f"play_cache error: {exc!r}")
                elif isinstance(phase, str):
                    s_hash = getattr(item_obj, "source_hash", None)
                    v_id = getattr(item_obj, "voice_id", None)
                    spd = getattr(item_obj, "speed", None)
                    self._speak(phase, source_hash=s_hash, voice_id=v_id, speed=spd)
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
                                self._speak(words)
                else:
                    _log(f"Phase received in worker: {phase}")
                    summ = self._get_summarizer()
                    line = summ.summarize(phase)
                    _log(f"Summarizer generated: {line}")
                    if line:
                        self._speak(line)
            except Exception as exc:  # noqa: BLE001 — keep the worker loop alive
                _log(f"tts_worker error: {exc!r}")
            finally:
                is_preempted = getattr(self, "_preempted", False)
                # When preempted, active item was re-queued; do not decrement _unfinished_tasks
                if not is_preempted:
                    self._tts_queue.task_done()
                self._update_depth(self._tts_queue.qsize())
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

    def _speak(
        self,
        line: str,
        source_hash: str | None = None,
        *,
        voice_id: str | None = None,
        speed: float | None = None,
    ) -> None:
        line = line.strip()
        if not line:
            return

        _log(f"speak: {line} (source_hash={source_hash})")
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
                        self._sink.play(cached_wav)
                    finally:
                        if state_lock:
                            with state_lock:
                                if self._engine_state == "PLAYING":
                                    self._engine_state = "IDLE"
                        else:
                            if getattr(self, "_engine_state", "IDLE") == "PLAYING":
                                self._engine_state = "IDLE"
                    return
            except Exception as exc:  # noqa: BLE001 — cache lookup error falls through to synth
                _log(f"cache lookup error for {source_hash}: {exc}")

        with tempfile.NamedTemporaryFile(prefix="narrator_", suffix=".wav", delete=False) as f:
            temp_wav = Path(f.name)

        try:
            # Pre-synthesis check
            if getattr(self, "_interrupted", False) or getattr(self, "_preempted", False):
                _log("synthesis skipped: interrupted or preempted prior to start")
                return

            if hasattr(self, "_tts_executor") and self._tts_executor is not None:
                synth = getattr(self._tts_executor, "synth", getattr(self, "_synth", None))
                if synth is not None and hasattr(self._tts_executor, "submit"):
                    has_audio = self._tts_executor.submit(
                        synth.synthesize_one, line, profile, temp_wav
                    )
                elif synth is not None:
                    has_audio = synth.synthesize_one(line, profile, temp_wav)
                else:
                    has_audio = False
            elif hasattr(self, "_synth") and self._synth is not None:
                has_audio = self._synth.synthesize_one(line, profile, temp_wav)
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
                            created_at=datetime.now(UTC)
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
                    except Exception as exc:  # noqa: BLE001 — promotion failure uses the staging wav
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
                self._sink.play(play_target)
            else:
                _log(f"no speakable audio generated for: {line[:50]!r}")
        except Exception as exc:  # noqa: BLE001 — speak failure must not kill the worker
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
    return svc.run()


if __name__ == "__main__":
    sys.exit(main())
