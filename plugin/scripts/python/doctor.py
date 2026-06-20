"""Doctor — one-shot health probe for auto-speech.

Surfaces the conditions that otherwise require the operator to go looking:
missing binaries, a starved /tmp, an over-cap log (rotation lagging), a
stale/recycled daemon pid, a saturated narration queue, and the current
autoplay scope. Renders human text or `--json`, and exits non-zero when any
check FAILs so it can gate scripts and monitors.

Every system probe is injectable so the checks are hermetically testable.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from auto_speech_log import max_bytes
from autoplay_scope import SoloScope
from health_report import HealthReport, Status

# FAIL below this much free space on the logs/cache filesystem; WARN below
# the higher mark. Unbounded /tmp growth was the original blocker, so headroom
# is a first-class signal.
DISK_FAIL_BYTES = 50 * 1024 * 1024
DISK_WARN_BYTES = 200 * 1024 * 1024

LOG_FILES = (
    "auto-speech-narrator-daemon.log",
    "auto-speech-narrator-daemon.out",
    "auto-speech-autoplay.log",
    "auto-speech-claude-stderr.log",
)


def _default_venv_python() -> Path:
    # plugin/scripts/python/doctor.py → project root is parents[3]
    return Path(__file__).resolve().parents[3] / ".venv" / "bin" / "python"


def _default_daemon_alive(pid: int) -> bool:
    # Reuse the narrator's PID-identity guard (handles reuse, not just kill -0).
    from narrator_service import _pid_is_our_daemon

    return _pid_is_our_daemon(pid)


class Doctor:
    def __init__(
        self,
        *,
        home: Path | None = None,
        tmp: Path | None = None,
        venv_python: Path | None = None,
        max_queue_depth: int = 32,
        which=shutil.which,
        disk_usage=shutil.disk_usage,
        daemon_alive=_default_daemon_alive,
    ) -> None:
        self._home = Path(home) if home is not None else Path(os.path.expanduser("~"))
        self._tmp = Path(tmp) if tmp is not None else Path("/tmp")
        self._venv_python = Path(venv_python) if venv_python is not None else _default_venv_python()
        self._max_queue_depth = max_queue_depth
        self._which = which
        self._disk_usage = disk_usage
        self._daemon_alive = daemon_alive

    def run(self) -> HealthReport:
        r = HealthReport()
        self._check_binaries(r)
        self._check_venv(r)
        self._check_disk(r)
        self._check_logs(r)
        self._check_daemon(r)
        self._check_queue(r)
        self._check_scope(r)
        return r

    # --- individual probes -------------------------------------------------

    def _check_binaries(self, r: HealthReport) -> None:
        if self._which("mpv"):
            r.add("mpv", Status.OK, "found (playback available)")
        else:
            r.add("mpv", Status.FAIL, "missing — no audio playback (brew install mpv)")
        if self._which("uv"):
            r.add("uv", Status.OK, "found")
        else:
            r.add("uv", Status.WARN, "missing — install/update unavailable (brew install uv)")

    def _check_venv(self, r: HealthReport) -> None:
        if os.access(self._venv_python, os.X_OK):
            r.add("venv", Status.OK, f"python present at {self._venv_python}")
        else:
            r.add("venv", Status.FAIL, f"interpreter missing at {self._venv_python} (run setup/install.sh)")

    def _check_disk(self, r: HealthReport) -> None:
        try:
            free = self._disk_usage(str(self._tmp)).free
        except OSError as exc:
            r.add("disk", Status.WARN, f"could not stat {self._tmp}: {exc}")
            return
        mb = free // (1024 * 1024)
        if free < DISK_FAIL_BYTES:
            r.add("disk", Status.FAIL, f"only {mb} MiB free on {self._tmp} — logs/cache may fail")
        elif free < DISK_WARN_BYTES:
            r.add("disk", Status.WARN, f"{mb} MiB free on {self._tmp} (low)")
        else:
            r.add("disk", Status.OK, f"{mb} MiB free on {self._tmp}")

    def _check_logs(self, r: HealthReport) -> None:
        cap = max_bytes()
        oversize = []
        for name in LOG_FILES:
            try:
                size = (self._tmp / name).stat().st_size
            except OSError:
                continue  # absent → nothing to report
            if size >= cap:
                oversize.append(f"{name}={size // 1024} KiB")
        if oversize:
            r.add(
                "logs",
                Status.WARN,
                f"over cap ({cap // (1024 * 1024)} MiB), rotation lagging: {', '.join(oversize)}",
            )
        else:
            r.add("logs", Status.OK, f"all under cap ({cap // (1024 * 1024)} MiB)")

    def _check_daemon(self, r: HealthReport) -> None:
        pid_file = self._tmp / "auto-speech-narrator-daemon.pid"
        try:
            pid = int(pid_file.read_text().strip())
        except (OSError, ValueError):
            r.add("narrator", Status.OK, "not running (idle-exit is normal between sessions)")
            return
        if self._daemon_alive(pid):
            r.add("narrator", Status.OK, f"running (pid={pid})")
        else:
            r.add("narrator", Status.WARN, f"stale pid {pid} (dead/recycled) — reclaimed on next start")

    def _check_queue(self, r: HealthReport) -> None:
        depth_file = self._tmp / "auto-speech-narration-depth"
        try:
            depth = int(depth_file.read_text().strip())
        except (OSError, ValueError):
            r.add("queue", Status.OK, "idle")
            return
        if depth >= self._max_queue_depth:
            r.add("queue", Status.WARN, f"saturated (depth={depth}/{self._max_queue_depth}) — dropping oldest")
        else:
            r.add("queue", Status.OK, f"depth={depth}/{self._max_queue_depth}")

    def _check_scope(self, r: HealthReport) -> None:
        if (self._home / ".claude" / "auto-speech.disabled").exists():
            r.add("autoplay", Status.WARN, "globally muted (~/.claude/auto-speech.disabled present)")
        else:
            r.add("autoplay", Status.OK, "enabled (default)")
        scope = SoloScope(home=self._home)
        held = scope.current()
        if held is None:
            r.add("scope", Status.OK, "ALL — every session reads")
        else:
            r.add("scope", Status.OK, f"SOLO — only session {held} reads")


def main(argv: list[str]) -> int:
    as_json = "--json" in argv[1:]
    # Use the configured queue cap so the saturation threshold matches reality.
    try:
        from narrator_config import load_config

        max_q = int(load_config().get("max_queue_depth", 32))
    except Exception:
        max_q = 32
    report = Doctor(max_queue_depth=max_q).run()
    print(report.to_json() if as_json else report.to_text())
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
