"""Find and stop every narrator a test started under its private folder.

A narrator a test starts through the start script is detached (double fork,
setsid): it is in no process group of the test's and outlives it. On
2026-10-30 two such narrators were left running after a test run
(#m1030-1044-b20b). A narrator is "the test's" when it holds a file open
under the test's private folder (its lock, log, socket, or a stub's hold
file). The live narrator keeps its files directly in /tmp, so it never
matches; a private folder that IS /tmp is refused outright.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

_LIVE_DIRS = {"/tmp", "/private/tmp"}


def _prefixes(state_dir: str | Path) -> tuple[str, ...]:
    raw = os.path.abspath(str(state_dir))
    real = os.path.realpath(raw)
    if raw in _LIVE_DIRS or real in _LIVE_DIRS or real == "/":
        raise ValueError(f"refusing to reap under the live state folder: {state_dir}")
    out = {raw.rstrip("/") + "/", real.rstrip("/") + "/"}
    for p in list(out):  # /tmp/x and /private/tmp/x name the same folder
        if p.startswith("/private/"):
            out.add(p[len("/private"):])
        elif p.startswith("/tmp/"):
            out.add("/private" + p)
    return tuple(out)


def _candidate_pids() -> list[int]:
    r = subprocess.run(["pgrep", "-f", "narrator_service.py"], capture_output=True, text=True, check=False)
    me = os.getpid()
    return [int(x) for x in r.stdout.split() if x.strip().isdigit() and int(x) != me]


def _open_names(pid: int) -> list[str]:
    r = subprocess.run(["lsof", "-a", "-p", str(pid), "-Fn"], capture_output=True, text=True, check=False)
    return [line[1:] for line in r.stdout.splitlines() if line.startswith("n")]


def narrators_under(state_dir: str | Path, settle: float = 5.0) -> list[int]:
    """Live narrator_service.py processes holding a file under `state_dir`.

    A narrator still importing has opened no state file yet (its log opens on
    import): such a one is watched for up to `settle` seconds until it shows
    where it belongs, so one started a moment ago is not missed."""
    prefixes = _prefixes(state_dir)
    found: list[int] = []
    pending = _candidate_pids()
    deadline = time.monotonic() + settle
    while pending:
        unsettled = []
        for pid in pending:
            names = _open_names(pid)
            if any(n.startswith(prefixes) for n in names):
                found.append(pid)
            elif _alive(pid) and not any(n.endswith("auto-speech-narrator-daemon.log") for n in names):
                unsettled.append(pid)
        if not unsettled or time.monotonic() >= deadline:
            break
        time.sleep(0.2)
        pending = unsettled
    return found


def await_starts(state_dir: str | Path, timeout: float = 15.0) -> None:
    """Wait while a start is in flight under `state_dir`: the start script
    holds its lock directory from its check until the new pid file appears.
    A hook that spawns the script detached may end before its narrator
    exists; reaping before the start finishes would miss it."""
    lock = Path(state_dir) / "auto-speech-narrator-start.lock.d"
    deadline = time.monotonic() + timeout
    while lock.exists() and time.monotonic() < deadline:
        time.sleep(0.1)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def reap_narrators(state_dir: str | Path, timeout: float = 20.0) -> list[int]:
    """SIGTERM each narrator under `state_dir`, SIGKILL any still there after
    `timeout`, and return the pids that survive both (should be none)."""
    return reap_pids(narrators_under(state_dir), timeout)


def reap_pids(pids: list[int], timeout: float = 20.0) -> list[int]:
    """SIGTERM `pids`, SIGKILL any still alive after `timeout`; the survivors."""
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + timeout
    while any(_alive(p) for p in pids) and time.monotonic() < deadline:
        time.sleep(0.1)
    for pid in pids:
        if _alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    time.sleep(0.2)
    return [p for p in pids if _alive(p)]


# A test module's guard: narrators under the private state folder when the
# module began are not its own; any that appear by its end are, and are stopped.
_GUARD_BEFORE: set[int] = set()


def private_root() -> str | None:
    """AUTO_SPEECH_TMP_ROOT when it is a private folder; None for the live one."""
    root = os.environ.get("AUTO_SPEECH_TMP_ROOT", "").strip()
    if not root or os.path.realpath(root) in _LIVE_DIRS:
        return None
    return root


def guard_start() -> None:
    root = private_root()
    _GUARD_BEFORE.clear()
    if root:
        _GUARD_BEFORE.update(narrators_under(root))


def guard_end(grace: float = 2.0) -> tuple[list[int], list[int]]:
    """Stop the narrators this module started that are still running at its
    end (after `grace` seconds for any on their way out). Returns (stopped,
    survivors): survivors outlived SIGTERM and SIGKILL, and should be none."""
    root = private_root()
    if not root:
        return [], []
    await_starts(root)
    left = [p for p in narrators_under(root) if p not in _GUARD_BEFORE]
    deadline = time.monotonic() + grace
    while left and time.monotonic() < deadline:
        time.sleep(0.2)
        left = [p for p in left if _alive(p)]
    return left, reap_pids(left)


def guard_end_or_fail() -> None:
    """A test module's last step: stop every narrator it started, say so, and
    raise if any survives."""
    stopped, survivors = guard_end()
    if stopped:
        print(f"[reaped] narrator(s) a test here started: {stopped}")
    assert not survivors, f"narrator(s) a test here started survived SIGTERM and SIGKILL: {survivors}"


def main(argv: list[str]) -> int:
    """`narrator_reaper.py check|reap <dir>`: list the narrators under <dir>,
    wait a moment for any already on their way out, then stop the rest and
    print `left: <pids>`. `check` exits 1 when any were running (a leak);
    `reap` exits 1 only when one survives the stopping."""
    import sys

    if len(argv) != 3 or argv[1] not in ("check", "reap"):
        print("usage: narrator_reaper.py check|reap <state-dir>", file=sys.stderr)
        return 2
    mode, state = argv[1], argv[2]
    await_starts(state)
    pids = narrators_under(state)
    deadline = time.monotonic() + 2.0
    while pids and time.monotonic() < deadline:
        time.sleep(0.2)
        pids = [p for p in pids if _alive(p)]
    if not pids:
        return 0
    survivors = reap_pids(pids)
    print(f"left: {' '.join(map(str, pids))}" + (f" (still alive after reaping: {survivors})" if survivors else ""))
    return 1 if (mode == "check" or survivors) else 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv))
