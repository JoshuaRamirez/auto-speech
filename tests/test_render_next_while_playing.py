"""The next queued line is rendered while the line before it plays
(task:auto-speech-render-next-while-playing, 27 Nov 2026).

A long read sent as passages played with a gap between each: the worker took
a line, rendered it, played it, and only then took and rendered the next. Here
every render takes RENDER_S and every play PLAY_S; the second line's render
must start while the first plays, it must be rendered once, and it must follow
the first without a render's wait. Nothing is heard (the sink only sleeps)."""

from __future__ import annotations

import isolation  # noqa: F401,I001  -- before any auto-speech module: private stores only
import time
import unittest
from pathlib import Path

import test_line_id as harness
import narrator_service

RENDER_S = 0.6
PLAY_S = 0.8


class _TimedSynth(harness._SlowSynth):
    def __init__(self, delay: float) -> None:
        super().__init__(delay)
        self.began: dict[str, float] = {}

    def synthesize_one(self, text, profile, out) -> bool:
        self.began.setdefault(text, time.monotonic())
        return super().synthesize_one(text, profile, out)


class _TimedSink(harness._Sink):
    def __init__(self) -> None:
        super().__init__()
        self.spans: list[tuple[float, float]] = []

    def play(self, wav_path, timeout=None, *, hold=True, already_waited=0.0, on_hold=None, on_start=None) -> None:
        start = time.monotonic()
        super().play(wav_path, timeout, hold=hold, already_waited=already_waited, on_hold=on_hold, on_start=on_start)
        time.sleep(PLAY_S)
        self.spans.append((start, time.monotonic()))


class TestRenderNextWhilePlaying(harness._Daemon):
    synth_s = RENDER_S

    def setUp(self) -> None:
        super().setUp()
        self.svc._sink = self.sink = _TimedSink()
        self.svc._synth = self.synth = _TimedSynth(RENDER_S)

    def test_the_next_line_is_rendered_while_the_one_before_it_plays(self):
        self.speak("The first passage.", "a")
        self.speak("The second passage.", "b")
        self.wait_for(lambda: self.line("b")["state"] == harness.DONE, (RENDER_S + PLAY_S) * 3 + 5)

        self.assertEqual(self.synth.calls, ["The first passage.", "The second passage."], "each rendered once")
        (_, first_end), (second_start, _) = self.sink.spans
        self.assertLess(self.synth.began["The second passage."], first_end, "rendered while the first played")
        self.assertLess(second_start - first_end, RENDER_S / 2, "no render's wait between the two")
        # The line is done once heard; its own file goes in the speak's cleanup just after.
        self.wait_for(lambda: not list(Path(narrator_service.SYNTH_DIR).glob("*.wav")), 2,
                      lambda: f"left behind: {list(Path(narrator_service.SYNTH_DIR).glob('*.wav'))}")

    def test_a_render_ahead_that_does_not_match_is_dropped_and_the_line_rendered_again(self):
        self.speak("The first passage.", "a")
        self.speak("The second passage.", "b")
        self.wait_for(lambda: "The second passage." in self.synth.began, RENDER_S * 2 + PLAY_S + 5)
        # The line changed before its turn: the render ahead is not taken.
        render = self.svc._next_render
        self.assertIsNotNone(render)
        self.svc._next_render = narrator_service.NextLineRender(("other", None, None), render.path)
        self.wait_for(lambda: self.line("b")["state"] == harness.DONE, (RENDER_S + PLAY_S) * 3 + 5)
        self.assertEqual(self.synth.calls.count("The second passage."), 2, "rendered again, not the stale render")
        self.assertEqual(len(self.sink.spans), 2)


if __name__ == "__main__":
    unittest.main()
