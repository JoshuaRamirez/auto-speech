"""Where auto-speech keeps its runtime state: socket, pid files, logs, markers.

Every `/tmp/auto-speech-*` path in the code is `state_path("auto-speech-*")`,
so one environment variable moves all of them together:

    AUTO_SPEECH_TMP_ROOT   directory for runtime state (default /tmp); the
                           same variable narrator_service_stop.sh, uninstall.sh
                           and the e2e harness already honoured

The live daemon, the hooks and the user's CLI never set it and keep using
/tmp. The test runner (tests/run_all.sh) and any worktree service set it
to a private directory, so a test or a development daemon can never take
the live socket, write the live log, or interrupt the live playback.
"""

from __future__ import annotations

import os
from pathlib import Path

STATE_DIR_ENV = "AUTO_SPEECH_TMP_ROOT"
DEFAULT_STATE_DIR = Path("/tmp")


def state_dir() -> Path:
    raw = os.environ.get(STATE_DIR_ENV, "").strip()
    return Path(raw) if raw else DEFAULT_STATE_DIR


def state_path(name: str) -> Path:
    """`name` is the bare file name, e.g. "auto-speech-daemon.sock"."""
    return state_dir() / name
