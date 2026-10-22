"""`auto-speech narrate restart [--when-quiet] [--timeout S]`: a restart that
waits for the queue to drain (ADR-024).

On 2026-10-21 at 23:36 a restart cut part 2 of a 20-part briefing while it
played. A restart now waits until nothing is playing, nothing is queued,
and (when ~/.local/bin/desk-speaker has a status) desk-speaker has nothing
pending; then it restarts the daemon through its launchd label. On timeout
it refuses and says what is still queued. `--when-quiet` also waits for no
hold: no other app's sound and no meeting holding a line.

The daemon's own SIGTERM path is the second guard: the line in flight
finishes and anything still queued goes on the missed list.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LAUNCHD_LABEL = os.environ.get("AUTO_SPEECH_LAUNCHD_LABEL", "dev.auto-speech.narrator")
DESK_SPEAKER = Path.home() / ".local" / "bin" / "desk-speaker"
DEFAULT_TIMEOUT_S = 300.0
POLL_S = 1.0
BUSY_STATES = ("PLAYING", "SYNTHESIZING", "PURGING", "INTERRUPTED")


@dataclass
class RestartResult:
    ok: bool
    text: str


def busy_reasons(status: dict[str, Any] | None, *, when_quiet: bool = False) -> list[str]:
    """What keeps the daemon from a clean restart, from its status reply.
    Empty: drained. None (no reply): nothing to drain."""
    if not status:
        return []
    reasons: list[str] = []
    state = str(status.get("daemon_state") or "")
    if state in BUSY_STATES or status.get("active_priority") is not None:
        reasons.append("a line is playing")
    depths = status.get("queue_depths")
    queued = sum(int(v) for v in depths.values()) if isinstance(depths, dict) else int(status.get("queued") or 0)
    if queued:
        reasons.append(f"{queued} line{'s' if queued != 1 else ''} queued")
    if when_quiet:
        hold = status.get("sound_yield")
        if isinstance(hold, dict) and hold.get("holding"):
            reasons.append(f"a line is held ({hold.get('held_seconds', 0):g} s) for another app's sound or a meeting")
    return reasons


def desk_speaker_pending(
    path: Path = DESK_SPEAKER,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> int | None:
    """How many lines desk-speaker has pending, from `desk-speaker status`;
    None when it has no such status (or it cannot be read)."""
    if not path.is_file() or not os.access(path, os.X_OK):
        return None
    try:
        res = run([str(path), "status"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    out = (res.stdout or "").strip()
    try:
        data = json.loads(out)
    except ValueError:
        data = None
    if isinstance(data, dict) and "pending" in data:
        pending = data["pending"]
        if isinstance(pending, list):
            return len(pending)
        if isinstance(pending, (int, float)) and not isinstance(pending, bool):
            return int(pending)
    m = re.search(r"pending\D{0,3}(\d+)", out, re.IGNORECASE)
    return int(m.group(1)) if m else None


def kickstart(label: str = LAUNCHD_LABEL, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> RestartResult:
    target = f"gui/{os.getuid()}/{label}"
    try:
        res = run(["launchctl", "kickstart", "-k", target], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return RestartResult(False, f"narrate restart: launchctl failed: {exc}")
    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()
        return RestartResult(False, f"narrate restart: launchctl kickstart {target} failed ({res.returncode}): {detail}")
    return RestartResult(True, f"narrate restart: restarted {target} after the queue drained")


def restart(
    *,
    status: Callable[[], dict[str, Any] | None],
    when_quiet: bool = False,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    pending: Callable[[], int | None] = desk_speaker_pending,
    restart_job: Callable[[], RestartResult] = kickstart,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = POLL_S,
) -> RestartResult:
    """Waits for the drain, then restarts; refuses on timeout."""
    deadline = clock() + max(0.0, timeout_s)
    while True:
        try:
            reply = status()
        except Exception:  # noqa: BLE001 — a daemon that does not answer has nothing queued
            reply = None
        reasons = busy_reasons(reply, when_quiet=when_quiet)
        waiting = pending()
        if waiting:
            reasons.append(f"desk-speaker has {waiting} pending")
        if not reasons:
            return restart_job()
        if clock() >= deadline:
            return RestartResult(
                False,
                f"narrate restart refused: still busy after {timeout_s:g} s: {'; '.join(reasons)}. Nothing was restarted.",
            )
        sleep(poll_s)
