"""A test daemon never takes the live narrator's launchd label.

On 2026-10-20 20:02 a niced suite run (desk-heavy) started a daemon through
narrator_service_start.sh's launchd path (ADR-019) under the live label
dev.auto-speech.narrator. launchd gave it none of the test's environment and a
bare PATH: it took the live socket, could not find mpv, and spoke nothing for
two hours. These tests run a copy of the start script on a fake tree, niced,
with a stub launchctl that only records its calls. Hermetic, silent.
"""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "plugin" / "scripts" / "shell"

# The stub daemon: writes its pid where the script looks, lives a few
# seconds so the script sees it running, then exits. Its name matches the
# script's "/narrator_service.py" identity check.
STUB_SERVICE = """\
import os, sys, time
root = os.environ.get("AUTO_SPEECH_TMP_ROOT", "/nonexistent")
with open(os.path.join(root, "auto-speech-narrator-daemon.pid"), "w") as f:
    f.write(str(os.getpid()))
time.sleep(4)
"""

STUB_LAUNCHCTL = """\
#!/bin/bash
printf '%s\\n' "$*" >> "{log}"
exit 0
"""


def _tree() -> tuple[Path, Path, Path, Path]:
    """A fake project: the start script and its helpers, a stub service, a
    .venv/bin/python that is this interpreter, a stub launchctl on PATH."""
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-start-label-"))
    shell = tmp / "plugin" / "scripts" / "shell"
    shell.mkdir(parents=True)
    for name in ("narrator_service_start.sh", "log_rotate.sh", "daemon_pid.sh"):
        shutil.copy2(SHELL / name, shell / name)
    py = tmp / "plugin" / "scripts" / "python"
    py.mkdir(parents=True)
    (py / "narrator_service.py").write_text(STUB_SERVICE)
    venv_bin = tmp / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(sys.executable)
    stub_bin = tmp / "stub-bin"
    stub_bin.mkdir()
    log = tmp / "launchctl.log"
    log.touch()
    launchctl = stub_bin / "launchctl"
    launchctl.write_text(STUB_LAUNCHCTL.format(log=log))
    launchctl.chmod(0o755)
    state = tmp / "state"
    state.mkdir()
    return tmp, state, stub_bin, log


def _start(tmp: Path, state: Path, stub_bin: Path, **extra: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AUTO_SPEECH_")}
    env.update(AUTO_SPEECH_TMP_ROOT=str(state), PATH=f"{stub_bin}:{env.get('PATH', '/usr/bin:/bin')}", **extra)
    script = tmp / "plugin" / "scripts" / "shell" / "narrator_service_start.sh"
    # Niced, as desk-heavy runs the suite: the case that took the launchd path.
    # To nice 5 from wherever this process starts: a GitHub runner starts
    # below 0, where `nice -n 5` alone stays at or under 0.
    step = 5 - min(os.getpriority(os.PRIO_PROCESS, 0), 0)
    return subprocess.run(["nice", "-n", str(step), "bash", str(script)], env=env, capture_output=True, text=True, timeout=30)


def _stop(state: Path) -> None:
    pid_file = state / "auto-speech-narrator-daemon.pid"
    try:
        os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
    except (OSError, ValueError):
        pass


def test_a_niced_start_on_a_private_state_dir_never_calls_launchctl() -> None:
    tmp, state, stub_bin, log = _tree()
    try:
        r = _start(tmp, state, stub_bin)
        assert r.returncode == 0, (r.stdout, r.stderr)
        assert log.read_text() == "", f"launchctl was called: {log.read_text()!r}"
        assert "submitted to launchd" not in r.stdout
        assert "narrator daemon started" in r.stdout, r.stdout
    finally:
        _stop(state)


def test_never_means_never_even_with_a_label() -> None:
    tmp, state, stub_bin, log = _tree()
    try:
        r = _start(tmp, state, stub_bin, AUTO_SPEECH_LAUNCHD_LABEL="dev.auto-speech.narrator.test", AUTO_SPEECH_START_VIA_LAUNCHD="never")
        assert r.returncode == 0, (r.stdout, r.stderr)
        assert log.read_text() == ""
    finally:
        _stop(state)


def test_a_named_label_is_used_and_the_job_gets_path_and_the_settings() -> None:
    tmp, state, stub_bin, log = _tree()
    try:
        _start(tmp, state, stub_bin, AUTO_SPEECH_LAUNCHD_LABEL="dev.auto-speech.narrator.test", AUTO_SPEECH_MPV_AO="null")
        calls = log.read_text().splitlines()
        assert calls and all("dev.auto-speech.narrator.test" in c for c in calls), calls
        assert not any(c.split()[-1:] == ["dev.auto-speech.narrator"] for c in calls), calls
        submit = next(c for c in calls if c.startswith("submit"))
        assert "/usr/bin/env PATH=/opt/homebrew/bin:/usr/local/bin:" in submit, submit
        assert f"AUTO_SPEECH_TMP_ROOT={state}" in submit and "AUTO_SPEECH_MPV_AO=null" in submit, submit
    finally:
        _stop(state)


TESTS = [
    test_a_niced_start_on_a_private_state_dir_never_calls_launchctl,
    test_never_means_never_even_with_a_label,
    test_a_named_label_is_used_and_the_job_gets_path_and_the_settings,
]


if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        try:
            t()
            print(f"ok   {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {exc!r}")
    if failed:
        print(f"{failed} FAILED")
        sys.exit(1)
    print(f"narrator_start_label: {len(TESTS)} tests passed")
