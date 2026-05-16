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

from narrator_config import load_config
from narrator_phase_classifier import Category, Phase, PhaseClassifier
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


def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except OSError:
        pass


def _existing_pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
        return pid
    except (ProcessLookupError, PermissionError):
        return None


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
        self._tts_queue: queue.Queue[Phase | None] = queue.Queue()
        self._summarizer: Summarizer | None = None
        self._summarizer_lock = threading.Lock()
        self._last_event_ts = time.time()
        self._idle_shutdown = float(self._config["idle_shutdown_seconds"])
        self._stop = threading.Event()

    def run(self) -> int:
        PID_FILE.write_text(str(os.getpid()))
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)
        _log(f"started pid={os.getpid()} provider={self._config['provider']}")
        self._update_depth(0)

        tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
        tts_thread.start()

        try:
            self._tail_events()
        finally:
            self._tts_queue.put(None)  # sentinel
            tts_thread.join(timeout=5.0)
            try:
                PID_FILE.unlink()
            except OSError:
                pass
            _log("shutdown")
        return 0

    def _on_signal(self, signum, frame):  # noqa: ARG002
        _log(f"signal {signum} → shutting down")
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

            # Per-cwd marker recheck. The hook gates on PWD at fire time,
            # but the daemon is global — without this check, a stray
            # event from another project's session (or a stale event
            # whose marker has since been removed) would still narrate.
            # The hook is cheap; the speak path is not. Filter here.
            cwd = ev.get("cwd") or ""
            if cwd:
                marker = Path(cwd) / ".claude" / "narrate.enabled"
                if not marker.exists():
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
        self._tts_queue.put(phase)
        self._update_depth(self._tts_queue.qsize())
        _log(
            f"enqueued phase={phase.category.value} "
            f"events={len(phase.events)} depth={self._tts_queue.qsize()}"
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
        FIFO. We use run_speak.sh and then wait for the resulting mpv to
        exit before pulling the next phase off the queue."""
        _log(f"speak: {line[:80]}")
        speak = _speak_script()
        proc = subprocess.run(
            [str(speak)],
            input=line.encode("utf-8"),
            capture_output=True,
            timeout=120,
        )
        if proc.returncode != 0:
            _log(f"speak rc={proc.returncode} stderr={proc.stderr.decode(errors='replace')[:200]}")
            return
        # Wait for mpv to finish so the next narration doesn't kill this one.
        self._wait_mpv_idle(max_seconds=120.0)

    def _wait_mpv_idle(self, max_seconds: float) -> None:
        from session_dir import SessionDir  # local import; project module

        deadline = time.monotonic() + max_seconds
        # Give mpv a moment to start before we check
        time.sleep(0.3)
        while time.monotonic() < deadline:
            if not SessionDir.is_mpv_running():
                return
            time.sleep(0.25)
        _log(f"mpv still running after {max_seconds}s; moving on")


def main() -> int:
    existing = _existing_pid()
    if existing is not None:
        print(f"narrator daemon already running (pid={existing})", file=sys.stderr)
        return 1

    svc = NarratorService()
    return svc.run()


if __name__ == "__main__":
    sys.exit(main())
