"""One hung `say` can never hold the narrator (#m1023-1717-fd7b).

2026-10-22 22:49 a macOS `say` hung for 18 h on one line. The executor's
budget abandoned its worker thread, but nothing killed the process, and
the daemon that owned it logged "shutdown" at its idle exit and then lived
on for 18 h, blocked joining that thread, without its pid file. The doctor
said "not running" while the launchd job's pid was alive.

Covered here, hermetically (a fake `say` and a fake `launchctl` on a
private PATH; no audio is played):
  * each synthesis has a timeout scaled to the line (10 s + 0.15 s a
    character, capped at 120 s);
  * a `say` that sleeps forever is killed with its whole process group,
    retried once, and then raised as a final SynthesisTimeout;
  * a final timeout sets the line aside at once, never queued again;
  * a timed-out line is queued again at most once (timeout_retries 1);
  * the doctor reports the narrator running when the launchd job has a
    live pid, whatever the pid file says, and reports a held stop with its
    waiting lines;
  * a daemon that stops removes the pid file only when it is its own;
  * a daemon whose worker threads are stuck still exits.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT / "tests"))

os.environ.setdefault("AUTO_SPEECH_TMP_ROOT", tempfile.mkdtemp(prefix="auto-speech-hang-"))
os.environ.setdefault("AUTO_SPEECH_MPV_AO", "null")

import apple_say_engine as ase  # noqa: E402
import doctor as doc  # noqa: E402
from health_report import Status  # noqa: E402
from missed_speech import MISSED, REASON_STOPPED, REASON_TIMEOUT, MissedLine, MissedList  # noqa: E402
from tts_executor import SynthesisTimeout  # noqa: E402

GIB = 1024 * 1024 * 1024


# --- a fake `say` ------------------------------------------------------------

def _fake_bin(tmp: Path, name: str, body: str) -> Path:
    d = tmp / "bin"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text("#!/bin/bash\n" + body)
    p.chmod(0o755)
    return d


# Hangs forever, with a child of its own (as `say` has its speech helpers):
# killing only the parent would leave the child behind.
HANG = """
echo $$ >> "$FAKE_DIR/calls"
sleep 1000 &
echo $! >> "$FAKE_DIR/children"
wait
"""

# Hangs on its first call, writes a WAV on the second.
HANG_THEN_WRITE = """
echo $$ >> "$FAKE_DIR/calls"
n=$(wc -l < "$FAKE_DIR/calls")
if [ "$n" -le 1 ]; then sleep 1000 & echo $! >> "$FAKE_DIR/children"; wait; fi
out=""
while [ $# -gt 0 ]; do if [ "$1" = "-o" ]; then out="$2"; fi; shift; done
printf 'RIFF....WAVEfmt ' > "$out"
"""


class _Env:
    def __init__(self, body: str) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fake-say-"))
        self.bin = _fake_bin(self.tmp, "say", body)
        self.saved = {k: os.environ.get(k) for k in ("PATH", "FAKE_DIR", "AUTO_SPEECH_SAY_BOOST")}

    def __enter__(self) -> "_Env":
        os.environ["PATH"] = f"{self.bin}:{self.saved['PATH']}"
        os.environ["FAKE_DIR"] = str(self.tmp)
        return self

    def __exit__(self, *exc) -> None:
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for f in ("children", "calls"):
            for pid in self.lines(f):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

    def lines(self, name: str) -> list[int]:
        p = self.tmp / name
        return [int(x) for x in p.read_text().split()] if p.exists() else []


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # a zombie still answers kill 0; ps shows it as Z
    import subprocess
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(st) and not st.startswith("Z")


def test_the_timeout_scales_with_the_line_and_is_capped() -> None:
    eng = ase.AppleSayEngine()
    assert eng.say_timeout("") == 10.0
    assert abs(eng.say_timeout("x" * 100) - 25.0) < 1e-9
    assert eng.say_timeout("x" * 10_000) == 120.0
    # the whole engine budget covers a try and its one retry
    assert eng.worst_case_seconds("x" * 100) >= 2 * 25.0


def test_a_say_that_hangs_forever_is_killed_retried_once_then_failed() -> None:
    if sys.platform != "darwin":
        return
    with _Env(HANG) as env:
        eng = ase.AppleSayEngine(timeout_base=3.0, timeout_per_char=0.0)
        out = env.tmp / "out.wav"
        t0 = time.monotonic()
        try:
            eng.synthesize("A line that never ends.", None, out)
        except SynthesisTimeout as exc:
            assert exc.final is True, "the engine already retried: the narrator must not queue it again"
        else:
            raise AssertionError("a hung say must raise SynthesisTimeout")
        took = time.monotonic() - t0
        assert took < 12.0, f"two 3 s tries took {took:.1f}s"
        assert len(env.lines("calls")) == 2, f"one try and one retry, got {env.lines('calls')}"
        time.sleep(0.2)
        for pid in env.lines("calls") + env.lines("children"):
            assert not _alive(pid), f"pid {pid} of the hung say's process group is still alive"
        assert not out.exists()


def test_a_say_that_hangs_once_succeeds_on_its_retry() -> None:
    if sys.platform != "darwin":
        return
    with _Env(HANG_THEN_WRITE) as env:
        eng = ase.AppleSayEngine(timeout_base=3.0, timeout_per_char=0.0)
        out = env.tmp / "out.wav"
        eng.synthesize("Second time lucky.", None, out)
        assert out.is_file() and out.stat().st_size > 0
        assert len(env.lines("calls")) == 2
        time.sleep(0.2)
        for pid in env.lines("children"):
            assert not _alive(pid)


# --- the narrator: a final timeout is set aside, not queued again -----------

def _service():
    import test_speech_under_load as sul
    return sul


def test_a_final_timeout_is_set_aside_without_another_retry() -> None:
    sul = _service()
    svc = sul._service(sul._Sink())
    synth = sul._Synth()
    svc._synth = synth

    class _FinalExecutor(sul._Executor):
        def submit(self, fn, *args, timeout=None, **kwargs):
            raise SynthesisTimeout(1.0, final=True)

    svc._tts_executor = _FinalExecutor(synth, fails=0)
    svc._speak("Hung line.", item=sul._item("Hung line."))
    assert svc._tts_queue.empty(), "a final timeout must not be queued again"
    [m] = svc._missed.load()
    assert (m.status, m.reason, m.text) == (MISSED, REASON_TIMEOUT, "Hung line.")


def test_a_timed_out_line_is_queued_again_at_most_once() -> None:
    import narrator_service
    assert narrator_service.DEFAULT_TIMEOUT_RETRIES == 1
    sul = _service()
    svc = sul._service(sul._Sink())
    synth = sul._Synth()
    svc._synth = synth
    svc._tts_executor = sul._Executor(synth, fails=1)
    svc._speak("Once more.", item=sul._item("Once more.", retries=1))
    assert svc._tts_queue.empty()
    [m] = svc._missed.load()
    assert m.reason == REASON_TIMEOUT


def test_the_executor_budget_covers_the_engines_try_and_retry() -> None:
    sul = _service()
    svc = sul._service(sul._Sink())

    class _Ex:
        engine = ase.AppleSayEngine()
    svc._tts_executor = _Ex()
    text = "x" * 400   # say timeout 70 s, worst case 140 s and more
    assert svc._synthesis_timeout(text, svc._profile) >= ase.AppleSayEngine().worst_case_seconds(text)


# --- the doctor ------------------------------------------------------------------

def _doctor(home: Path, tmp: Path, **over):
    kw = {
        "home": home,
        "tmp": tmp,
        "venv_python": Path(sys.executable),
        "mcp_server_script": Path(sys.executable),
        "which": lambda n: f"/usr/bin/{n}",
        "disk_usage": lambda _p: type("D", (), {"free": 5 * GIB})(),
        "daemon_alive": lambda _pid: False,
    }
    kw.update(over)
    return doc.Doctor(**kw)


def _check(report, name):
    return next(c for c in report.checks if c.name == name)


def _fake_launchctl(tmp: Path, pid: int | None) -> Path:
    if pid is None:
        body = 'echo "Could not find service \\"$2\\" in domain for port" >&2; exit 113\n'
    else:
        body = f"""
if [ "$1" = "list" ] && [ "$2" = "dev.auto-speech.narrator" ]; then
cat <<EOF
{{
\t"LimitLoadToSessionType" = "Aqua";
\t"Label" = "dev.auto-speech.narrator";
\t"OnDemand" = false;
\t"LastExitStatus" = 0;
\t"PID" = {pid};
\t"Program" = "/usr/bin/env";
}};
EOF
exit 0
fi
exit 113
"""
    return _fake_bin(tmp, "launchctl", body) / "launchctl"


def test_doctor_says_running_when_the_launchd_job_has_a_live_pid_and_no_pid_file() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t:
        lc = _fake_launchctl(Path(t), os.getpid())
        report = _doctor(Path(h), Path(t), launchctl=str(lc)).run()
        c = _check(report, "narrator")
        assert c.detail.startswith("running"), c.detail
        assert str(os.getpid()) in c.detail


def test_doctor_says_running_when_the_pid_file_is_stale_but_launchd_has_it() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t:
        (Path(t) / "auto-speech-narrator-daemon.pid").write_text("999999")
        lc = _fake_launchctl(Path(t), os.getpid())
        report = _doctor(Path(h), Path(t), launchctl=str(lc)).run()
        c = _check(report, "narrator")
        assert c.detail.startswith("running"), c.detail


def test_doctor_without_a_launchd_job_keeps_the_pid_file_check() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t:
        lc = _fake_launchctl(Path(t), None)
        report = _doctor(Path(h), Path(t), launchctl=str(lc)).run()
        assert _check(report, "narrator").detail.startswith("not running")
        (Path(t) / "auto-speech-narrator-daemon.pid").write_text(str(os.getpid()))
        report = _doctor(Path(h), Path(t), launchctl=str(lc), daemon_alive=lambda p: True).run()
        assert _check(report, "narrator").detail.startswith("running")


def test_doctor_reports_a_held_stop_with_its_waiting_lines() -> None:
    with tempfile.TemporaryDirectory() as h, tempfile.TemporaryDirectory() as t:
        since = time.time() - 120
        (Path(t) / "auto-speech-stopped.json").write_text(json.dumps({"since": since}))
        missed_path = Path(t) / "missed.json"
        ml = MissedList(missed_path)
        for i in range(3):
            ml.upsert(MissedLine(id=f"s{i}", text=f"line {i}", queued_at=since + i, status=MISSED,
                              reason=REASON_STOPPED, missed_at=since + i))
        ml.upsert(MissedLine(id="m1", text="other", queued_at=since, status=MISSED, reason="meeting", missed_at=since))
        report = _doctor(Path(h), Path(t), missed_path=missed_path).run()
        c = _check(report, "stopped")
        when = time.strftime("%H:%M", time.localtime(since))
        assert c.status is Status.WARN
        assert f"stopped since {when}, 3 lines waiting" in c.detail, c.detail


# --- the daemon's own exit ------------------------------------------------------

def test_a_stopping_daemon_removes_only_its_own_pid_file() -> None:
    import narrator_service as ns
    pf = Path(tempfile.mkdtemp(prefix="pidf-")) / "narrator.pid"
    pf.write_text("12345")
    ns._release_pid_file(pf, 999)
    assert pf.read_text() == "12345", "another daemon's pid file must stay"
    ns._release_pid_file(pf, 12345)
    assert not pf.exists()


def test_stuck_worker_threads_are_named_so_the_daemon_can_exit_past_them() -> None:
    import narrator_service as ns
    gate = threading.Event()
    t = threading.Thread(target=gate.wait, name="tts-worker_0", daemon=False)
    t.start()
    try:
        assert "tts-worker_0" in [x.name for x in ns._stuck_threads()]
    finally:
        gate.set()
        t.join()
    assert "tts-worker_0" not in [x.name for x in ns._stuck_threads()]


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"FAILED {name}: {exc!r}")
            traceback.print_exc()
    print(f"say_hang: {passed} tests passed" + (f", {failed} failed" if failed else ""))
    sys.exit(1 if failed else 0)
