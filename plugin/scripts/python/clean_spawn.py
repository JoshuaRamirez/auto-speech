"""Start child programs clean: posix_spawn, never a fork; a built environment.

His rule (26 Nov 2026): "ban the clone-first call and start child programs
clean, with nothing inherited unless named". The narrator is a long-lived,
threaded daemon; CPython forks it for every child unless it can use
posix_spawn, which it does only for an absolute program path with
close_fds=False and no cwd, new session or preexec_fn. close_fds=False is safe:
every descriptor Python opens is close-on-exec (PEP 446), so only the pipes
reach the child.

The child's environment is built: the few names below, AUTO_SPEECH_* settings
and what the caller names; nothing else (no Claude session ids, tokens or
terminal pane of whoever started the daemon).
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from typing import IO, Any, Iterable, Mapping, Optional

EVERY_CHILD_GETS = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TZ", "TERM",
    "LANG", "LC_ALL", "LC_CTYPE", "__CF_USER_TEXT_ENCODING",
)
DEFAULT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def child_env(names: Iterable[str] = (), extra: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """The environment a child gets: EVERY_CHILD_GETS, AUTO_SPEECH_*, `names`, then `extra`."""
    wanted = set(EVERY_CHILD_GETS) | set(names)
    env = {k: v for k, v in os.environ.items() if k in wanted or k.startswith("AUTO_SPEECH_")}
    env.setdefault("PATH", DEFAULT_PATH)
    env.update(extra or {})
    return env


def spawn_argv(argv: list[str], env: Optional[Mapping[str, str]] = None) -> list[str]:
    """`argv` with its program as an absolute path (posix_spawn needs one); unchanged when not found."""
    if not argv or "/" in str(argv[0]):
        return [str(a) for a in argv]
    found = shutil.which(str(argv[0]), path=(env or child_env())["PATH"])
    return [found or str(argv[0]), *[str(a) for a in argv[1:]]]


def run(argv: list[str], *, names: Iterable[str] = (), extra: Optional[Mapping[str, str]] = None,
        **kwargs: Any) -> subprocess.CompletedProcess:
    """subprocess.run, started by posix_spawn with a built environment."""
    env = child_env(names, extra)
    return subprocess.run(spawn_argv(argv, env), env=env, close_fds=False, **kwargs)


def popen(argv: list[str], *, names: Iterable[str] = (), extra: Optional[Mapping[str, str]] = None,
          **kwargs: Any) -> subprocess.Popen:
    """subprocess.Popen, started by posix_spawn with a built environment."""
    env = child_env(names, extra)
    return subprocess.Popen(spawn_argv(argv, env), env=env, close_fds=False, **kwargs)


class GroupChild:
    """A child started by os.posix_spawn in a process group of its own, so a
    time limit can stop it with its helpers (subprocess forks for that)."""

    def __init__(self, argv: list[str], *, stdin: Any = None, stdout: Any = None, stderr: Any = None,
                 names: Iterable[str] = (), reap_in_background: bool = False) -> None:
        env = child_env(names)
        argv = spawn_argv(argv, env)
        actions: list[tuple] = []
        mine: list[int] = []      # our ends, kept
        theirs: list[int] = []    # the child's ends, closed here after the spawn
        self.stdin: Optional[IO[bytes]] = None
        self.stdout: Optional[IO[bytes]] = None
        self.stderr: Optional[IO[bytes]] = None
        for fd, spec in ((0, stdin), (1, stdout), (2, stderr)):
            if spec is None or spec == subprocess.DEVNULL:
                actions.append((os.POSIX_SPAWN_OPEN, fd, os.devnull, os.O_RDWR, 0))
            elif spec == subprocess.STDOUT and fd == 2:
                actions.append((os.POSIX_SPAWN_DUP2, 1, 2))
            elif spec == subprocess.PIPE:
                r, w = os.pipe()
                child_end, our_end = (r, w) if fd == 0 else (w, r)
                actions.append((os.POSIX_SPAWN_DUP2, child_end, fd))
                theirs.append(child_end)
                mine.append(our_end)
                stream = os.fdopen(our_end, "wb" if fd == 0 else "rb")
                setattr(self, ("stdin", "stdout", "stderr")[fd], stream)
            else:
                actions.append((os.POSIX_SPAWN_DUP2, spec if isinstance(spec, int) else spec.fileno(), fd))
        try:
            self.pid = os.posix_spawn(argv[0], argv, env, file_actions=actions, setpgroup=0,
                                      setsigdef=(signal.SIGPIPE, signal.SIGINT, signal.SIGTERM))
        except OSError:
            for s in (self.stdin, self.stdout, self.stderr):
                if s is not None:
                    s.close()
            raise
        finally:
            for fd in theirs:
                os.close(fd)
        self.args = argv
        self.returncode: Optional[int] = None
        if reap_in_background:
            threading.Thread(target=self.wait, daemon=True).start()

    def poll(self) -> Optional[int]:
        if self.returncode is None:
            try:
                pid, status = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                self.returncode = -1
                return self.returncode
            if pid == self.pid:
                self.returncode = os.waitstatus_to_exitcode(status)
        return self.returncode

    def wait(self, timeout: Optional[float] = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.poll() is None:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.args, timeout)
            time.sleep(0.01)
        assert self.returncode is not None
        return self.returncode

    def kill_group(self, sig: int = signal.SIGKILL) -> None:
        for target in (lambda: os.killpg(self.pid, sig), lambda: os.kill(self.pid, sig)):
            try:
                target()
            except OSError:
                pass

    def communicate(self, timeout: Optional[float] = None) -> tuple[bytes, bytes]:
        """Reads stdout and stderr to their end and waits; TimeoutExpired past `timeout` (the child is left running)."""
        out: list[bytes] = []
        err: list[bytes] = []
        readers = []
        for stream, sink in ((self.stdout, out), (self.stderr, err)):
            if stream is not None:
                t = threading.Thread(target=lambda s=stream, k=sink: k.append(s.read()), daemon=True)
                t.start()
                readers.append(t)
        if self.stdin is not None:
            self.stdin.close()
        deadline = None if timeout is None else time.monotonic() + timeout
        for t in readers:
            t.join(None if deadline is None else max(0.0, deadline - time.monotonic()))
            if t.is_alive():
                raise subprocess.TimeoutExpired(self.args, timeout)
        self.wait(None if deadline is None else max(0.0, deadline - time.monotonic()))
        self.close()
        return b"".join(out), b"".join(err)

    def close(self) -> None:
        """Closes our ends of its pipes."""
        for stream in (self.stdin, self.stdout, self.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
