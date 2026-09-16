"""SayWorker: speak caller-supplied words verbatim through the playback queue.

The detached half of the MCP `speak` tool. The server writes the words to
a private temp file and spawns this worker, which:

  global-mute check → enqueue FIFO ticket → wait for turn (mpv idle,
  narrator drained, head of queue) → speak (stdin = the words, verbatim)

Unlike the autoplay worker there is no transcript extraction, no LLM
rewrite, no cache, no dedup and no staleness: an explicit request to say
something twice is said twice, in arrival order, and never cuts off
another playback.

ALWAYS exits 0. Failures are logged, never raised. The text file is
removed as soon as it has been read.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from autoplay_gate import AutoplayGate
from autoplay_worker import SPEAK, _int_env, mpv_running, narrator_depth
from playback_fifo import PlaybackFifo


def _default_runner(argv, *, stdin_text: str) -> int:
    proc = subprocess.run(
        argv,
        input=stdin_text.encode("utf-8"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode


class SayWorker:
    """Speaks one text file's contents. `run()` always returns 0."""

    def __init__(
        self,
        text_path: str,
        *,
        gate: AutoplayGate | None = None,
        fifo: PlaybackFifo | None = None,
        mpv_running=mpv_running,
        narrator_depth=narrator_depth,
        queue_wait_max: int | None = None,
        runner=_default_runner,
        sleep=time.sleep,
        log=None,
    ) -> None:
        self._text_path = Path(text_path)
        self._gate = gate or AutoplayGate()
        self._user_log = log
        self._fifo = fifo or PlaybackFifo(log=self._log)
        self._mpv_running = mpv_running
        self._narrator_depth = narrator_depth
        self._queue_wait_max = (
            queue_wait_max
            if queue_wait_max is not None
            else _int_env("AUTO_SPEECH_QUEUE_WAIT_MAX", 600)
        )
        self._runner = runner
        self._sleep = sleep

    def _log(self, msg: str) -> None:
        if self._user_log is not None:
            self._user_log(msg)
            return
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"[{ts}] [say pid={os.getpid()}] {msg}", flush=True)

    def _read_and_remove(self) -> str | None:
        try:
            text = self._text_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            self._log(f"cannot read text file {self._text_path}: {exc!r}")
            return None
        finally:
            try:
                self._text_path.unlink()
            except OSError:
                pass
        return text

    def run(self) -> int:
        text = self._read_and_remove()
        if text is None or not text.strip():
            self._log("nothing to say; skipping")
            return 0
        if self._gate.worker_gated_off():
            self._log("global mute present; not speaking")
            return 0

        self._fifo.enqueue()
        # No staleness: an explicit speak request is never superseded.
        self._fifo.wait_for_queue_turn(
            self._queue_wait_max,
            self._mpv_running,
            self._narrator_depth,
            lambda: False,
            sleep=self._sleep,
        )
        self._fifo.mark_playing()
        self._log(f"speaking chars={len(text)}")
        try:
            rc = self._runner(["bash", str(SPEAK)], stdin_text=text)
        except OSError as exc:
            self._log(f"speak launch failed: {exc!r}")
            rc = None
        if rc not in (0, None):
            self._log(f"speak exit {rc}")
        self._fifo.release()
        return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point: say_worker.py TEXT_PATH"""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: say_worker.py TEXT_PATH", file=sys.stderr)
        return 0
    SayWorker(args[0]).run()
    return 0  # ALWAYS exit 0


if __name__ == "__main__":
    sys.exit(main())
