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

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from auto_speech_log import get_logger
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

EVENTS_LOG = Path("/tmp/auto-speech-narrator-events.jsonl")
PID_FILE = Path("/tmp/auto-speech-narrator-daemon.pid")
LOG_FILE = Path("/tmp/auto-speech-narrator-daemon.log")
DEPTH_FILE = Path("/tmp/auto-speech-narration-depth")
WATERMARK_FILE = Path("/tmp/auto-speech-narrator-daemon.watermark")

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


def _speak_script() -> Path:
    return _project_root() / "plugin" / "scripts" / "shell" / "run_speak.sh"


class NarratorService:
    def __init__(self) -> None:
        self._config = load_config()
        self._classifier = PhaseClassifier(
            silence_seconds=self._config["silence_seconds"]
        )
        # Bounded so a burst of phases can't grow the queue without limit
        # when the TTS worker (which blocks on playback) lags. See
        # _enqueue_phase for the drop-oldest backpressure policy.
        self._max_queue = int(self._config.get("max_queue_depth", 32))
        self._tts_queue: queue.Queue[Phase | None] = queue.Queue(
            maxsize=self._max_queue
        )
        self._dropped_phases = 0
        self._summarizer: Summarizer | None = None
        self._summarizer_lock = threading.Lock()
        self._last_event_ts = time.time()
        self._idle_shutdown = float(self._config["idle_shutdown_seconds"])
        self._stop = threading.Event()
        # Records and guards the daemon lifecycle. Reflects reality; does
        # not replace the tail/queue/watermark logic.
        self._fsm = NarratorStateMachine()

    def run(self) -> int:
        self._fsm.transition(STARTING)  # NOT_RUNNING → STARTING
        PID_FILE.write_text(str(os.getpid()))
        self._fsm.transition(RUNNING)   # STARTING → RUNNING (pid file written)
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)
        _log(f"started pid={os.getpid()} provider={self._config['provider']}")
        self._update_depth(0)
        _sweep_stale_session_markers()

        # Eagerly load the summarizer at boot so the first phase doesn't
        # eat a 3-6 s MLX-load latency at speak time. Failures here
        # silently fall back to Mock via load_summarizer's downgrade
        # path (the user gets robotic narration but no daemon crash).
        eager_thread = threading.Thread(target=self._eager_load, daemon=True)
        eager_thread.start()

        tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
        tts_thread.start()

        try:
            self._tail_events()
        finally:
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
                        offset = size
                    self._process_chunk(chunk)
                    try:
                        WATERMARK_FILE.write_text(str(offset))
                    except OSError:
                        pass

            # Idle shutdown
            if time.time() - self._last_event_ts > self._idle_shutdown:
                _log(f"idle for >{self._idle_shutdown}s → shutting down")
                if self._fsm.can(IDLE_SHUTDOWN):
                    self._fsm.transition(IDLE_SHUTDOWN)  # RUNNING → IDLE_SHUTDOWN
                return

            self._stop.wait(POLL_INTERVAL_S)

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
            session_marker = (
                Path.home()
                / ".claude"
                / "auto-speech-narrate-sessions"
                / session_id
            )
            if not session_marker.exists():
                continue

            # Stop event → flush in-flight phase but SUPPRESS narration.
            # The end-of-turn autoplay owns the final read; we don't want
            # the narrator's post-Stop flush competing with it (and
            # killing autoplay's mpv via _kill_prior_session).
            event_type = ev.get("event", "")
            if event_type == "Stop":
                self._classifier.flush()  # discard, don't enqueue
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
            _log(
                f"skipping phase category={phase.category.value} "
                f"(suppressed)"
            )
            return
        min_events = int(self._config.get("min_events_per_phase", 1))
        if len(phase.events) < min_events:
            _log(
                f"skipping phase category={phase.category.value} "
                f"events={len(phase.events)} (< {min_events})"
            )
            return
        self._enqueue_phase(phase)
        self._update_depth(self._tts_queue.qsize())
        _log(
            f"enqueued phase={phase.category.value} "
            f"events={len(phase.events)} depth={self._tts_queue.qsize()}"
        )

    def _enqueue_phase(self, phase: Phase) -> None:
        """Bounded put with drop-oldest backpressure.

        The TTS worker blocks on playback-to-completion to preserve true
        FIFO, so a burst of phases can outrun it. Rather than let the
        queue grow without bound (a slow memory leak under sustained tool
        use), cap it at max_queue_depth and shed the OLDEST queued phase
        when full — stale narration is the least worth speaking — counting
        every drop so the loss is observable in the daemon log.

        Only the tail thread calls this; the TTS worker only removes from
        the queue. After shedding one item a slot is therefore guaranteed
        free, but the final put stays defensive for safety.
        """
        try:
            self._tts_queue.put_nowait(phase)
            return
        except queue.Full:
            pass
        try:
            dropped = self._tts_queue.get_nowait()
            self._tts_queue.task_done()
            self._dropped_phases += 1
            cat = getattr(getattr(dropped, "category", None), "value", "?")
            _log(
                f"queue full (max={self._max_queue}); dropped oldest "
                f"phase={cat} total_dropped={self._dropped_phases}"
            )
        except queue.Empty:
            pass
        try:
            self._tts_queue.put_nowait(phase)
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
                _log(f"summarizer loaded in {time.time() - t0:.1f}s: {type(self._summarizer).__name__}")
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

    def _tts_worker(self) -> None:
        while True:
            phase = self._tts_queue.get()
            if phase is None:
                return
            try:
                summ = self._get_summarizer()
                line = summ.summarize(phase)
                if line:
                    self._speak(line)
            except Exception as exc:
                _log(f"tts_worker error: {exc!r}")
            finally:
                self._tts_queue.task_done()
                self._update_depth(self._tts_queue.qsize())

    def _speak(self, line: str) -> None:
        """Block until the spoken WAV plays to completion so we get true
        FIFO. Two waits are needed:

          PRE-wait: any mpv that's currently playing — whether ours from
          a previous phase OR the end-of-turn autoplay's response read —
          must finish first. Otherwise speak.py's MpvController.start()
          kills it (newest-wins is what we DON'T want here). This is
          the autoplay-interruption fix.

          POST-wait: wait for our own mpv to finish so the next phase's
          speak doesn't kill it.

        Both use the same SessionDir.is_mpv_running() probe; the
        pre-wait doesn't sleep first since we want to detect the
        currently-running mpv immediately.
        """
        # PRE: don't interrupt an in-progress play.
        self._wait_mpv_idle(max_seconds=120.0, initial_sleep=0.0)
        _log(f"speak: {line[:80]}")
        speak = _speak_script()
        # Pass SUPPRESS_HOOKS=1 through to the TTS subprocess as a belt
        # for the suspenders. Today's speak.py path doesn't spawn any
        # Claude Code hooks (it's pure local TTS + mpv), so this is
        # purely defensive — if a future TTS engine ever invokes claude
        # internally, recursion is pre-empted. See cli_rewrite's use of
        # the same env var (commit 5cff691).
        env = {**os.environ, "AUTO_SPEECH_SUPPRESS_HOOKS": "1"}
        proc = subprocess.run(
            [str(speak)],
            input=line.encode("utf-8"),
            capture_output=True,
            timeout=120,
            env=env,
        )
        if proc.returncode != 0:
            _log(f"speak rc={proc.returncode} stderr={proc.stderr.decode(errors='replace')[:200]}")
            return
        # POST: wait for our mpv to finish before pulling the next phase.
        self._wait_mpv_idle(max_seconds=120.0, initial_sleep=0.3)

    def _wait_mpv_idle(self, max_seconds: float, initial_sleep: float = 0.3) -> None:
        from session_dir import SessionDir  # local import; project module

        deadline = time.monotonic() + max_seconds
        # Optional initial sleep — useful AFTER we start mpv (let it spin
        # up before checking), pointless BEFORE we'd start a new one.
        if initial_sleep > 0:
            time.sleep(initial_sleep)
        while time.monotonic() < deadline:
            if not SessionDir.is_mpv_running():
                return
            time.sleep(0.25)
        _log(f"mpv still running after {max_seconds}s; moving on")


_STALE_MARKER_DAYS = 30


def _sweep_stale_session_markers() -> None:
    """Remove per-session marker files older than _STALE_MARKER_DAYS in
    both narrate-sessions and autoplay-sessions dirs. Each new Claude
    Code session creates a marker if its user opts in; they accumulate
    over time without this. 30-day window is conservative — sessions
    that old are almost certainly gone."""
    cutoff = time.time() - (_STALE_MARKER_DAYS * 86400)
    for sub in ("auto-speech-narrate-sessions", "auto-speech-autoplay-sessions"):
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
