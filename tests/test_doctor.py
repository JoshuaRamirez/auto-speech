"""Unit tests for the doctor health probe.

Every system probe is injected (which / disk_usage / daemon_alive / temp
home+tmp / venv python) so the checks run hermetically. Covers the healthy
baseline, each FAIL condition (mpv, venv, disk), the WARN conditions
(oversize log, stale daemon, saturated queue, global mute), the running-
daemon OK path, scope reporting, exit codes, and JSON shape.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

import doctor as doc
from health_report import Status

GIB = 1024 * 1024 * 1024


def _which_all(name):  # both binaries present
    return f"/usr/bin/{name}"


def _disk_free(n):
    return lambda _p: SimpleNamespace(free=n)


def _doctor(home, tmp, **over):
    kw = {
        "home": home,
        "tmp": tmp,
        "venv_python": Path(sys.executable),  # a real executable → venv OK
        "which": _which_all,
        "disk_usage": _disk_free(5 * GIB),
        "daemon_alive": lambda _pid: False,
    }
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
        assert _check(report, "jq").status is Status.OK
        assert _check(report, "venv").status is Status.OK
        assert _check(report, "disk").status is Status.OK
        assert _check(report, "narrator").status is Status.OK  # not running = OK
        assert _check(report, "scope").status is Status.OK
        assert "enrolled" in _check(report, "scope").detail
        assert "opt-IN" in _check(report, "autoplay").detail


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


def test_missing_jq_is_only_warn() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t:
        report = _doctor(Path(h), Path(t), which=lambda n: None if n == "jq" else "/usr/bin/x").run()
        jq = _check(report, "jq")
        assert jq.status is Status.WARN
        assert "session id" in jq.detail
        assert report.healthy is True  # jq missing degrades autoplay, not all audio


def test_autoplay_reports_opt_in_not_enabled_default() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t:
        report = _doctor(Path(h), Path(t)).run()
        autoplay = _check(report, "autoplay")
        assert autoplay.status is Status.OK
        assert "opt-IN" in autoplay.detail
        assert "enabled (default)" not in autoplay.detail
        scope = _check(report, "scope")
        assert "every enrolled session reads" in scope.detail
        assert "every session reads" not in scope.detail.replace("every enrolled session reads", "")


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


def test_bad_config_warns_but_healthy() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t, \
            tempfile.TemporaryDirectory() as c:
        (Path(c) / "autoplay.toml").write_text('[autoplay]\nmode = "nope"\n', encoding="utf-8")
        report = _doctor(Path(h), Path(t), config_dir=Path(c)).run()
        cfg = _check(report, "config")
        assert cfg.status is Status.WARN
        assert "mode must be one of" in cfg.detail
        assert report.healthy is True  # a config typo degrades, doesn't break


def test_clean_config_ok() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t, \
            tempfile.TemporaryDirectory() as c:
        report = _doctor(Path(h), Path(t), config_dir=Path(c)).run()
        assert _check(report, "config").status is Status.OK


def test_updates_in_sync_ok() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t, \
            tempfile.TemporaryDirectory() as p:
        lock = Path(p) / "uv.lock"
        stamp = Path(p) / ".synced"
        lock.write_bytes(b"locked")
        import self_update
        self_update.record_sync(lock, stamp)  # stamp matches lock
        report = _doctor(Path(h), Path(t), lock_path=lock, stamp_path=stamp).run()
        assert _check(report, "updates").status is Status.OK


def test_updates_out_of_sync_warns() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t, \
            tempfile.TemporaryDirectory() as p:
        lock = Path(p) / "uv.lock"
        stamp = Path(p) / ".synced"
        lock.write_bytes(b"locked")  # no stamp written → out of sync
        report = _doctor(Path(h), Path(t), lock_path=lock, stamp_path=stamp).run()
        c = _check(report, "updates")
        assert c.status is Status.WARN
        assert "/auto-speech-update" in c.detail
        assert report.healthy is True  # out-of-sync degrades, doesn't break


def test_main_survives_non_table_narrator_section() -> None:
    """A valid-TOML non-table `narrator` must not traceback out of main().

    load_config() does section.get(...) on whatever raw["narrator"] is; a
    string/int/true raises AttributeError. Doctor.main() has to keep going
    so _check_config can report "[narrator] is not a table".
    """
    with tempfile.TemporaryDirectory() as h:
        home = Path(h)
        cfg_dir = home / ".config" / "auto-speech"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "narrator.toml").write_text('narrator = "bad"\n', encoding="utf-8")
        saved_home = os.environ.get("HOME")
        saved_override = os.environ.pop("AUTO_SPEECH_NARRATOR_CONFIG", None)
        os.environ["HOME"] = str(home)
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = doc.main(["doctor", "--json"])
            obj = json.loads(buf.getvalue())
            assert isinstance(rc, int)
            assert "checks" in obj
            cfg = next(c for c in obj["checks"] if c["name"] == "config")
            assert "is not a table" in cfg["detail"]
        finally:
            if saved_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = saved_home
            if saved_override is not None:
                os.environ["AUTO_SPEECH_NARRATOR_CONFIG"] = saved_override


def test_json_shape_and_exit() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t:
        report = _doctor(Path(h), Path(t)).run()
        obj = json.loads(report.to_json())
        assert obj["healthy"] is True
        assert {c["name"] for c in obj["checks"]} >= {
            "mpv", "uv", "jq", "venv", "disk", "logs", "narrator", "queue", "scope", "autoplay",
        }


def main() -> int:
    tests = [
        test_healthy_baseline,
        test_missing_mpv_is_fail,
        test_missing_uv_is_only_warn,
        test_missing_jq_is_only_warn,
        test_autoplay_reports_opt_in_not_enabled_default,
        test_missing_venv_is_fail,
        test_low_disk_is_fail,
        test_oversize_log_warns_but_healthy,
        test_running_daemon_ok,
        test_stale_daemon_pid_warns,
        test_saturated_queue_warns,
        test_global_mute_and_solo_scope,
        test_bad_config_warns_but_healthy,
        test_clean_config_ok,
        test_updates_in_sync_ok,
        test_updates_out_of_sync_warns,
        test_main_survives_non_table_narrator_section,
        test_json_shape_and_exit,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"doctor: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
