"""Isolation proof: with AUTO_SPEECH_TMP_ROOT set, every runtime path the
code uses (socket, pid files, logs, markers, mpv pid) lives under that
directory, and with it unset every one lives under /tmp. Each module is
imported in a fresh interpreter with the env set, so module-level
constants are exercised exactly as a test process or a worktree daemon
would see them. Shell scripts are checked by sourcing their variable
block under the same env. Hermetic; no daemon is started."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
SHELL = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "shell"

MODULES = [
    "narrator_service",
    "daemon_client",
    "speak",
    "replay",
    "cli",
    "mcp_server",
    "native_audio_sink",
    "dedup_guard",
    "autoplay_worker",
    "staleness_beacon",
    "sound_yield",
    "http_routing",
]

_DUMP = r'''
import importlib, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
out = {}
for name in sys.argv[2].split(","):
    mod = importlib.import_module(name)
    for attr, val in vars(mod).items():
        if attr.startswith("_"):
            continue
        if isinstance(val, (str, Path)) and str(val).startswith("/") and Path(val).name.startswith("auto-speech-"):
            out[f"{name}.{attr}"] = str(val)
print(json.dumps(out))
'''

SHELL_SCRIPTS = {
    "narrator_service_start.sh": ("PID_FILE", "LOG_FILE", "OUT_FILE"),
    "narrator_service_status.sh": ("PID_FILE", "DEPTH_FILE", "LOG_FILE"),
    "narrator_hook.sh": ("EVENTS_LOG", "ERR_LOG"),
    "autoplay_hook.sh": ("LOG", "BEACON_DEFAULT"),
    "autoplay_status.sh": ("LOG",),
    "start_webapp.sh": ("PIDFILE", "LOG"),
}


def _module_paths(state_dir: str | None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("AUTO_SPEECH_TMP_ROOT", None)
    env.pop("AUTO_SPEECH_DAEMON_SOCK", None)
    if state_dir is not None:
        env["AUTO_SPEECH_TMP_ROOT"] = state_dir
    res = subprocess.run(
        [sys.executable, "-c", _DUMP, str(SRC), ",".join(MODULES)],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return json.loads(res.stdout.strip().splitlines()[-1])


def _shell_vars(script: str, names: tuple[str, ...], state_dir: str | None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("AUTO_SPEECH_TMP_ROOT", None)
    if state_dir is not None:
        env["AUTO_SPEECH_TMP_ROOT"] = state_dir
    # Evaluate only the assignment lines of the script, in order, so sourcing
    # never runs its body.
    text = (SHELL / script).read_text().splitlines()
    assigns = [ln for ln in text if ln.startswith(("STATE_DIR=",) + tuple(f"{n}=" for n in names))]
    probe = "\n".join(assigns) + "\n" + "\n".join(f'printf "%s\\n" "{n}=${n}"' for n in names)
    res = subprocess.run(["bash", "-c", probe], capture_output=True, text=True, env=env, check=True)
    return dict(line.split("=", 1) for line in res.stdout.strip().splitlines())


def test_modules_follow_state_dir() -> None:
    with tempfile.TemporaryDirectory(prefix="auto-speech-state-") as d:
        paths = _module_paths(d)
        assert paths, "no runtime paths found"
        for key in (
            "narrator_service.DEFAULT_SOCKET_PATH",
            "narrator_service.PID_FILE",
            "narrator_service.LOG_FILE",
            "narrator_service.EVENTS_LOG",
            "daemon_client.DEFAULT_SOCKET_PATH",
            "native_audio_sink.MPV_PID_PATH",
            "mcp_server.SAY_LOG",
            "dedup_guard.NOW_PLAYING_LOCK",
            "autoplay_worker.NARRATION_DAEMON_PID_FILE",
            "staleness_beacon.BEACON_DEFAULT",
            "sound_yield.MPV_PID_PATH",
        ):
            assert key in paths, f"{key} missing from {sorted(paths)}"
        wrong = {k: v for k, v in paths.items() if not v.startswith(d + "/")}
        assert not wrong, f"paths outside the state dir: {wrong}"


def test_modules_default_to_tmp() -> None:
    paths = _module_paths(None)
    wrong = {k: v for k, v in paths.items() if not v.startswith("/tmp/auto-speech-")}
    assert not wrong, f"paths not under /tmp by default: {wrong}"


def test_shell_scripts_follow_state_dir() -> None:
    with tempfile.TemporaryDirectory(prefix="auto-speech-state-") as d:
        for script, names in SHELL_SCRIPTS.items():
            got = _shell_vars(script, names, d)
            for n in names:
                assert got[n].startswith(d + "/auto-speech-"), f"{script}: {n}={got[n]}"
            default = _shell_vars(script, names, None)
            for n in names:
                assert default[n].startswith("/tmp/auto-speech-"), f"{script}: {n}={default[n]} by default"


def test_no_literal_tmp_path_left_in_code() -> None:
    offenders: list[str] = []
    for f in list(SRC.glob("*.py")) + list(SHELL.glob("*.sh")):
        in_doc = False
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if line.count('"""') % 2 == 1:
                in_doc = not in_doc
                continue
            if in_doc or line.lstrip().startswith("#"):
                continue
            if "/tmp/auto-speech-" in line and "auto-speech-sync.log" not in line:
                offenders.append(f"{f.name}:{i}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def main() -> int:
    tests = [
        test_modules_follow_state_dir,
        test_modules_default_to_tmp,
        test_shell_scripts_follow_state_dir,
        test_no_literal_tmp_path_left_in_code,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"state_paths: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
