"""Unit tests for PlaybackFifo (the cross-session FIFO playback queue).

Reproduces the coverage of the retired test_autoplay_queue.sh anchor
test, exercising the helpers directly:
  - empty queue → head
  - sole ticket → head
  - older LIVE ticket blocks us
  - older DEAD-owner ticket is garbage-collected, then we are head
  - newer ticket does not block us
  - tickets sort in arrival order
  - wait_for_queue_turn: proceeds when idle+drained+head, bails on stale,
    proceeds on cap expiry
  - release removes the ticket
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from playback_fifo import PlaybackFifo
from playback_ticket import HEAD, RELEASED


def _qdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="auto-speech-fifo-test-"))


def test_empty_queue_is_head() -> None:
    f = PlaybackFifo(pid=os.getpid(), queue_dir=_qdir())
    assert f.ticket_is_head() is True  # no ticket, empty dir → proceed


def test_sole_ticket_is_head() -> None:
    f = PlaybackFifo(pid=os.getpid(), queue_dir=_qdir())
    f.enqueue()
    assert f.ticket_is_head() is True


def test_older_live_ticket_blocks() -> None:
    d = _qdir()
    f = PlaybackFifo(pid=os.getpid(), queue_dir=d)
    f.enqueue()
    # Older ticket (smaller stamp), owner = our own live pid.
    older = d / f"00000000000000000001.{os.getpid()}"
    older.write_text(f"{os.getpid()}\n", encoding="utf-8")
    assert f.ticket_is_head() is False


def test_older_dead_owner_ticket_collected() -> None:
    d = _qdir()
    f = PlaybackFifo(pid=os.getpid(), queue_dir=d)
    f.enqueue()
    older = d / "00000000000000000001.999"
    older.write_text("999999\n", encoding="utf-8")  # PID safely non-existent
    assert f.ticket_is_head() is True
    assert not older.exists(), "dead-owner ticket must be garbage-collected"


def test_newer_ticket_does_not_block() -> None:
    d = _qdir()
    f = PlaybackFifo(pid=os.getpid(), queue_dir=d)
    f.enqueue()
    newer = d / f"99999999999999999999.{os.getpid()}"
    newer.write_text(f"{os.getpid()}\n", encoding="utf-8")
    assert f.ticket_is_head() is True


def test_tickets_sort_in_arrival_order() -> None:
    d = _qdir()
    a = PlaybackFifo(pid=os.getpid(), queue_dir=d).enqueue()
    b = PlaybackFifo(pid=os.getpid(), queue_dir=d).enqueue()
    assert a.name < b.name, f"{a.name} should sort before {b.name}"


def test_wait_proceeds_when_idle_and_drained() -> None:
    f = PlaybackFifo(pid=os.getpid(), queue_dir=_qdir())
    f.enqueue()
    ok = f.wait_for_queue_turn(
        cap_seconds=5,
        mpv_running=lambda: False,
        narrator_depth=lambda: 0,
        is_stale=lambda: False,
        sleep=lambda s: None,
    )
    assert ok is True
    assert f.machine.state == HEAD


def test_wait_bails_on_stale() -> None:
    f = PlaybackFifo(pid=os.getpid(), queue_dir=_qdir())
    f.enqueue()
    # mpv busy so we never reach head; stale fires after the first poll.
    ok = f.wait_for_queue_turn(
        cap_seconds=5,
        mpv_running=lambda: True,
        narrator_depth=lambda: 0,
        is_stale=lambda: True,
        sleep=lambda s: None,
    )
    assert ok is False


def test_wait_proceeds_on_cap_expiry() -> None:
    f = PlaybackFifo(pid=os.getpid(), queue_dir=_qdir())
    f.enqueue()
    # mpv never idle, never stale → cap expiry → proceed anyway (no kill).
    calls = {"n": 0}

    def mpv():
        calls["n"] += 1
        return True

    ok = f.wait_for_queue_turn(
        cap_seconds=2,  # cap*2 = 4 iterations
        mpv_running=mpv,
        narrator_depth=lambda: 0,
        is_stale=lambda: False,
        sleep=lambda s: None,
    )
    assert ok is True
    assert calls["n"] == 4, f"expected cap*2=4 polls, got {calls['n']}"


def test_release_removes_ticket() -> None:
    f = PlaybackFifo(pid=os.getpid(), queue_dir=_qdir())
    t = f.enqueue()
    assert t.exists()
    f.release()
    assert not t.exists()
    assert f.machine.state == RELEASED


def main() -> int:
    tests = [
        test_empty_queue_is_head,
        test_sole_ticket_is_head,
        test_older_live_ticket_blocks,
        test_older_dead_owner_ticket_collected,
        test_newer_ticket_does_not_block,
        test_tickets_sort_in_arrival_order,
        test_wait_proceeds_when_idle_and_drained,
        test_wait_bails_on_stale,
        test_wait_proceeds_on_cap_expiry,
        test_release_removes_ticket,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"playback_fifo: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
