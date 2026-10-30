"""Every test writes to its own store, never his live one (#m1030-1241-6e0a).

On 2026-10-30 at 11:02 and 11:03 a builder ran
tests/e2e/test_tier5_adversarial_lifecycle.py by hand with a private
AUTO_SPEECH_TMP_ROOT but no AUTO_SPEECH_HISTORY_DIR. Its NarratorService was
built with no history passed in, so SpeechHistory() took its default,
~/.local/state/auto-speech/history, and 258 fixture lines ("req_0222".."req_0349",
"success_item_3") became rows in his speech player.

The one place that prevents that: every test file imports this module before
it imports any of auto-speech's own modules, and tests/run_all.sh takes its
exports from here (`isolation.py --exports DIR`). On import it gives every
path auto-speech keeps state in a private folder, unless the caller already
named one, and points HOME at a private home, so a path no variable names yet
(Path.home() / ...) lands in the private home too, never in his.

Nothing in the product refuses anything (his 2026-10-15 12:51): the tests
choose where they write.
"""

from __future__ import annotations

import atexit
import os
import pwd
import shutil
import sys
import tempfile
from pathlib import Path

MARK_ENV = "AUTO_SPEECH_TEST_ISOLATED"  # the private folder, once isolated
REAL_HOME_ENV = "AUTO_SPEECH_TEST_REAL_HOME"  # his home, for the guard test to look at, never to write

# Every variable auto-speech reads a state path from, and where it goes
# inside the private folder. A new state path gets its variable here.
PATHS = {
    "AUTO_SPEECH_TMP_ROOT": "",  # sockets, pid files, logs, the stopped file (state_paths.py)
    "AUTO_SPEECH_DAEMON_SOCK": "auto-speech-daemon.sock",
    "AUTO_SPEECH_MISSED_FILE": "missed.json",
    "AUTO_SPEECH_MEETING_FILE": "meeting.json",
    "AUTO_SPEECH_HISTORY_DIR": "history",
    "AUTO_SPEECH_HISTORY_CONFIG": "history.toml",
    "AUTO_SPEECH_LINES_FILE": "lines.json",
    "UI_UTILITY_SOCK": "ui-utility.sock",
}
# Switches: no launchd label, no real UI Utility, no player launch.
FIXED = {
    "AUTO_SPEECH_START_VIA_LAUNCHD": "never",
    "UI_UTILITY_SILENT": "1",
    "AUTO_SPEECH_PLAYER_LAUNCH": "never",
}


def real_home() -> Path:
    """His home folder from the password database: HOME may be the private one."""
    return Path(os.environ.get(REAL_HOME_ENV) or pwd.getpwuid(os.getuid()).pw_dir)


def live_state_dir() -> Path:
    return real_home() / ".local" / "state" / "auto-speech"


def _private_dir() -> Path:
    for name in (MARK_ENV, "AUTO_SPEECH_TMP_ROOT"):
        raw = os.environ.get(name, "").strip()
        if raw:
            return Path(raw)
    made = Path(tempfile.mkdtemp(prefix="auto-speech-tests-", dir="/tmp"))
    # Made here (a test run by hand): it goes when this process ends. A
    # folder the runner made is the runner's to remove.
    atexit.register(shutil.rmtree, made, True)
    return made


def environment(root: Path, env: dict[str, str] | None = None) -> dict[str, str]:
    """The variables to set for a test process whose private folder is `root`:
    each one only when `env` (the caller's) does not already name it."""
    env = os.environ if env is None else env
    out: dict[str, str] = {}
    for name, rel in PATHS.items():
        if not env.get(name, "").strip():
            out[name] = str(root / rel) if rel else str(root)
    for name, value in FIXED.items():
        if not env.get(name, "").strip():
            out[name] = value
    out.setdefault(REAL_HOME_ENV, env.get(REAL_HOME_ENV) or str(real_home()))
    home = root / "home"
    if env.get("HOME", "") != str(home):
        out["HOME"] = str(home)
    out[MARK_ENV] = str(root)
    return out


def isolate() -> Path:
    """Isolate this process (and every child it starts). Idempotent."""
    root = _private_dir()
    for name, value in environment(root).items():
        os.environ[name] = value
    (root / "home").mkdir(parents=True, exist_ok=True)
    # No test plays sound (his 2026-10-20 10:40), unless asked on purpose.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from quiet_audio import ensure_quiet
    finally:
        sys.path.pop(0)
    ensure_quiet()
    return root


def _shell_exports(root: Path) -> str:
    import shlex

    root.mkdir(parents=True, exist_ok=True)
    (root / "home").mkdir(parents=True, exist_ok=True)
    return "".join(f"export {k}={shlex.quote(v)}\n" for k, v in environment(root, env={}).items())


if __name__ == "__main__":
    # `isolation.py --exports DIR`: the lines tests/run_all.sh evals.
    if len(sys.argv) == 3 and sys.argv[1] == "--exports":
        sys.stdout.write(_shell_exports(Path(sys.argv[2])))
        sys.exit(0)
    print("usage: isolation.py --exports DIR", file=sys.stderr)
    sys.exit(2)
else:
    PRIVATE_DIR = isolate()
