"""The narrator cannot hang on one line (his 2026-10-19 five-hour silence).

Hermetic, silent: no mpv, no model, no sound. Covers
  1. TTSExecutor.submit(timeout=): a stuck synthesis raises SynthesisTimeout
     within its budget and the next call is served at once by a fresh worker;
  2. the narrator sizes each budget to the line (synthesis) and the clip (play);
  3. a stuck synthesis is set aside on the missed list and the worker moves on;
  4. a play past its budget is set aside, with the budget handed to the sink;
  5. the worker stamps progress for the doctor at every step.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from quiet_audio import ensure_quiet  # noqa: E402

ensure_quiet()  # the suite never plays sound unless AUTO_SPEECH_TEST_REAL_AUDIO=1

_TMP = tempfile.mkdtemp(prefix="auto-speech-hang-")
os.environ["AUTO_SPEECH_TMP_ROOT"] = _TMP
os.environ["AUTO_SPEECH_MISSED_FILE"] = str(Path(_TMP) / "missed.json")

import narrator_service  # noqa: E402
from missed_speech import REASON_TIMEOUT, MissedList  # noqa: E402
from native_audio_sink import PlaybackError  # noqa: E402
from tts_executor import SynthesisTimeout, TTSExecutor  # noqa: E402
from voice_profile import VoiceProfile  # noqa: E402

PROFILE = VoiceProfile("test_voice", 1.0, 15.0, "2026-10-03T00:00:00Z", 100)


def _silent_wav(seconds: float) -> Path:
    path = Path(tempfile.mkstemp(prefix="hang_", suffix=".wav", dir=_TMP)[1])
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x00\x00" * int(8000 * seconds))
    return path


class _StuckSynth:
    """synthesize_one never returns until `release` is set."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.calls = 0

    def synthesize_one(self, text, profile, out_path) -> bool:
        self.calls += 1
        self.release.wait(30)
        return False


class _QuickSynth:
    def __init__(self, seconds: float = 0.5) -> None:
        self.seconds = seconds

    def synthesize_one(self, text, profile, out_path) -> bool:
        Path(out_path).write_bytes(_silent_wav(self.seconds).read_bytes())
        return True


class _RecordingSink:
    """A sink that takes the hold keywords and the play budget, records them,
    and plays nothing. `raise_timeout` makes it behave like mpv run past the
    budget (the real sink kills mpv and raises PlaybackError 'timed out')."""

    def __init__(self, raise_timeout: bool = False) -> None:
        self.played: list[tuple[Path, float | None]] = []
        self.raise_timeout = raise_timeout
        self.last_hold = None
        self.hold_status = {}

    def play(self, path, timeout=None, *, hold=True, already_waited=0.0, on_hold=None) -> None:
        self.played.append((Path(path), timeout))
        if self.raise_timeout:
            raise PlaybackError(f"Playback timed out after {timeout}s: {path}")

    def interrupt(self) -> None:
        pass


def _service(sink, synth, config=None):
    svc = narrator_service.NarratorService.__new__(narrator_service.NarratorService)
    svc._config = {"synthesis_timeout_factor": 3.0, "play_timeout_margin_seconds": 15.0, **(config or {})}
    svc._sink = sink
    svc._profile = PROFILE
    svc._engine = None
    svc._synth = synth
    svc._tts_executor = TTSExecutor(engine=object(), synth=synth)
    svc._tts_initialized = True
    svc._missed = MissedList(Path(_TMP) / "missed.json")
    return svc


def _progress() -> dict:
    return json.loads((Path(_TMP) / "auto-speech-narration-progress").read_text())


# 1. the executor's budget --------------------------------------------------

def test_executor_timeout_abandons_the_stuck_worker_and_serves_the_next_call() -> None:
    ex = TTSExecutor(engine=object(), synth=object())
    stuck = threading.Event()
    t0 = time.monotonic()
    try:
        ex.submit(stuck.wait, 30, timeout=0.3)
        raise AssertionError("a stuck call must raise SynthesisTimeout")
    except SynthesisTimeout as exc:
        assert exc.seconds == 0.3
    assert time.monotonic() - t0 < 2.0, "the timeout is the budget, not the stuck call's length"
    # The next call is not queued behind the stuck one.
    t1 = time.monotonic()
    assert ex.submit(lambda: "served") == "served"
    assert time.monotonic() - t1 < 1.0
    assert ex.abandoned == 1
    stuck.set()
    ex.shutdown(wait=False)


def test_executor_without_timeout_waits_as_before() -> None:
    ex = TTSExecutor(engine=object(), synth=object())
    assert ex.submit(lambda: 7) == 7
    assert ex.abandoned == 0
    ex.shutdown()


# 2. the budgets' size ------------------------------------------------------

