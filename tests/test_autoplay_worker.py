"""Worker-level smoke tests for AutoplayWorker.

Walks the lifecycle end to end with stubbed extract/hash/speak/rewrite
(injected runner) and stubbed collaborators so no real audio, LLM, mpv,
or beacon state is touched. Covers:
  - global-disable bail (SPAWNED → BAILED)
  - stale-during-coalesce bail (COALESCING → BAILED)
  - source-too-short bail (RESOLVING → BAILED)
  - cache-hit success path (… → SPEAKING → DONE) with correct speak call
  - dedup bail on cache-hit path
  - run() always returns 0
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

import autoplay_worker as awmod
from autoplay_worker import AutoplayWorker
from worker_lifecycle import BAILED, DONE


class _StubGate:
    def __init__(self, gated: bool) -> None:
        self._gated = gated

    def worker_gated_off(self) -> bool:
        return self._gated


class _StubBeacon:
    """Returns scripted is_stale() answers, then sticks on the last."""

    def __init__(self, answers) -> None:
        self._answers = list(answers) or [False]

    def is_stale(self) -> bool:
        if len(self._answers) > 1:
            return self._answers.pop(0)
        return self._answers[0]


class _StubDedup:
    def __init__(self, already: bool, claim: bool = True) -> None:
        self._already = already
        self._claim = claim
        self.written = None

    def already_playing(self, h) -> bool:
        return self._already

    def try_claim(self, h) -> bool:
        if self._claim:
            self.written = h
            return True
        return False

    def write_now_playing(self, h) -> None:
        self.written = h


class _StubFifo:
    def __init__(self, turn: bool = True) -> None:
        self._turn = turn
        self.enqueued = False
        self.played = False

    def enqueue(self):
        self.enqueued = True
        return Path("/tmp/fake-ticket")

    def wait_for_queue_turn(self, *a, **k) -> bool:
        return self._turn

    def mark_playing(self) -> None:
        self.played = True


class _Recorder:
    """Scriptable runner keyed by the first program arg basename / role."""

    def __init__(self, script) -> None:
        self._script = script  # callable(argv) -> (rc, out)
        self.calls = []

    def __call__(self, argv, *, stdin_text=None, stdout_path=None):
        self.calls.append((argv, stdin_text))
        return self._script(argv, stdin_text)


_CFG = {"coalesce_seconds": 0, "narration_wait_max": 90, "queue_wait_max": 600, "min_len": 20}


def _worker(**kw):
    base = {
        "config": _CFG,
        "gate": _StubGate(False),
        "beacon": _StubBeacon([False, False, False, False, False]),
        "fifo": _StubFifo(),
        "dedup": _StubDedup(False),
        "sleep": lambda s: None,
    }
    base.update(kw)
    return AutoplayWorker(0.0, "", "sid12345", **base)


def test_global_disable_bails() -> None:
    w = _worker(gate=_StubGate(True))
    assert w.run() == 0
    assert w.machine.state == BAILED


def test_stale_during_coalesce_bails() -> None:
    w = _worker(beacon=_StubBeacon([True]))  # stale right after coalesce
    assert w.run() == 0
    assert w.machine.state == BAILED


def test_source_too_short_bails() -> None:
    def script(argv, stdin):
        if argv[1].endswith("run_extract.sh"):
            return 0, "short"  # < 20 bytes
        raise AssertionError("should not reach hash")

    w = _worker(runner=_Recorder(script))
    assert w.run() == 0
    assert w.machine.state == BAILED


def _with_cache(monkey_root: Path, hash_prefix: str):
    wav = monkey_root / "config" / "cache" / hash_prefix / "full.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    wav.write_text("RIFF", encoding="utf-8")


def test_cache_hit_success_path() -> None:
    long_msg = "x" * 40
    full_hash = "a" * 64
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-worker-test-"))
    _with_cache(tmp, full_hash[:16])

    spoke = {"called": False, "hash": None, "stdin": None}

    def script(argv, stdin):
        prog = argv[1] if argv[0] == "bash" else argv[0]
        if str(prog).endswith("run_extract.sh"):
            return 0, long_msg
        if str(prog).endswith("compute_hash.sh"):
            return 0, full_hash + "\n"
        if str(prog).endswith("run_speak.sh"):
            spoke["called"] = True
            spoke["hash"] = argv[argv.index("--source-hash") + 1]
            spoke["stdin"] = stdin
            return 0, None
        raise AssertionError(f"unexpected argv {argv}")

    fifo = _StubFifo(turn=True)
    dedup = _StubDedup(False)
    orig_root = awmod._PROJECT_ROOT
    awmod._PROJECT_ROOT = tmp
    try:
        w = _worker(runner=_Recorder(script), fifo=fifo, dedup=dedup)
        # EXTRACT exec-check: os.access on the real wrapper — present in repo.
        assert os.access(str(awmod.EXTRACT), os.X_OK), "run_extract.sh must be executable"
        assert w.run() == 0
    finally:
        awmod._PROJECT_ROOT = orig_root

    assert w.machine.state == DONE, w.machine.state
    assert spoke["called"] is True
    assert spoke["hash"] == full_hash
    assert spoke["stdin"] == ""  # cache-hit path speaks with empty stdin
    assert fifo.played is True
    assert dedup.written == full_hash


def test_dedup_bails_on_cache_hit_path() -> None:
    long_msg = "x" * 40
    full_hash = "b" * 64
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-worker-test-"))
    _with_cache(tmp, full_hash[:16])

    def script(argv, stdin):
        prog = argv[1] if argv[0] == "bash" else argv[0]
        if str(prog).endswith("run_extract.sh"):
            return 0, long_msg
        if str(prog).endswith("compute_hash.sh"):
            return 0, full_hash + "\n"
        raise AssertionError("must not speak when deduped")

    orig_root = awmod._PROJECT_ROOT
    awmod._PROJECT_ROOT = tmp
    try:
        w = _worker(runner=_Recorder(script), dedup=_StubDedup(True))
        assert w.run() == 0
    finally:
        awmod._PROJECT_ROOT = orig_root
    assert w.machine.state == BAILED


def test_claim_lost_bails_after_wait() -> None:
    """A sibling won the atomic claim during our queue wait → bail, no speak."""
    long_msg = "x" * 40
    full_hash = "c" * 64
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-worker-test-"))
    _with_cache(tmp, full_hash[:16])

    def script(argv, stdin):
        prog = argv[1] if argv[0] == "bash" else argv[0]
        if str(prog).endswith("run_extract.sh"):
            return 0, long_msg
        if str(prog).endswith("compute_hash.sh"):
            return 0, full_hash + "\n"
        raise AssertionError("must not speak when the claim was lost")

    orig_root = awmod._PROJECT_ROOT
    awmod._PROJECT_ROOT = tmp
    try:
        # already_playing False (passes the early check + the wait), but the
        # atomic try_claim loses the race → False.
        w = _worker(runner=_Recorder(script), dedup=_StubDedup(False, claim=False))
        assert w.run() == 0
    finally:
        awmod._PROJECT_ROOT = orig_root
    assert w.machine.state == BAILED


def test_resolve_config_logs_and_falls_back_on_config_error() -> None:
    """A failing/unreadable config must not crash the worker: resolve_config
    falls back to env/defaults AND logs the failure (no silent swallow)."""
    import contextlib
    import io

    import autoplay_config

    def _boom() -> dict:
        raise RuntimeError("malformed config")

    saved_attr = autoplay_config.load_config
    saved_env = {
        k: os.environ.pop(k)
        for k in (
            "AUTO_SPEECH_AUTOPLAY_COALESCE",
            "AUTO_SPEECH_NARRATION_WAIT_MAX",
            "AUTO_SPEECH_QUEUE_WAIT_MAX",
            "AUTO_SPEECH_AUTOPLAY_MIN_LEN",
        )
        if k in os.environ
    }
    autoplay_config.load_config = _boom
    try:
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            cfg = awmod.resolve_config()
        # Fell back to the documented defaults...
        assert cfg["coalesce_seconds"] == 1.0
        assert cfg["narration_wait_max"] == 90
        assert cfg["queue_wait_max"] == 600
        assert cfg["min_len"] == 20
        # ...and the failure was surfaced, not swallowed.
        assert "config" in buf.getvalue().lower()
    finally:
        autoplay_config.load_config = saved_attr
        os.environ.update(saved_env)


def main() -> int:
    tests = [
        test_global_disable_bails,
        test_stale_during_coalesce_bails,
        test_source_too_short_bails,
        test_cache_hit_success_path,
        test_dedup_bails_on_cache_hit_path,
        test_claim_lost_bails_after_wait,
        test_resolve_config_logs_and_falls_back_on_config_error,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"autoplay_worker: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
