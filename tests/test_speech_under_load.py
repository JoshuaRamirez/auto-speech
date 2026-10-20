"""Speech holds up under load (ADR-019): priority, budgets that stretch with
the load, a retry that waits for the load to fall, a play that ends only on
a stall, never on the wall clock while the clip advances; and one short,
niced load test with the real `say` and mpv (silent: --ao=null)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from queue import Queue
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "scripts" / "python"))

os.environ.setdefault("AUTO_SPEECH_TMP_ROOT", tempfile.mkdtemp(prefix="auto-speech-load-"))
os.environ.setdefault("AUTO_SPEECH_MPV_AO", "null")

import narrator_service  # noqa: E402
import speech_priority  # noqa: E402
from missed_speech import MISSED, REASON_TIMEOUT, MissedList  # noqa: E402
from native_audio_sink import NativeAudioSink, PlaybackError  # noqa: E402
from priority_arbiter import QueueItem  # noqa: E402
from speech_priority import LoadWatch, PriorityReport, boosted_argv, raise_own_priority  # noqa: E402
from tts_executor import SynthesisTimeout  # noqa: E402
from voice_profile import VoiceProfile  # noqa: E402

PASSED = 0


def check(name: str, fn) -> None:
    global PASSED
    fn()
    PASSED += 1


# --- helpers -------------------------------------------------------------------

class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def _watch(loads: list[float], cores: int = 4) -> tuple[LoadWatch, _Clock]:
    clock = _Clock()
    seq = iter(loads)
    last = [loads[-1]]

    def load() -> float:
        try:
            last[0] = next(seq)
        except StopIteration:
            pass
        return last[0]

    return LoadWatch(cores=cores, load_fn=load, clock=clock, sleep=clock.sleep), clock


def _service(sink, *, config: dict | None = None, watch: LoadWatch | None = None):
    svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
    svc._config = dict(config or {})
    svc._sink = sink
    svc._profile = VoiceProfile("test_voice", 1.0, 15.0, "2026-10-03T00:00:00Z", 100)
    svc._engine = None
    svc._state_lock = threading.Lock()
    svc._engine_state = "IDLE"
    svc._interrupted = False
    svc._preempted = False
    svc._missed = MissedList(Path(tempfile.mkdtemp(prefix="auto-speech-missed-")) / "missed.json")
    gate = mock.MagicMock()
    gate.settings.return_value = mock.MagicMock(max_wait_seconds=180.0, meeting_hold=False)
    gate.assess.return_value = mock.MagicMock(reason="", meeting_names=lambda: ())
    svc._yield_gate = gate
    svc._meeting_active = None
    svc._meeting_checked_at = 0.0
    svc._last_hold_ended_at = None
    svc._sink_takes_hold_kwargs = None
    svc._tts_queue = Queue()
    svc._load_watch = watch or LoadWatch(cores=4, load_fn=lambda: 0.0, clock=lambda: 0.0, sleep=lambda s: None)
    svc._progress = lambda *a, **k: None
    svc._speaker = None
    svc._speaker_spoken = lambda sid: None
    svc._speaker_plan = lambda text, sid: None
    return svc


def _item(text: str, **kw) -> QueueItem:
    kw.setdefault("priority", 4)
    return QueueItem(payload=text, **kw)


class _Sink:
    """Plays, or raises the scripted PlaybackError once, then plays."""

    def __init__(self, errors: list[str] = ()) -> None:
        self.errors = list(errors)
        self.played: list[Path] = []
        self.last_hold = None

    def play(self, wav_path, timeout=None, *, hold=True, already_waited=0.0, on_hold=None) -> None:
        if self.errors:
            raise PlaybackError(self.errors.pop(0))
        self.played.append(Path(wav_path))


class _Executor:
    """A TTS executor whose first `fails` submissions time out."""

    def __init__(self, synth, fails: int) -> None:
        self.synth = synth
        self.fails = fails
        self.budgets: list[float | None] = []

    def ensure_loaded(self) -> None:
        return None

    def submit(self, fn, *args, timeout=None, **kwargs):
        self.budgets.append(timeout)
        if self.fails > 0:
            self.fails -= 1
            raise SynthesisTimeout(float(timeout or 0.0))
        return fn(*args, **kwargs)


class _Synth:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def synthesize_one(self, text, profile, out_path) -> bool:
        self.calls.append(text)
        _silent_wav(Path(out_path), 0.2)
        return True


def _silent_wav(path: Path, seconds: float, rate: int = 24000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


# --- LoadWatch -----------------------------------------------------------------

def test_scale_is_the_load_per_core_between_one_and_the_cap() -> None:
    w, _ = _watch([2.0, 12.0, 40.0], cores=4)
    assert w.scale(4.0) == 1.0          # 0.5 per core: no stretch
    assert w.scale(4.0) == 3.0          # 3 per core
    assert w.scale(4.0) == 4.0          # 10 per core, capped
    w2, _ = _watch([40.0], cores=4)
    assert w2.scale(0) == 1.0           # cap off


def test_wait_until_below_returns_clear_when_the_load_falls() -> None:
    w, clock = _watch([16.0, 16.0, 4.0], cores=4)
    assert w.wait_until_below(2.0, deadline_seconds=60, poll_seconds=2.0) == "clear"
    assert clock.t == 4.0               # two polls under the threshold check


def test_wait_until_below_gives_up_at_the_deadline() -> None:
    w, clock = _watch([64.0], cores=4)
    assert w.wait_until_below(2.0, deadline_seconds=10, poll_seconds=2.0) == "deadline"
    assert clock.t >= 10.0


def test_wait_until_below_stops_on_request_and_never_waits_when_off() -> None:
    w, clock = _watch([64.0], cores=4)
    calls = [0]

    def stop() -> bool:
        calls[0] += 1
        return calls[0] >= 3

    assert w.wait_until_below(2.0, deadline_seconds=100, poll_seconds=1.0, should_stop=stop) == "stopped"
    assert w.wait_until_below(0.0, deadline_seconds=100) == "clear"


# --- priority ------------------------------------------------------------------

def test_boosted_argv_wraps_in_taskpolicy_tier_zero_and_can_be_turned_off() -> None:
    argv = ["say", "-o", "x.wav", "--", "hi"]
    with mock.patch.dict(os.environ, {"AUTO_SPEECH_QOS_BOOST": "0"}):
        assert boosted_argv(argv) == argv
    with mock.patch.dict(os.environ, {"AUTO_SPEECH_QOS_BOOST": "1"}):
        out = boosted_argv(argv)
        if speech_priority.taskpolicy_path() is None:
            assert out == argv
        else:
            assert out[:5] == [speech_priority.taskpolicy_path(), "-t", "0", "-l", "0"]
            assert out[5:] == argv


def test_raise_own_priority_reports_and_never_raises() -> None:
    report = raise_own_priority()
    assert isinstance(report, PriorityReport)
    assert report.nice >= 0
    text = report.describe()
    assert "nice" in text and "clamp" in text and "QoS" in text
    if sys.platform == "darwin":
        assert report.bg_cleared, text
        assert report.thread_qos, text


# --- budgets stretch with the load --------------------------------------------

def test_synthesis_and_play_budgets_stretch_with_the_load() -> None:
    quiet, _ = _watch([0.0], cores=4)
    busy, _ = _watch([12.0], cores=4)   # 3 per core
    svc = _service(_Sink(), watch=quiet, config={"synthesis_timeout_factor": 3.0, "play_timeout_margin_seconds": 15.0})
    base = svc._synthesis_timeout("x" * 150, svc._profile)   # 10 s spoken × 3 = 30 (the floor)
    svc._load_watch = busy
    assert svc._synthesis_timeout("x" * 150, svc._profile) == base * 3.0
    wav = _silent_wav(Path(tempfile.mkdtemp()) / "c.wav", 2.0)
    svc._load_watch = quiet
    assert svc._play_timeout(wav) == 2.0 * 2 + 15.0
    svc._load_watch = busy
    assert svc._play_timeout(wav) == (2.0 * 2 + 15.0) * 3.0
    svc._config["load_timeout_scale_cap"] = 0
    assert svc._play_timeout(wav) == 2.0 * 2 + 15.0


# --- retries -------------------------------------------------------------------

def test_a_synthesis_timeout_queues_the_line_again_not_on_the_missed_list() -> None:
    svc = _service(_Sink())
    synth = _Synth()
    svc._synth = synth
    svc._tts_executor = _Executor(synth, fails=1)
    svc._speak("The build is green.", item=_item("The build is green.", session_id="s1"))
    assert synth.calls == []
    again = svc._tts_queue.get_nowait()
    assert (again.payload, again.retries, again.line_text, again.priority, again.session_id) == (
        "The build is green.", 1, "The build is green.", 4, "s1")
    assert svc._missed.load() == []


def test_retries_spent_sets_the_line_aside_as_timed_out() -> None:
    svc = _service(_Sink(), config={"timeout_retries": 2})
    synth = _Synth()
    svc._synth = synth
    svc._tts_executor = _Executor(synth, fails=1)
    svc._speak("Last try.", item=_item("Last try.", retries=2))
    assert svc._tts_queue.empty()
    [m] = svc._missed.load()
    assert (m.status, m.reason, m.text) == (MISSED, REASON_TIMEOUT, "Last try.")


def test_a_retried_synthesis_plays_after_the_load_falls() -> None:
    svc = _service(_Sink())
    synth = _Synth()
    svc._synth = synth
    svc._tts_executor = _Executor(synth, fails=1)
    svc._speak("Again.", item=_item("Again."))
    again = svc._tts_queue.get_nowait()
    svc._speak(again.payload, item=again)
    assert synth.calls == ["Again."]
    assert len(svc._sink.played) == 1


def test_a_cut_off_play_queues_the_whole_wav_again_and_plays_it_whole() -> None:
    sink = _Sink(errors=["Playback stalled for 15s at 3.2s: x.wav"])
    svc = _service(sink)
    retry_dir = Path(tempfile.mkdtemp(prefix="auto-speech-retry-"))
    with mock.patch.object(narrator_service, "RETRY_DIR", retry_dir):
        wav = _silent_wav(Path(tempfile.mkdtemp()) / "line.wav", 0.3)
        heard = svc._play_item(wav, _item("A long line, cut off.", session_id="s9"), text="A long line, cut off.")
        assert heard is False
        again = svc._tts_queue.get_nowait()
        kept = Path(again.payload)
        assert kept.parent == retry_dir and kept.suffix == ".wav" and kept.stat().st_size == wav.stat().st_size
        assert (again.retries, again.line_text, again.session_id) == (1, "A long line, cut off.", "s9")
        assert svc._missed.load() == []
        # The retry plays the kept WAV whole, from the start, and the WAV then goes.
        assert svc._play_cached(kept, again, text=again.line_text) is True
        assert sink.played == [kept]
        svc._drop_retry_wav(kept, again)
        assert not kept.exists()


def test_a_wall_clock_timeout_is_retried_the_same_way() -> None:
    sink = _Sink(errors=["Playback timed out after 23.0s: x.wav"])
    svc = _service(sink)
    with mock.patch.object(narrator_service, "RETRY_DIR", Path(tempfile.mkdtemp())):
        wav = _silent_wav(Path(tempfile.mkdtemp()) / "line.wav", 0.3)
        assert svc._play_item(wav, _item("Timed out."), text="Timed out.") is False
        assert svc._tts_queue.get_nowait().retries == 1


def test_await_load_goes_ahead_when_clear_and_requeues_when_preempted() -> None:
    busy_then_clear, _ = _watch([16.0, 4.0], cores=4)
    svc = _service(_Sink(), watch=busy_then_clear, config={"retry_load_per_core": 2.0, "retry_wait_seconds": 30})
    assert svc._await_load(_item("x", retries=1)) is True
    stuck, _ = _watch([64.0], cores=4)
    svc._load_watch = stuck
    svc._preempted = True
    item = _item("y", retries=2)
    assert svc._await_load(item) is False
    assert svc._tts_queue.get_nowait() is item


# --- the sink's stall watch ---------------------------------------------------

def test_the_stall_watch_adds_an_ipc_socket_and_reads_progress() -> None:
    with mock.patch.dict(os.environ, {"AUTO_SPEECH_PLAY_STALL_SECONDS": "7"}):
        sink = NativeAudioSink(sound_yield=False)
    assert sink.stall_seconds == 7.0
    sink._ipc_path = sink._new_ipc_path()
    argv = sink.mpv_argv("/usr/bin/mpv", "a.wav")
    assert any(a.startswith("--input-ipc-server=") for a in argv)
    sink.stall_seconds = 0
    assert sink._new_ipc_path() is None
    sink._ipc_path = None
    assert sink._read_progress() is None


def test_a_play_still_advancing_is_never_cut_off_by_the_wall_clock() -> None:
    sink = NativeAudioSink(sound_yield=False)
    sink.stall_seconds = 0.5
    proc = mock.MagicMock()
    calls = [0]

    def communicate(timeout=None):
        calls[0] += 1
        if calls[0] <= 6:
            raise subprocess.TimeoutExpired(cmd="mpv", timeout=timeout)
        return (b"", b"")

    proc.communicate.side_effect = communicate
    proc.returncode = 0
    positions = iter([0.5, 1.0, 1.5, 2.0, 2.5, 3.0])
    with mock.patch.object(sink, "_read_progress", side_effect=lambda: (next(positions), False)):
        out = sink._wait_for_exit(proc, Path("a.wav"), timeout=0.01)   # a wall clock far shorter than the clip
    assert out == (b"", b"")
    assert sink.last_position == 3.0
    proc.terminate.assert_not_called()


def test_a_stalled_play_ends_after_the_stall_window_and_is_named_so() -> None:
    sink = NativeAudioSink(sound_yield=False)
    sink.stall_seconds = 0.3
    proc = mock.MagicMock()
    proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="mpv", timeout=1.0)
    proc.poll.return_value = None
    sink._proc = proc
    with mock.patch.object(sink, "_read_progress", return_value=(4.2, False)):
        try:
            sink._wait_for_exit(proc, Path("a.wav"), timeout=None)
        except PlaybackError as exc:
            assert "stalled" in str(exc) and "4.2s" in str(exc)
        else:
            raise AssertionError("a stalled play must end")
    proc.terminate.assert_called()


def test_a_paused_play_is_not_a_stall() -> None:
    sink = NativeAudioSink(sound_yield=False)
    sink.stall_seconds = 0.2
    proc = mock.MagicMock()
    calls = [0]

    def communicate(timeout=None):
        calls[0] += 1
        if calls[0] <= 5:
            raise subprocess.TimeoutExpired(cmd="mpv", timeout=timeout)
        return (b"", b"")

    proc.communicate.side_effect = communicate
    with mock.patch.object(sink, "_read_progress", return_value=(1.0, True)):
        assert sink._wait_for_exit(proc, Path("a.wav"), timeout=None) == (b"", b"")
    proc.terminate.assert_not_called()


def test_without_progress_the_wall_clock_still_ends_a_hung_play() -> None:
    sink = NativeAudioSink(sound_yield=False)
    proc = mock.MagicMock()
    proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="mpv", timeout=1.0)
    with mock.patch.object(sink, "_read_progress", return_value=None):
        try:
            sink._wait_for_exit(proc, Path("a.wav"), timeout=0.2)
        except PlaybackError as exc:
            assert "timed out after 0.2s" in str(exc)
        else:
            raise AssertionError("a hung play with no progress must end on the wall clock")


# --- the load test: real say and mpv, short and niced, silent ------------------

def _burners(n: int, seconds: float) -> list[subprocess.Popen]:
    code = f"import time\nend=time.time()+{seconds}\nwhile time.time()<end: pass\n"
    return [
        subprocess.Popen(["nice", "-n", "19", sys.executable, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(n)
    ]


def test_a_long_clip_plays_to_its_end_under_load() -> None:
    """Twice the cores in niced burners for the test's length; a real `say`
    synthesis of a long line, then a real mpv play (--ao=null) with a 5 s
    stall window and a wall clock half the clip's length: the clip plays
    to its end, the play's elapsed time is at least the clip's length."""
    if sys.platform != "darwin" or not shutil.which("say") or not shutil.which("mpv"):
        return
    from apple_say_engine import AppleSayEngine

    cores = os.cpu_count() or 4
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-loadtest-"))
    wav = tmp / "long.wav"
    text = ("Speech holds up under load. " * 6).strip()   # about twelve seconds spoken
    burners = _burners(cores * 2, 25.0)
    try:
        time.sleep(1.0)
        t0 = time.monotonic()
        AppleSayEngine().synthesize(text, voice_profile=None, out_path=wav)
        synth_s = time.monotonic() - t0
        assert wav.is_file() and wav.stat().st_size > 1000, "say produced no audio under load"
        with wave.open(str(wav)) as w:
            duration = w.getnframes() / float(w.getframerate())
        assert duration >= 5.0, f"the clip is only {duration:.1f}s"

        with mock.patch.dict(os.environ, {"AUTO_SPEECH_MPV_AO": "null"}):
            sink = NativeAudioSink(sound_yield=False)
        sink.stall_seconds = 5.0
        t1 = time.monotonic()
        sink.play(wav, timeout=duration * 0.5)   # the wall clock alone would have cut it off halfway
        played_s = time.monotonic() - t1
        assert played_s >= duration * 0.9, f"the clip ({duration:.1f}s) ended after {played_s:.1f}s"
        assert sink.last_position is not None and sink.last_position >= duration * 0.5, (
            f"mpv's position was not read ({sink.last_position}); the stall watch did not run")
        print(f"load test: synthesis {synth_s:.1f}s, clip {duration:.1f}s, played {played_s:.1f}s, "
              f"load {os.getloadavg()[0]:.0f} on {cores} cores")
    finally:
        for b in burners:
            b.kill()
        for b in burners:
            b.wait()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            PASSED += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"FAILED {name}: {exc!r}")
            traceback.print_exc()
    print(f"speech_under_load: {PASSED} tests passed" + (f", {failed} failed" if failed else ""))
    sys.exit(1 if failed else 0)