def test_synthesis_budget_is_sized_to_the_line_with_a_floor() -> None:
    svc = _service(_RecordingSink(), _QuickSynth())
    assert svc._synthesis_timeout("short", PROFILE) == narrator_service.SYNTHESIS_TIMEOUT_FLOOR_SECONDS
    long_line = "x" * 1500  # 100 s spoken at 15 cps
    assert abs(svc._synthesis_timeout(long_line, PROFILE) - 300.0) < 1e-6
    svc._config["synthesis_timeout_factor"] = 0
    assert svc._synthesis_timeout(long_line, PROFILE) is None


def test_play_budget_is_the_clip_length_plus_the_margin() -> None:
    svc = _service(_RecordingSink(), _QuickSynth())
    clip = _silent_wav(2.0)
    assert abs(svc._play_timeout(clip) - 17.0) < 0.05
    svc._config["play_timeout_margin_seconds"] = 0
    assert svc._play_timeout(clip) is None
    assert svc._play_timeout(Path(_TMP) / "missing.wav") is None


# 3. a stuck synthesis is abandoned and the line set aside -------------------

def test_stuck_synthesis_is_set_aside_and_the_worker_moves_on() -> None:
    synth = _StuckSynth()
    sink = _RecordingSink()
    svc = _service(sink, synth)
    svc._synthesis_timeout = lambda text, profile: 0.4  # the budget, small for the test
    t0 = time.monotonic()
    svc._speak("a line the model never finishes")
    took = time.monotonic() - t0
    assert took < 3.0, f"_speak must return within the budget, took {took:.1f}s"
    assert sink.played == [], "nothing played"
    lines = svc._missed.load()
    assert any(getattr(line, "reason", "") == REASON_TIMEOUT for line in lines), [vars(m) for m in lines]
    assert svc._tts_executor.abandoned == 1
    # The next line is served by the fresh worker, not queued behind the stuck one.
    svc._synth = _QuickSynth(0.2)
    svc._tts_executor = TTSExecutor(engine=object(), synth=svc._synth)
    svc._synthesis_timeout = lambda text, profile: 5.0
    t1 = time.monotonic()
    svc._speak("the next line")
    assert len(sink.played) == 1 and time.monotonic() - t1 < 3.0
    synth.release.set()


# 4. a play past its budget --------------------------------------------------

def test_play_budget_reaches_the_sink_and_a_timeout_is_set_aside() -> None:
    sink = _RecordingSink(raise_timeout=False)
    svc = _service(sink, _QuickSynth(0.5))
    svc._speak("a short line")
    assert len(sink.played) == 1
    _path, budget = sink.played[0]
    assert budget is not None and abs(budget - 15.5) < 0.1, budget

    sink = _RecordingSink(raise_timeout=True)
    svc = _service(sink, _QuickSynth(0.5))
    svc._speak("a line mpv never finishes")
    lines = svc._missed.load()
    assert any(getattr(line, "reason", "") == REASON_TIMEOUT and "mpv never" in line.text for line in lines)


def test_a_sink_without_the_budget_keyword_still_plays() -> None:
    class _PlainSink:
        def __init__(self) -> None:
            self.played = []

        def play(self, path) -> None:
            self.played.append(Path(path))

    sink = _PlainSink()
    svc = _service(sink, _QuickSynth(0.2))
    svc._speak("plain")
    assert len(sink.played) == 1


# 5. progress stamps -----------------------------------------------------------

def test_progress_is_stamped_through_a_speak() -> None:
    sink = _RecordingSink()
    svc = _service(sink, _QuickSynth(1.0))
    import queue

    svc._tts_queue = queue.Queue()
    svc._tts_queue.put("x")  # one more waiting behind this line
    svc._speak("stamp me")
    stamp = _progress()
    assert stamp["event"] == "playing", stamp
    assert stamp["depth"] == 1
    assert stamp["expected_done_at"] is not None and stamp["expected_done_at"] > stamp["at"]
    assert stamp["expected_done_at"] - stamp["at"] >= 16.0  # clip 1 s + margin 15 s, plus the hold's longest wait


def main() -> int:
    tests = [
        test_executor_timeout_abandons_the_stuck_worker_and_serves_the_next_call,
        test_executor_without_timeout_waits_as_before,
        test_synthesis_budget_is_sized_to_the_line_with_a_floor,
        test_play_budget_is_the_clip_length_plus_the_margin,
        test_stuck_synthesis_is_set_aside_and_the_worker_moves_on,
        test_play_budget_reaches_the_sink_and_a_timeout_is_set_aside,
        test_a_sink_without_the_budget_keyword_still_plays,
        test_progress_is_stamped_through_a_speak,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {exc!r}")
    print("ALL PASSED" if not failed else f"{failed} FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
