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
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from auto_speech_log import get_logger
from config_constants import DEFAULT_SPEED, DEFAULT_VOICE_ID, FALLBACK_CHARS_PER_SEC
from native_audio_sink import NativeAudioSink
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
from resilient_synthesizer import ResilientSynthesizer
from tts_engine import TTSEngine
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


def _pid_cmdline(pid: int) -> str:
    """Best-effort command line of `pid` via `ps`; '' on any error."""
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2,
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


class _DaemonRequestHandler(socketserver.BaseRequestHandler):
    """Handles incoming client requests on the UNIX domain stream socket."""

    server: _DaemonSocketServer

    def handle(self) -> None:
        chunks: list[bytes] = []
        while True:
            try:
                data = self.request.recv(4096)
                if not data:
                    break
                chunks.append(data)
            except (ConnectionResetError, BrokenPipeError, OSError):
                break

        if not chunks:
            return

        try:
            text = b"".join(chunks).decode("utf-8", errors="replace")
        except Exception:
            return

        cleaned = text.strip()
        if not cleaned:
            return

        service: NarratorService | None = getattr(self.server, "service", None)
        if service is not None:
            service.enqueue_text(cleaned)

        try:
            self.request.sendall(b"OK\n")
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass


class _DaemonSocketServer(socketserver.ThreadingUnixStreamServer):
    """Multi-threaded UNIX domain stream socket server for daemon IPC."""

    address_family = socket.AF_UNIX
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: str | Path,
        RequestHandlerClass: type[socketserver.BaseRequestHandler],
        service: NarratorService,
    ) -> None:
        self.service = service
        super().__init__(str(server_address), RequestHandlerClass)

    def server_bind(self) -> None:
        try:
            path = Path(self.server_address)
            if path.exists() or path.is_symlink():
                path.unlink(missing_ok=True)
        except OSError:
            pass
        super().server_bind()


class NarratorService:
    def __init__(
        self,
        *,
        sink: NativeAudioSink | None = None,
        engine: TTSEngine | None = None,
        synth: ResilientSynthesizer | None = None,
        profile: VoiceProfile | None = None,
        socket_path: Path | str | None = None,
    ) -> None:
        self._config = load_config()
        self._classifier = PhaseClassifier(silence_seconds=0.5, max_events_per_phase=1)
        # Bounded so a burst of phases can't grow the queue without limit
        # when the TTS worker (which blocks on playback) lags. See
        # _enqueue_phase for the drop-oldest backpressure policy.
        self._max_queue = int(self._config.get("max_queue_depth", 32))
        self._tts_queue: queue.Queue = queue.Queue(maxsize=self._max_queue)
        self._dropped_phases = 0
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
        self._engine: TTSEngine | None = engine
        self._synth: ResilientSynthesizer | None = synth
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

    def _on_signal(self, signum, frame):  # noqa: ARG002
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
                _DaemonRequestHandler,
                service=self,
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

            # Stop event → flush in-flight phase but SUPPRESS narration.
            # The end-of-turn autoplay owns the final read; we don't want
            # the narrator's post-Stop flush competing with it (and
            # killing autoplay's mpv via _kill_prior_session).
            event_type = ev.get("event", "")
            if event_type == "UserPromptSubmit":
                if self._is_cron_tick(ev.get("payload", {}).get("transcriptPath")):
                    _log("Skipping UserPromptSubmit narration for background cron tick.")
                    continue

                # Immediately interrupt any currently playing audio so the user isn't talked over!
                if hasattr(self, "_sink") and self._sink is not None:
                    self._sink.interrupt()

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
                            self._tts_queue.put(words)
                except Exception as e:
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

                history = ev.get("payload", {}).get("conversation_history", "")
                self._tts_queue.put(
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
            dropped = self._tts_queue.get_nowait()
            self._tts_queue.task_done()
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
        except Exception as exc:
            _log(f"eager-load failed (will retry lazily on first phase): {exc!r}")

    def _ensure_tts_initialized(self) -> None:
        """Initialize TTSEngine and ResilientSynthesizer on the worker thread.

        Honors Apple MLX single-thread stream affinity: MLX compute streams
        are per-thread, so the model must be loaded and invoked on the exact
        same thread (_tts_worker).
        """
        if self._synth is not None and self._profile is not None:
            return

        try:
            if self._profile is None:
                self._profile = _load_profile_or_fallback(self._config)

            if self._engine is None:
                model_id = self._config.get("tts_model", "mlx-community/Kokoro-82M-bf16")
                self._engine = TTSEngine(model_id=model_id)

            if self._synth is None:
                self._synth = ResilientSynthesizer(self._engine, log=_log)

            _log(f"loading tts engine on worker thread (model={self._engine.model_id})...")
            self._engine._ensure_loaded()
            _log("tts engine ready on worker thread")
        except Exception as exc:
            _log(f"error initializing tts engine on worker thread: {exc!r}")

    def _tts_worker(self) -> None:
        _log("tts_worker thread started")
        self._ensure_tts_initialized()
        while True:
            phase = self._tts_queue.get()
            if phase is None:
                _log("tts_worker received sentinel; stopping")
                return
            try:
                if isinstance(phase, str):
                    self._speak(phase)
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
            except Exception as exc:
                _log(f"tts_worker error: {exc!r}")
            finally:
                self._tts_queue.task_done()
                self._update_depth(self._tts_queue.qsize())

    def _speak(self, line: str) -> None:
        """Synthesize and play speech in-process on the _tts_worker thread.

        Replaces legacy external process sprawl and mpv duration sleep hacks.
        Uses ResilientSynthesizer to handle any Kokoro generation faults,
        plays synchronously via NativeAudioSink, and cleans up temporary WAV files.
        """
        line = line.strip()
        if not line:
            return

        _log(f"speak: {line}")
        self._ensure_tts_initialized()
        if self._synth is None or self._sink is None or self._profile is None:
            _log(f"tts engine not available; dropped narration: {line[:50]!r}")
            return

        with tempfile.NamedTemporaryFile(prefix="narrator_", suffix=".wav", delete=False) as f:
            temp_wav = Path(f.name)

        try:
            has_audio = self._synth.synthesize_one(line, self._profile, temp_wav)
            if has_audio and temp_wav.exists() and temp_wav.stat().st_size > 0:
                _log(f"playing audio ({temp_wav.stat().st_size} bytes)...")
                self._sink.play(temp_wav)
            else:
                _log(f"no speakable audio generated for: {line[:50]!r}")
        except Exception as exc:
            _log(f"speak error: {exc!r}")
        finally:
            temp_wav.unlink(missing_ok=True)
            temp_wav.with_suffix(temp_wav.suffix + ".partial").unlink(missing_ok=True)
            for frag in temp_wav.parent.glob(f"{temp_wav.stem}-*.wav"):
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
