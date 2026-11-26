"""The process list read from the kernel in this process, as `ps` shows it.

His rule (26 Nov 2026): do not start a program to read what can be read
directly. The narrator asked `ps` for the process list once per utterance and
every half second while a sound hold lasted; this reads the same through
sysctl: KERN_PROC_ALL for pid and parent, KERN_PROCARGS2 for each command line
(the kernel refuses it for another user's process; the short name stands in).
"""

from __future__ import annotations

import ctypes
import struct
import sys
from typing import Optional

# struct kinfo_proc on Darwin: 648 bytes; kp_proc.p_pid at 40, kp_proc.p_comm
# at 243, kp_eproc.e_ppid at 560 (checked against ps).
_KINFO_PROC_SIZE, _KP_PID, _KP_COMM, _KP_PPID = 648, 40, 243, 560
_CTL_KERN, _KERN_PROC, _KERN_PROC_ALL, _KERN_PROC_PID, _KERN_PROCARGS2 = 1, 14, 0, 1, 49

_libc: Optional[ctypes.CDLL] = None


def _sysctl(mib: list[int]) -> Optional[bytes]:
    global _libc
    if sys.platform != "darwin":
        return None
    if _libc is None:
        try:
            _libc = ctypes.CDLL(None, use_errno=True)
        except OSError:
            return None
    arr = (ctypes.c_int * len(mib))(*mib)
    size = ctypes.c_size_t(0)
    if _libc.sysctl(arr, len(mib), None, ctypes.byref(size), None, 0) != 0:
        return None
    size.value += size.value // 8 + 4096
    buf = ctypes.create_string_buffer(size.value)
    if _libc.sysctl(arr, len(mib), buf, ctypes.byref(size), None, 0) != 0:
        return None
    return buf.raw[: size.value]


def _entries(raw: bytes) -> list[tuple[int, int, str]]:
    out = []
    for at in range(0, len(raw) - _KINFO_PROC_SIZE + 1, _KINFO_PROC_SIZE):
        pid = struct.unpack_from("i", raw, at + _KP_PID)[0]
        ppid = struct.unpack_from("i", raw, at + _KP_PPID)[0]
        comm = raw[at + _KP_COMM: at + _KP_COMM + 17].split(b"\0", 1)[0].decode("utf-8", "replace")
        out.append((pid, ppid, comm))
    return out


def command_of(pid: int) -> str:
    """argv of `pid` joined by spaces, as `ps -o command=` shows it; '' when refused or gone."""
    if pid <= 0:
        return ""
    raw = _sysctl([_CTL_KERN, _KERN_PROCARGS2, pid])
    if not raw or len(raw) < 4:
        return ""
    argc = struct.unpack_from("i", raw, 0)[0]
    rest = raw[4:]
    end = rest.find(b"\0")
    if end < 0:
        return ""
    at = end
    while at < len(rest) and rest[at] == 0:
        at += 1
    args = rest[at:].split(b"\0")[: max(argc, 0)]
    return " ".join(a.decode("utf-8", "replace") for a in args)


def process_table() -> Optional[dict[int, tuple[int, str]]]:
    """pid -> (ppid, command) for every process; None off macOS or when the read fails."""
    raw = _sysctl([_CTL_KERN, _KERN_PROC, _KERN_PROC_ALL])
    if not raw or len(raw) % _KINFO_PROC_SIZE:
        return None
    return {pid: (ppid, command_of(pid) or comm) for pid, ppid, comm in _entries(raw)}


def parent_and_command(pid: int) -> Optional[tuple[int, str]]:
    """(parent pid, command line) of a live process, or None when it is gone (or off macOS)."""
    raw = _sysctl([_CTL_KERN, _KERN_PROC, _KERN_PROC_PID, pid])
    if not raw or len(raw) < _KINFO_PROC_SIZE:
        return None
    found = [e for e in _entries(raw) if e[0] == pid]
    if not found:
        return None
    _, ppid, comm = found[0]
    return ppid, command_of(pid) or comm
