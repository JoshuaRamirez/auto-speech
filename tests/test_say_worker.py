"""Unit tests for SayWorker (the detached half of the MCP `speak` tool).

Stubbed runner, probes, sleep and a temp queue dir: no audio, mpv, or
real FIFO state is touched. Covers:
  - speaks the file's text verbatim via the speak wrapper, then releases
    its ticket and removes the text file
  - waits while mpv is busy, then speaks
  - global mute: no speak, text file still removed
  - blank / missing text file: no speak
  - speak failure (non-zero exit or OSError) still returns 0
  - run() always returns 0
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

import say_worker as swmod  # noqa: E402
from autoplay_gate import AutoplayGate  # noqa: E402
from playback_fifo import PlaybackFifo  # noqa: E402
from say_worker import SayWorker  # noqa: E402


def _text_file(text: str) -> Path:
    fd, path = tempfile.mkstemp(prefix="auto-speech-say-test-", suffix=".txt")
    Path(path).write_text(text, encoding="utf-8")
    return Path(path)


def _worker(text_path: Path, *, muted=False, mpv_answers=(False,), runner=None):
    home = Path(tempfile.mkdtemp(prefix="auto-speech-say-home-"))
    if muted:
        (home / ".claude").mkdir()
        (home / ".claude" / "auto-speech.disabled").touch()
    qdir = Path(tempfile.mkdtemp(prefix="auto-speech-say-q-"))
    calls: list[tuple[list[str], str]] = []
    answers = list(mpv_answers)

    def mpv_running() -> bool:
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def default_runner(argv, *, stdin_text):
        calls.append((argv, stdin_text))
        return 0

    sleeps: list[float] = []
    w = SayWorker(
        str(text_path),
        gate=AutoplayGate(home=home),
        fifo=PlaybackFifo(queue_dir=qdir),
        mpv_running=mpv_running,
        narrator_depth=lambda: 0,
        queue_wait_max=5,
        runner=runner or default_runner,
        sleep=sleeps.append,
        log=lambda msg: None,
    )
    return w, calls, qdir, sleeps


def test_speaks_verbatim_and_cleans_up() -> None:
    text = "Say *exactly* this.\n"
    p = _text_file(text)
    w, calls, qdir, _ = _worker(p)
    assert w.run() == 0
    assert calls == [(["bash", str(swmod.SPEAK)], text)]
    assert not p.exists()
    assert list(qdir.iterdir()) == []  # ticket released


def test_waits_for_mpv_then_speaks() -> None:
    p = _text_file("queued behind a playback")
    w, calls, _, sleeps = _worker(p, mpv_answers=(True, True, False))
    assert w.run() == 0
    assert len(sleeps) == 2
    assert len(calls) == 1


def test_global_mute_skips_speech() -> None:
    p = _text_file("muted words")
    w, calls, qdir, _ = _worker(p, muted=True)
    assert w.run() == 0
    assert calls == []
    assert not p.exists()
    assert list(qdir.iterdir()) == []  # never enqueued


def test_blank_or_missing_text_skips_speech() -> None:
    p = _text_file("  \n\t")
    w, calls, _, _ = _worker(p)
    assert w.run() == 0 and calls == []
    missing = Path(tempfile.gettempdir()) / "auto-speech-say-test-does-not-exist.txt"
    w, calls, _, _ = _worker(missing)
    assert w.run() == 0 and calls == []


def test_speak_failure_still_returns_zero() -> None:
    def failing(argv, *, stdin_text):
        return 5

    def raising(argv, *, stdin_text):
        raise OSError("bash missing")

    for runner in (failing, raising):
        p = _text_file("this will fail")
        w, _, qdir, _ = _worker(p, runner=runner)
        assert w.run() == 0
        assert list(qdir.iterdir()) == []


def main() -> int:
    tests = [
        test_speaks_verbatim_and_cleans_up,
        test_waits_for_mpv_then_speaks,
        test_global_mute_skips_speech,
        test_blank_or_missing_text_skips_speech,
        test_speak_failure_still_returns_zero,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"say_worker: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
