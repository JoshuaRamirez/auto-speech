"""Unit tests for the doctor health probe.

Every system probe is injected (which / disk_usage / daemon_alive / temp
home+tmp / venv python) so the checks run hermetically. Covers the healthy
baseline, each FAIL condition (mpv, venv, disk), the WARN conditions
(oversize log, stale daemon, saturated queue, global mute), the running-
daemon OK path, scope reporting, exit codes, and JSON shape.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

import doctor as doc  # noqa: E402
from health_report import Status  # noqa: E402

GIB = 1024 * 1024 * 1024


def _which_all(name):  # both binaries present
    return f"/usr/bin/{name}"


def _disk_free(n):
    return lambda _p: SimpleNamespace(free=n)


def _doctor(home, tmp, **over):
    kw = dict(
        home=home,
        tmp=tmp,
        venv_python=Path(sys.executable),  # a real executable → venv OK
        which=_which_all,
        disk_usage=_disk_free(5 * GIB),
        daemon_alive=lambda _pid: False,
    )
    kw.update(over)
    return doc.Doctor(**kw)


def _check(report, name):
    return next(c for c in report.checks if c.name == name)


def test_healthy_baseline() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t:
        report = _doctor(Path(h), Path(t)).run()
        assert report.healthy is True
        assert report.exit_code == 0
        assert _check(report, "mpv").status is Status.OK
        assert _check(report, "venv").status is Status.OK
        assert _check(report, "disk").status is Status.OK
        assert _check(report, "narrator").status is Status.OK  # not running = OK
        assert _check(report, "scope").status is Status.OK


def test_missing_mpv_is_fail() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t:
        report = _doctor(Path(h), Path(t), which=lambda n: None if n == "mpv" else "/usr/bin/x").run()
        assert _check(report, "mpv").status is Status.FAIL
        assert report.healthy is False
        assert report.exit_code == 1


def test_missing_uv_is_only_warn() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t:
        report = _doctor(Path(h), Path(t), which=lambda n: None if n == "uv" else "/usr/bin/x").run()
        assert _check(report, "uv").status is Status.WARN
        assert report.healthy is True  # uv missing degrades, not breaks


def test_missing_venv_is_fail() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t:
        report = _doctor(Path(h), Path(t), venv_python=Path(t) / "no-python").run()
        assert _check(report, "venv").status is Status.FAIL
        assert report.exit_code == 1


def test_low_disk_is_fail() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t:
        report = _doctor(Path(h), Path(t), disk_usage=_disk_free(10 * 1024 * 1024)).run()
        assert _check(report, "disk").status is Status.FAIL
        assert report.exit_code == 1


def test_oversize_log_warns_but_healthy() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t:
        big = Path(t) / "auto-speech-autoplay.log"
        big.write_bytes(b"x" * (6 * 1024 * 1024))  # > 5 MiB default cap
        report = _doctor(Path(h), Path(t)).run()
        assert _check(report, "logs").status is Status.WARN
        assert report.healthy is True


def test_running_daemon_ok() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t:
        (Path(t) / "auto-speech-narrator-daemon.pid").write_text("4321")
        report = _doctor(Path(h), Path(t), daemon_alive=lambda _pid: True).run()
        c = _check(report, "narrator")
        assert c.status is Status.OK
        assert "4321" in c.detail


def test_stale_daemon_pid_warns() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t:
        (Path(t) / "auto-speech-narrator-daemon.pid").write_text("4321")
        report = _doctor(Path(h), Path(t), daemon_alive=lambda _pid: False).run()
        assert _check(report, "narrator").status is Status.WARN
        assert report.healthy is True  # reclaimed on next start, not a failure


def test_saturated_queue_warns() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t:
        (Path(t) / "auto-speech-narration-depth").write_text("32")
        report = _doctor(Path(h), Path(t), max_queue_depth=32).run()
        assert _check(report, "queue").status is Status.WARN


def test_global_mute_and_solo_scope() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t:
        claude = Path(h) / ".claude"
        claude.mkdir()
        (claude / "auto-speech.disabled").touch()
        (claude / "auto-speech-autoplay-solo").write_text("sess-Z")
        report = _doctor(Path(h), Path(t)).run()
        assert _check(report, "autoplay").status is Status.WARN  # muted
        assert "SOLO" in _check(report, "scope").detail
        assert "sess-Z" in _check(report, "scope").detail


def test_json_shape_and_exit() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t:
        report = _doctor(Path(h), Path(t)).run()
        obj = json.loads(report.to_json())
        assert obj["healthy"] is True
        assert {c["name"] for c in obj["checks"]} >= {"mpv", "venv", "disk", "logs", "narrator", "queue", "scope"}


def main() -> int:
    tests = [
        test_healthy_baseline,
        test_missing_mpv_is_fail,
        test_missing_uv_is_only_warn,
        test_missing_venv_is_fail,
        test_low_disk_is_fail,
        test_oversize_log_warns_but_healthy,
        test_running_daemon_ok,
        test_stale_daemon_pid_warns,
        test_saturated_queue_warns,
        test_global_mute_and_solo_scope,
        test_json_shape_and_exit,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"doctor: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
