"""Speech holds up under load (ADR-019).

Three things keep the narrator audible while the Mac is busy:

1. **Its own priority.** The daemon clears any Darwin background clamp it
   inherited, reports a nice value it cannot undo, and puts its speech
   threads at user-interactive QoS. The processes it spawns (``say`` and
   mpv) run under ``taskpolicy -t 0 -l 0`` (throughput and latency tier 0)
   so the scheduler serves them before heavy work at background QoS.
2. **Budgets that stretch with the load.** A synthesis or wall-clock play
   budget is multiplied by the load per core, capped, so a busy Mac is not
   mistaken for a hung one.
3. **A wait for the load to fall.** A retried line waits (polling the load
   average) until the load per core is under a threshold or a deadline
   passes, then plays.

Nothing here is a gate: a check that cannot be made (no libc, no
taskpolicy) is logged and skipped, never refused.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import shutil
import time
from dataclasses import dataclass
from typing import Callable, Optional

QOS_BOOST_ENV = "AUTO_SPEECH_QOS_BOOST"            # "0" turns the taskpolicy wrap off
TASKPOLICY = "/usr/sbin/taskpolicy"
TASKPOLICY_ARGS = ("-t", "0", "-l", "0")           # throughput tier 0, latency tier 0
PRIO_DARWIN_PROCESS = 4                             # setpriority(2) "which" for the Darwin policy
PRIO_DARWIN_NORMAL = 0                              # value 0 clears PRIO_DARWIN_BG
QOS_CLASS_USER_INTERACTIVE = 0x21                   # pthread/qos.h

DEFAULT_LOAD_SCALE_CAP = 4.0
DEFAULT_RETRY_LOAD_PER_CORE = 2.0
DEFAULT_RETRY_WAIT_SECONDS = 180.0
DEFAULT_RETRY_POLL_SECONDS = 2.0


def boost_enabled() -> bool:
    return os.environ.get(QOS_BOOST_ENV, "1").strip() != "0"


def taskpolicy_path() -> Optional[str]:
    """The taskpolicy binary, when it exists and the boost is on."""
    if not boost_enabled():
        return None
    if os.path.isfile(TASKPOLICY) and os.access(TASKPOLICY, os.X_OK):
        return TASKPOLICY
    found = shutil.which("taskpolicy")
    return found or None


def boosted_argv(argv: list[str]) -> list[str]:
    """`argv` under taskpolicy's top tiers; `argv` itself when the boost is
    off or taskpolicy is missing. taskpolicy execs the command, so the
    child's pid is the command's own (the sink's interrupt still works)."""
    tp = taskpolicy_path()
    if tp is None:
        return list(argv)
    return [tp, *TASKPOLICY_ARGS, *argv]


@dataclass(frozen=True)
class PriorityReport:
    nice: int
    nice_restored: bool
    bg_cleared: bool
    thread_qos: bool
    notes: tuple[str, ...] = ()

    def describe(self) -> str:
        parts = [f"nice {self.nice}" + (" (restored to 0)" if self.nice_restored else "")]
        parts.append("background clamp cleared" if self.bg_cleared else "background clamp not cleared")
        parts.append("thread QoS user-interactive" if self.thread_qos else "thread QoS unchanged")
        if self.nice > 0 and not self.nice_restored:
            parts.append("the spawner niced the daemon; only a fresh start at nice 0 undoes that")
        parts.extend(self.notes)
        return "; ".join(parts)


def _libc() -> Optional[ctypes.CDLL]:
    try:
        name = ctypes.util.find_library("c") or "libc.dylib"
        return ctypes.CDLL(name, use_errno=True)
    except OSError:
        return None


def set_thread_qos_user_interactive() -> bool:
    """The calling thread at QOS_CLASS_USER_INTERACTIVE. False when the
    call is unavailable (not macOS) or refused."""
    libc = _libc()
    if libc is None:
        return False
    try:
        fn = libc.pthread_set_qos_class_self_np
    except AttributeError:
        return False
    fn.argtypes = (ctypes.c_uint, ctypes.c_int)
    fn.restype = ctypes.c_int
    try:
        return fn(QOS_CLASS_USER_INTERACTIVE, 0) == 0
    except (OSError, ValueError):
        return False


def raise_own_priority() -> PriorityReport:
    """Undo what the daemon may have inherited: a Darwin background clamp
    (cleared with setpriority(PRIO_DARWIN_PROCESS, 0, 0)) and a nice value
    (restored to 0 when the kernel allows; an unprivileged process usually
    cannot lower its nice, so that is reported). The main thread goes to
    user-interactive QoS."""
    notes: list[str] = []
    try:
        # getpriority, not nice(0): at nice 20 macOS's nice(0) fails EPERM.
        nice = os.getpriority(os.PRIO_PROCESS, 0)
    except OSError:
        nice = 0
    restored = False
    if nice > 0:
        try:
            os.setpriority(os.PRIO_PROCESS, 0, 0)
            restored = os.getpriority(os.PRIO_PROCESS, 0) == 0
        except (OSError, PermissionError) as exc:
            notes.append(f"nice not restored: {exc.__class__.__name__}")
    bg_cleared = False
    try:
        os.setpriority(PRIO_DARWIN_PROCESS, 0, PRIO_DARWIN_NORMAL)
        bg_cleared = True
    except (OSError, PermissionError, AttributeError) as exc:
        notes.append(f"background clamp: {exc.__class__.__name__}")
    thread_qos = set_thread_qos_user_interactive()
    return PriorityReport(nice=nice, nice_restored=restored, bg_cleared=bg_cleared, thread_qos=thread_qos, notes=tuple(notes))


def _load1() -> float:
    try:
        return float(os.getloadavg()[0])
    except (OSError, AttributeError):
        return 0.0


class LoadWatch:
    """The one-minute load average per core, and what the narrator does
    with it: a budget scale and a wait for the load to fall. `load_fn` and
    `clock`/`sleep` are injectable for tests."""

    def __init__(
        self,
        *,
        cores: Optional[int] = None,
        load_fn: Callable[[], float] = _load1,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cores = max(1, int(cores or os.cpu_count() or 1))
        self._load_fn = load_fn
        self._clock = clock
        self._sleep = sleep

    def load_per_core(self) -> float:
        return max(0.0, self._load_fn()) / self.cores

    def scale(self, cap: float = DEFAULT_LOAD_SCALE_CAP) -> float:
        """How much longer a budget gets under load: the load per core,
        never under 1, never over `cap`; 1 when the cap is off (<= 0)."""
        if cap <= 0:
            return 1.0
        return max(1.0, min(float(cap), self.load_per_core()))

    def wait_until_below(
        self,
        threshold_per_core: float = DEFAULT_RETRY_LOAD_PER_CORE,
        *,
        deadline_seconds: float = DEFAULT_RETRY_WAIT_SECONDS,
        poll_seconds: float = DEFAULT_RETRY_POLL_SECONDS,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> str:
        """Wait until the load per core is under the threshold. Returns
        "clear" (it is, possibly at once), "deadline" (the wait ran out:
        the caller goes ahead anyway) or "stopped" (should_stop said so).
        A threshold <= 0 never waits."""
        if threshold_per_core <= 0:
            return "clear"
        start = self._clock()
        while True:
            if should_stop is not None and should_stop():
                return "stopped"
            if self.load_per_core() < threshold_per_core:
                return "clear"
            if self._clock() - start >= deadline_seconds:
                return "deadline"
            self._sleep(max(0.05, float(poll_seconds)))
