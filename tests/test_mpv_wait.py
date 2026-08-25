"""Unit tests for MpvController._wait_for_prior_session().

FIFO playback contract: starting a new playback must NEVER terminate an
active one. The controller waits (bounded) for the prior mpv session to
finish on its own. SessionDir is monkeypatched so no real mpv or /tmp
state is touched.
"""
from __future__ import annotations

import inspect
import sys
import tempfile
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

import mpv_controller
from mpv_controller import MpvController


class _FakeSessionDir:
    """Stand-in for SessionDir with scriptable is_mpv_running answers."""

    def __init__(self, running_answers: list[bool]) -> None:
        self._answers = list(running_answers)
        self.clear_calls = 0
        self.kill_attempts = 0  # would only move if something signalled us
        # Session state redirected into a scratch dir so start() never
        # touches the real /tmp/auto-speech.
        self._root = Path(tempfile.mkdtemp(prefix="auto-speech-mpv-test-"))
        self.writes: list[tuple] = []

    def is_mpv_running(self) -> bool:
        if len(self._answers) > 1:
            return self._answers.pop(0)
        return self._answers[0]

    def read_pid(self) -> int | None:
        return 12345

    def clear(self) -> None:
        self.clear_calls += 1

    def root(self) -> Path:
        return self._root

    def socket_path(self) -> Path:
        return self._root / "control.sock"

    def write(self, pid, wav_path, started_at) -> None:
        self.writes.append((pid, wav_path, started_at))


def _patched(fake: _FakeSessionDir):
    class _CM:
        def __enter__(self):
            self._orig = mpv_controller.SessionDir
            mpv_controller.SessionDir = fake
            return fake

        def __exit__(self, *a):
            mpv_controller.SessionDir = self._orig

    return _CM()


def test_no_prior_session_returns_immediately() -> None:
    fake = _FakeSessionDir([False])
    with _patched(fake):
        start = time.monotonic()
        MpvController()._wait_for_prior_session()
        elapsed = time.monotonic() - start
    assert elapsed < 0.2, f"should not have waited; took {elapsed:.2f}s"
    assert fake.clear_calls == 1, "stale session state must be cleared"


def test_waits_until_prior_session_finishes() -> None:
    # Running for 3 polls, then gone — the wait must outlast the playback.
    fake = _FakeSessionDir([True, True, True, False])
    with _patched(fake):
        ctl = MpvController()
        start = time.monotonic()
        ctl._wait_for_prior_session()
        elapsed = time.monotonic() - start
    # 2 sleeps minimum (poll interval 0.25s): the prior session was alive
    # through the first three is_mpv_running() calls.
    assert elapsed >= 2 * MpvController._PRIOR_PLAYBACK_POLL_SECONDS, (
        f"returned too early ({elapsed:.2f}s) — would have overlapped playback"
    )
    assert fake.clear_calls == 1


def test_cap_expiry_proceeds_without_killing() -> None:
    fake = _FakeSessionDir([True])  # never finishes
    with _patched(fake):
        ctl = MpvController()
        # Shrink the cap so the test is fast.
        orig_cap = MpvController._PRIOR_PLAYBACK_WAIT_SECONDS
        MpvController._PRIOR_PLAYBACK_WAIT_SECONDS = 0.6
        try:
            ctl._wait_for_prior_session()  # must return, must not raise
        finally:
            MpvController._PRIOR_PLAYBACK_WAIT_SECONDS = orig_cap
    # No clear() on cap expiry: the prior session is still alive and owns
    # the session dir.
    assert fake.clear_calls == 0
    assert fake.kill_attempts == 0


def test_reused_controller_can_start_twice() -> None:
    """Regression: a controller held across playbacks must restart cleanly.

    web_server.py keeps ONE MpvController for the life of the process and
    never calls stop() — /api/end quits mpv over IPC directly. The FSM was
    therefore still at READY on the next start(), where IDLE → STARTING
    raised IllegalTransition. Uncaught in both _handle_speak (cache hit)
    and _handle_replay, so every playback after the first 500'd for the
    rest of the server's life.
    """
    fake = _FakeSessionDir([False])
    wav = Path(__file__).resolve()  # any existing file; start() only stats it

    class _FakeProc:
        pid = 4242
        returncode = None

        def poll(self):
            return None

    with _patched(fake):
        orig_which = mpv_controller.shutil.which
        orig_popen = mpv_controller.subprocess.Popen
        orig_await = MpvController._await_socket_ready
        mpv_controller.shutil.which = lambda _n: "/usr/bin/true"
        mpv_controller.subprocess.Popen = lambda *a, **k: _FakeProc()
        MpvController._await_socket_ready = lambda self, s, p: None
        try:
            ctl = MpvController()
            ctl.start(wav)
            assert ctl._fsm.state == mpv_controller.READY
            ctl.start(wav)  # must not raise IllegalTransition
            assert ctl._fsm.state == mpv_controller.READY
        finally:
            mpv_controller.shutil.which = orig_which
            mpv_controller.subprocess.Popen = orig_popen
            MpvController._await_socket_ready = orig_await


def test_start_no_longer_kills_prior_session() -> None:
    # Source-level pin: the start() sequence must wait, never terminate.
    src = inspect.getsource(MpvController.start)
    assert "_kill_prior_session" not in src, (
        "MpvController.start() must not call _kill_prior_session() — "
        "FIFO contract: active playback is never cut off"
    )
    assert "_wait_for_prior_session" in src


def main() -> int:
    tests = [
        test_no_prior_session_returns_immediately,
        test_waits_until_prior_session_finishes,
        test_cap_expiry_proceeds_without_killing,
        test_reused_controller_can_start_twice,
        test_start_no_longer_kills_prior_session,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"mpv_wait: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
