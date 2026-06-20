"""AutoplayWorker: the autoplay worker orchestrator.

Ported from autoplay_worker.sh. Drives WorkerLifecycleMachine and
composes StalenessBeacon, PlaybackFifo, DedupGuard, and AutoplayGate to
reproduce the bash worker's behavior step for step:

  global-disable re-check → coalesce sleep → stale check → enqueue ticket
  → extract last assistant message → min-len → compute hash → stale check
  → cache hit?  yes: stale recheck → dedup → queue turn → now-playing
                     → speak --source-hash (empty stdin)
                no:  require `claude` → cli_rewrite (timeout 90) → empty
                     check → dedup → queue turn → now-playing
                     → speak --source-hash (rewrite stdin)

ALWAYS exits 0. Every failure is logged (greppable `[worker pid sid]`
form) and never bubbles up.

The shell wrappers run_extract.sh / compute_hash.sh / run_speak.sh and
cli_rewrite.py are invoked via subprocess (extract/hash/TTS are not
reimplemented). Collaborators and the subprocess runners are injectable
so the full lifecycle can be walked in tests with no real audio/LLM.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from autoplay_gate import AutoplayGate
from dedup_guard import DedupGuard
from playback_fifo import PlaybackFifo, _pid_alive
from staleness_beacon import StalenessBeacon
from worker_lifecycle import (
    AWAITING_TURN,
    BAILED,
    COALESCING,
    DONE,
    QUEUED,
    RESOLVING,
    SPEAKING,
    WorkerLifecycleMachine,
)

_PLUGIN_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _PLUGIN_SCRIPTS_DIR.parent.parent

EXTRACT = _PLUGIN_SCRIPTS_DIR / "shell" / "run_extract.sh"
COMPUTE_HASH = _PLUGIN_SCRIPTS_DIR / "shell" / "compute_hash.sh"
SPEAK = _PLUGIN_SCRIPTS_DIR / "shell" / "run_speak.sh"
CLI_REWRITE = _PLUGIN_SCRIPTS_DIR / "python" / "cli_rewrite.py"
VENV = _PROJECT_ROOT / ".venv"

NARRATION_DEPTH_FILE = Path("/tmp/auto-speech-narration-depth")
NARRATION_DAEMON_PID_FILE = Path("/tmp/auto-speech-narrator-daemon.pid")
MPV_PID_PATH = Path("/tmp/auto-speech/mpv.pid")
CLAUDE_STDERR_LOG = Path("/tmp/auto-speech-claude-stderr.log")


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def resolve_config() -> dict:
    """Resolve coalesce / narration-wait / queue-wait / min-len.

    Env-var precedence over autoplay.toml, matching the bash worker:
      coalesce         AUTO_SPEECH_AUTOPLAY_COALESCE > TOML > 1
      narration_wait   AUTO_SPEECH_NARRATION_WAIT_MAX > int(TOML) > 90
      queue_wait       AUTO_SPEECH_QUEUE_WAIT_MAX > 600
      min_len          AUTO_SPEECH_AUTOPLAY_MIN_LEN > 20
    """
    coalesce_cfg: float | None = None
    narration_cfg: int | None = None
    try:
        from autoplay_config import load_config

        cfg = load_config()
        coalesce_cfg = cfg.get("coalesce_seconds")
        nw = cfg.get("narration_wait_max_seconds")
        # bash does int(float(...)) so arithmetic doesn't choke on a decimal.
        narration_cfg = int(float(nw)) if nw is not None else None
    except Exception as exc:
        # Don't crash the worker on a bad/unreadable config (e.g. a wrong-
        # typed narration_wait value): fall back to env/defaults below. But
        # log it — silent fallback hides a misconfiguration from the operator.
        print(
            f"[worker] autoplay config load failed; using env/defaults: {exc!r}",
            file=sys.stderr,
        )

    coalesce_env = os.environ.get("AUTO_SPEECH_AUTOPLAY_COALESCE")
    if coalesce_env not in (None, ""):
        coalesce = float(coalesce_env)
    elif coalesce_cfg is not None:
        coalesce = float(coalesce_cfg)
    else:
        coalesce = 1.0

    narration_env = os.environ.get("AUTO_SPEECH_NARRATION_WAIT_MAX")
    if narration_env not in (None, ""):
        narration_wait = int(narration_env)
    elif narration_cfg is not None:
        narration_wait = narration_cfg
    else:
        narration_wait = 90

    return {
        "coalesce_seconds": coalesce,
        "narration_wait_max": narration_wait,
        "queue_wait_max": _int_env("AUTO_SPEECH_QUEUE_WAIT_MAX", 600),
        "min_len": _int_env("AUTO_SPEECH_AUTOPLAY_MIN_LEN", 20),
    }


class AutoplayWorker:
    """Orchestrates one autoplay run. `run()` always returns 0."""

    def __init__(
        self,
        beacon_mtime: float,
        transcript_path: str = "",
        session_id: str = "",
        *,
        config: dict | None = None,
        gate: AutoplayGate | None = None,
        beacon: StalenessBeacon | None = None,
        fifo: PlaybackFifo | None = None,
        dedup: DedupGuard | None = None,
        log=None,
        sleep=time.sleep,
        runner=None,
    ) -> None:
        self._beacon_mtime = float(beacon_mtime)
        self._transcript_path = transcript_path or ""
        self._session_id = session_id or ""
        self._cfg = config or resolve_config()
        self._gate = gate or AutoplayGate()
        self._beacon = beacon or StalenessBeacon(self._beacon_mtime, self._session_id)
        self._fifo = fifo or PlaybackFifo(log=self._log)
        self._dedup = dedup or DedupGuard()
        self._machine = WorkerLifecycleMachine()
        self._sleep = sleep
        self._runner = runner or self._default_runner
        self._user_log = log

    # ---- logging ---------------------------------------------------------
    def _log(self, msg: str) -> None:
        if self._user_log is not None:
            self._user_log(msg)
            return
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sid = self._session_id[:8] if self._session_id else "no-sid"
        print(f"[{ts}] [worker pid={os.getpid()} sid={sid}] {msg}", flush=True)

    @property
    def machine(self) -> WorkerLifecycleMachine:
        return self._machine

    # ---- subprocess plumbing --------------------------------------------
    def _default_runner(self, argv, *, stdin_text=None, stdout_path=None):
        """Run argv; return (returncode, stdout_text). For real invocations."""
        stdout_target = open(stdout_path, "wb") if stdout_path else subprocess.PIPE
        try:
            proc = subprocess.run(
                argv,
                input=stdin_text.encode("utf-8") if stdin_text is not None else None,
                stdout=stdout_target if stdout_path else subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if stdout_path:
                return proc.returncode, None
            return proc.returncode, proc.stdout.decode("utf-8", "replace")
        finally:
            if stdout_path:
                stdout_target.close()

    # ---- queue-turn collaborators ---------------------------------------
    def _mpv_running(self) -> bool:
        try:
            raw = MPV_PID_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        if not raw:
            return False
        try:
            return _pid_alive(int(raw))
        except ValueError:
            return False

    def _narrator_depth(self) -> int:
        if not (NARRATION_DAEMON_PID_FILE.is_file() and NARRATION_DEPTH_FILE.is_file()):
            return 0
        try:
            narr_pid = int(NARRATION_DAEMON_PID_FILE.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return 0
        if not _pid_alive(narr_pid):
            return 0
        try:
            return int(NARRATION_DEPTH_FILE.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            return 0

    def _wait_turn(self) -> bool:
        return self._fifo.wait_for_queue_turn(
            self._cfg["queue_wait_max"],
            self._mpv_running,
            self._narrator_depth,
            self._beacon.is_stale,
            sleep=self._sleep,
        )

    # ---- run -------------------------------------------------------------
    def run(self) -> int:
        """Execute the worker flow. Always returns 0."""
        # Global-disable re-check (worker scope).
        if self._gate.worker_gated_off():
            self._log("disable marker present; bailing")
            self._machine.transition(BAILED)
            return 0

        # Coalesce window: collapse bursts of Stop events.
        self._machine.transition(COALESCING)
        self._sleep(self._cfg["coalesce_seconds"])
        if self._beacon.is_stale():
            self._log("stale during coalesce window; bailing (newer worker will handle)")
            self._machine.transition(BAILED)
            return 0

        # Take our FIFO slot now — arrival order is Stop-event order.
        self._machine.transition(QUEUED)
        self._fifo.enqueue()

        if not os.access(str(EXTRACT), os.X_OK):
            self._log(f"extract wrapper missing or not executable: {EXTRACT}")
            self._machine.transition(BAILED)
            return 0

        self._machine.transition(RESOLVING)

        # Extract last assistant message.
        extract_argv = ["bash", str(EXTRACT), "--ordinal", "1"]
        if self._transcript_path:
            extract_argv += ["--transcript-path", self._transcript_path]
        rc, src = self._runner(extract_argv)
        if rc != 0 or src is None:
            self._log("extract failed (no qualifying message?); skipping")
            self._machine.transition(BAILED)
            return 0

        src_len = len(src.encode("utf-8"))
        if src_len < self._cfg["min_len"]:
            self._log(f"source too short ({src_len} < {self._cfg['min_len']}); skipping")
            self._machine.transition(BAILED)
            return 0

        # Compute cache key.
        rc, source_hash = self._runner(
            ["bash", str(COMPUTE_HASH)], stdin_text=src
        )
        source_hash = (source_hash or "").strip()
        if rc != 0 or not source_hash:
            self._log("compute_hash failed; skipping")
            self._machine.transition(BAILED)
            return 0
        hash_prefix = source_hash[:16]
        self._log(f"source chars={src_len} hash={hash_prefix}")

        if self._beacon.is_stale():
            self._log("stale before cache check; bailing")
            self._machine.transition(BAILED)
            return 0

        cache_wav = _PROJECT_ROOT / "config" / "cache" / hash_prefix / "full.wav"
        if cache_wav.is_file():
            return self._play_cache_hit(source_hash)
        return self._play_cache_miss(source_hash, src)

    def _play_cache_hit(self, source_hash: str) -> int:
        self._log("cache hit; playing")
        if self._beacon.is_stale():
            self._log("stale just before play; bailing")
            self._machine.transition(BAILED)
            return 0
        if self._dedup.already_playing(source_hash):
            self._log("same hash already playing; skipping duplicate (cache-hit path)")
            self._machine.transition(BAILED)
            return 0
        self._machine.transition(AWAITING_TURN)
        if not self._wait_turn():
            self._machine.transition(BAILED)
            return 0
        if not self._dedup.try_claim(source_hash):
            self._log("same hash claimed by a sibling worker; skipping duplicate (cache-hit path)")
            self._machine.transition(BAILED)
            return 0
        self._speak(source_hash, stdin_text="", path_label="cache-hit path")
        return 0

    def _play_cache_miss(self, source_hash: str, src: str) -> int:
        # Cache miss requires the claude binary for the rewrite step.
        from shutil import which

        if which("claude") is None:
            self._log("claude not on PATH; skipping rewrite")
            self._machine.transition(BAILED)
            return 0

        self._log("invoking cli_rewrite.py (wraps claude -p with timeout)")
        if not VENV.is_dir():
            self._log(f"venv missing at {VENV}; skipping")
            self._machine.transition(BAILED)
            return 0

        rc, rewrite = self._runner(
            [str(VENV / "bin" / "python"), str(CLI_REWRITE), "--timeout", "90"],
            stdin_text=src,
        )
        if rc != 0:
            self._log(f"cli_rewrite exit {rc}; skipping")
            self._machine.transition(BAILED)
            return 0
        rewrite = rewrite or ""
        if len(rewrite.encode("utf-8")) < 1:
            self._log("cli_rewrite produced empty output; skipping")
            self._machine.transition(BAILED)
            return 0
        self._log(f"rewrite chars={len(rewrite.encode('utf-8'))}")

        if self._dedup.already_playing(source_hash):
            self._log("same hash already playing; skipping duplicate (post-rewrite path)")
            self._machine.transition(BAILED)
            return 0
        self._machine.transition(AWAITING_TURN)
        if not self._wait_turn():
            self._machine.transition(BAILED)
            return 0
        if not self._dedup.try_claim(source_hash):
            self._log("same hash claimed by a sibling worker; skipping duplicate (post-rewrite path)")
            self._machine.transition(BAILED)
            return 0
        self._speak(source_hash, stdin_text=rewrite, path_label="after rewrite")
        return 0

    def _speak(self, source_hash: str, *, stdin_text: str, path_label: str) -> None:
        # The now-playing marker was already stamped atomically by
        # try_claim() before this point (see _play_cache_* paths).
        self._machine.transition(SPEAKING)
        self._fifo.mark_playing()
        rc, _ = self._runner(
            ["bash", str(SPEAK), "--source-hash", source_hash],
            stdin_text=stdin_text,
        )
        if rc != 0:
            self._log(f"speak.py exit {rc} ({path_label})")
        self._machine.transition(DONE)


def main(argv: list[str] | None = None) -> int:
    """Entry point matching the shim arg contract:

        autoplay_worker.py BEACON_MTIME [TRANSCRIPT_PATH] [SESSION_ID]
    """
    args = list(sys.argv[1:] if argv is None else argv)
    beacon_mtime = float(args[0]) if len(args) >= 1 and args[0] else 0.0
    transcript_path = args[1] if len(args) >= 2 else ""
    session_id = args[2] if len(args) >= 3 else ""
    worker = AutoplayWorker(beacon_mtime, transcript_path, session_id)
    worker.run()
    return 0  # ALWAYS exit 0


if __name__ == "__main__":
    sys.exit(main())
