"""One narrator, one socket (#m1030-1044-b20b).

On 2026-10-30 at 10:53 a narrator restart started two narrators in the same
second (pids 11649 and 11644). Both got past the "already running" check,
since neither had written its pid file yet. One bound the socket; the other
failed to bind ("[Errno 48] Address already in use"), shut down, and on its
way out removed the socket file: the live narrator's. The survivor ran on
with no socket, `narrate status` read "daemon alive: False", and no desk line
could reach it until it was restarted by hand.

These tests pin the three guarantees that close it:

  1. Two narrators started together: exactly one serves, the other exits
     without touching the pid file or the socket; the socket file is there
     afterwards and answers `status`.
  2. A narrator removes only a socket file it bound itself: never one that a
     live server answers on, never one that replaced its own.
  3. Starts are serialized by a lock the narrator holds for its life; a stale
     socket file nobody answers on is still reclaimed.

Everything runs in a private folder with a private socket path, a mock
summarizer and a mock sink: nothing is heard, the live narrator
(/tmp/auto-speech-daemon.sock) is never touched.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "plugin" / "scripts" / "python"
for _p in (str(SRC), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("AUTO_SPEECH_TMP_ROOT", tempfile.mkdtemp(prefix="auto-speech-tests-"))
os.environ.setdefault("AUTO_SPEECH_MISSED_FILE", os.path.join(os.environ["AUTO_SPEECH_TMP_ROOT"], "missed.json"))

from narrator_reaper import narrators_under, reap_narrators  # noqa: E402
from quiet_audio import ensure_quiet  # noqa: E402

ensure_quiet()

LIVE_SOCKET = Path("/tmp/auto-speech-daemon.sock")

# Every private folder a test here starts a narrator under: reaped in each
# test's tearDown, and checked once more after the last test (#m1030-1044-b20b).
PRIVATE_DIRS: list[Path] = []
# Moved aside (never removed) after the last check, so a survivor's files
# stay where that check looks.
MOVE_AFTER: list[Path] = []

# A narrator child: imports first (slow, uneven), then waits for the go file
# so both reach main() together, as the two starts did at 10:53:26. main()
# builds the service with a mock sink and synth: real start path, no sound.
CHILD = """\
import sys, time
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, {src!r})
import narrator_service
go = Path(sys.argv[2])
_Real = narrator_service.NarratorService
def _make(*a, **kw):
    return _Real(sink=MagicMock(), synth=MagicMock())
narrator_service.NarratorService = _make
while not go.exists():
    time.sleep(0.0005)
sys.exit(narrator_service.main())
"""


def _answers_status(path: Path, timeout: float = 1.0) -> dict | None:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(path))
        s.sendall(json.dumps({"action": "status"}).encode() + b"\n")
        data = b""
        while b"\n" not in data:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        line = data.partition(b"\n")[0]
        return json.loads(line) if line else None
    except (OSError, ValueError):
        return None
    finally:
        s.close()


def _private_env(state: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AUTO_SPEECH_", "UI_UTILITY_"))}
    cfg = state / "narrator.toml"
    cfg.write_text('[narrator]\nprovider = "mock"\n', encoding="utf-8")
    home = state / "home"
    home.mkdir(exist_ok=True)
    env.update(
        HOME=str(home),
        AUTO_SPEECH_TMP_ROOT=str(state),
        AUTO_SPEECH_DAEMON_SOCK=str(state / "auto-speech-daemon.sock"),
        AUTO_SPEECH_MISSED_FILE=str(state / "missed.json"),
        AUTO_SPEECH_MEETING_FILE=str(state / "meeting.json"),
        AUTO_SPEECH_HISTORY_DIR=str(state / "history"),
        AUTO_SPEECH_HISTORY_CONFIG=str(state / "history.toml"),
        AUTO_SPEECH_NARRATOR_CONFIG=str(cfg),
        AUTO_SPEECH_START_VIA_LAUNCHD="never",
        AUTO_SPEECH_PLAYER_LAUNCH="never",
        AUTO_SPEECH_MPV_AO="null",
        UI_UTILITY_SOCK=str(state / "ui-utility.sock"),
        UI_UTILITY_SILENT="1",
    )
    return env


class TestTwoStartsTogether(unittest.TestCase):
    """The 10:53 restart, replayed: two narrators reach main() at once."""

    def setUp(self) -> None:
        # A short path: AF_UNIX paths are capped near 104 bytes on macOS.
        self.state = Path(tempfile.mkdtemp(prefix="as-one-", dir="/tmp"))
        PRIVATE_DIRS.append(self.state)
        MOVE_AFTER.append(self.state)
        self.env = _private_env(self.state)
        self.sock = Path(self.env["AUTO_SPEECH_DAEMON_SOCK"])
        self.pid_file = self.state / "auto-speech-narrator-daemon.pid"
        # Never the live narrator's socket or state folder.
        assert self.sock != LIVE_SOCKET and self.state != Path("/tmp"), self.sock
        self.procs: list[subprocess.Popen] = []

    def tearDown(self) -> None:
        # Each child leads its own process group: stop the group, wait, then
        # kill what is left; reap anything else under the private folder.
        for p in self.procs:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except OSError:
                    pass
        for p in self.procs:
            try:
                p.wait(timeout=25)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except OSError:
                    pass
                p.wait(timeout=5)
            if p.stderr is not None:
                p.stderr.close()
        survivors = reap_narrators(self.state)
        still = [p.pid for p in self.procs if p.poll() is None] + survivors
        self.assertEqual(still, [], "every narrator this test started is gone")

    def _spawn(self, go: Path) -> subprocess.Popen:
        # "/narrator_service.py" in argv: the daemon's own identity check
        # (_pid_is_our_daemon) matches a child's command line by it.
        p = subprocess.Popen(
            [sys.executable, "-c", CHILD.format(src=str(SRC)), "/narrator_service.py", str(go)],
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        self.procs.append(p)
        return p

    def test_two_narrators_started_together_leave_one_serving_on_its_socket(self) -> None:
        go = self.state / "go"
        a, b = self._spawn(go), self._spawn(go)
        time.sleep(1.5)  # both imported and waiting on the go file
        go.touch()

        # The loser exits; the winner serves. Allow for a slow machine.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            alive = [p for p in (a, b) if p.poll() is None]
            if len(alive) == 1 and _answers_status(self.sock) is not None:
                break
            time.sleep(0.05)
        time.sleep(1.0)  # let any late clean-up by the loser happen

        alive = [p for p in (a, b) if p.poll() is None]
        self.assertEqual(len(alive), 1, f"exactly one narrator runs; alive={[p.pid for p in alive]}")
        winner = alive[0]
        loser = b if winner is a else a
        self.assertEqual(loser.returncode, 1, "the narrator that lost the start exits 1")
        self.assertIn("already running", loser.stderr.read())

        self.assertTrue(self.sock.is_socket(), "the winner's socket file is still there")
        status = _answers_status(self.sock)
        self.assertIsNotNone(status, "the socket answers status")
        self.assertEqual(self.pid_file.read_text().strip(), str(winner.pid), "the pid file names the narrator that serves")


class TestSocketOwnership(unittest.TestCase):
    """A narrator removes only the socket file it bound itself."""

    def setUp(self) -> None:
        import narrator_service

        self.ns = narrator_service
        self.tmp = Path(tempfile.mkdtemp(prefix="as-own-", dir="/tmp"))
        self.sock = self.tmp / "d.sock"
        assert self.sock != LIVE_SOCKET

    def tearDown(self) -> None:
        shutil.move(str(self.tmp), tempfile.mkdtemp(prefix="as-own-done-"))

    def _service(self):
        from unittest.mock import MagicMock

        return self.ns.NarratorService(socket_path=self.sock, sink=MagicMock(), synth=MagicMock(),
                                       config={**self.ns.load_config(), "provider": "mock"})

    def _listener(self, path: Path) -> socket.socket:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(str(path))
        s.listen(4)
        return s

    def test_a_narrator_that_finds_a_live_server_leaves_its_socket_in_place(self) -> None:
        live = self._listener(self.sock)
        try:
            ino = self.sock.stat().st_ino
            svc = self._service()
            with self.assertRaises(OSError):
                svc._start_socket_server()
            svc._stop_socket_server()
            svc._cleanup_socket_file()
            self.assertTrue(self.sock.is_socket(), "the live server's socket file stays")
            self.assertEqual(self.sock.stat().st_ino, ino, "and it is the same file")
        finally:
            live.close()

    def test_a_narrator_never_removes_a_socket_that_replaced_its_own(self) -> None:
        svc = self._service()
        svc._start_socket_server()
        # Another server takes the path (the old code's stale-unlink did this).
        self.sock.unlink()
        other = self._listener(self.sock)
        try:
            ino = self.sock.stat().st_ino
            svc._stop_socket_server()
            self.assertTrue(self.sock.is_socket(), "the other server's socket file stays")
            self.assertEqual(self.sock.stat().st_ino, ino)
        finally:
            other.close()

    def test_a_narrator_removes_its_own_socket_on_stop(self) -> None:
        svc = self._service()
        svc._start_socket_server()
        self.assertTrue(self.sock.is_socket())
        svc._stop_socket_server()
        self.assertFalse(self.sock.exists())

    def test_a_stale_socket_file_nobody_answers_on_is_reclaimed(self) -> None:
        dead = self._listener(self.sock)
        dead.close()  # the file stays; nothing listens
        self.assertTrue(self.sock.exists())
        svc = self._service()
        svc._start_socket_server()
        try:
            self.assertIsNotNone(_answers_status(self.sock), "the new narrator serves on the reclaimed path")
        finally:
            svc._stop_socket_server()


class TestStartLock(unittest.TestCase):
    """Starts are serialized: the lock is held for the narrator's life."""

    def setUp(self) -> None:
        import narrator_service

        self.ns = narrator_service
        self.tmp = Path(tempfile.mkdtemp(prefix="as-lock-", dir="/tmp"))
        self.lock = self.tmp / "narrator.lock"

    def tearDown(self) -> None:
        shutil.move(str(self.tmp), tempfile.mkdtemp(prefix="as-lock-done-"))

    def test_a_second_start_cannot_take_the_lock_while_the_first_holds_it(self) -> None:
        first = self.ns._take_start_lock(self.lock)
        self.assertIsNotNone(first)
        try:
            self.assertIsNone(self.ns._take_start_lock(self.lock))
        finally:
            os.close(first)
        # Released (as at the holder's exit): the next start takes it.
        again = self.ns._take_start_lock(self.lock)
        self.assertIsNotNone(again)
        os.close(again)


class TestStartScriptSerializes(unittest.TestCase):
    """Two runs of narrator_service_start.sh at once spawn one narrator.

    A copy of the script on a fake tree with a stub service that counts its
    spawns and writes its pid file after a delay (the window in which, at
    10:53, a second start found no pid file). Hermetic and silent."""

    STUB = """import os, time
root = os.environ["AUTO_SPEECH_TMP_ROOT"]
hold = open(os.path.join(root, "stub.hold"), "w")  # so the reaper finds it
with open(os.path.join(root, "spawns.log"), "a") as f:
    f.write(f"{os.getpid()}\\n")
time.sleep(0.6)
with open(os.path.join(root, "auto-speech-narrator-daemon.pid"), "w") as f:
    f.write(str(os.getpid()))
time.sleep(4)
"""

    def setUp(self) -> None:
        from test_narrator_start_label import _tree

        self.tmp, self.state, self.stub_bin, _ = _tree()
        PRIVATE_DIRS.append(self.state)
        MOVE_AFTER.append(self.tmp)
        (self.tmp / "plugin" / "scripts" / "python" / "narrator_service.py").write_text(self.STUB)

    def tearDown(self) -> None:
        # The start script detaches what it spawns (setsid): no process group
        # of ours reaches it, so it is found by the files it holds.
        survivors = reap_narrators(self.state)
        self.assertEqual(survivors, [], "every narrator the start script spawned is gone")

    def test_two_starts_at_once_spawn_one_narrator(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("AUTO_SPEECH_")}
        env.update(AUTO_SPEECH_TMP_ROOT=str(self.state), AUTO_SPEECH_START_VIA_LAUNCHD="never",
                   PATH=f"{self.stub_bin}:{env.get('PATH', '/usr/bin:/bin')}")
        script = self.tmp / "plugin" / "scripts" / "shell" / "narrator_service_start.sh"
        runs = [subprocess.Popen(["bash", str(script)], env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True) for _ in range(2)]
        outs = [r.communicate(timeout=30) for r in runs]
        spawns = (self.state / "spawns.log").read_text().split()
        self.assertEqual(len(spawns), 1, f"one narrator spawned, not {len(spawns)}: {outs}")
        self.assertTrue(any("already running" in o for o, _ in outs), outs)
        self.assertFalse((self.state / "auto-speech-narrator-start.lock.d").exists(), "the start lock is released")


class TestZzzNoNarratorLeftRunning(unittest.TestCase):
    """Runs last (unittest orders classes by name): no narrator holding a
    file under any private folder of this module is alive."""

    def test_no_narrator_started_here_is_still_running(self) -> None:
        self.assertTrue(PRIVATE_DIRS, "the narrator tests ran first")
        left = {str(d): narrators_under(d) for d in PRIVATE_DIRS}
        self.assertEqual({d: p for d, p in left.items() if p}, {}, "narrators left running")


def tearDownModule() -> None:
    left = {str(d): reap_narrators(d) for d in PRIVATE_DIRS}
    left = {d: p for d, p in left.items() if p}
    for d in MOVE_AFTER:
        if d.exists():
            shutil.move(str(d), tempfile.mkdtemp(prefix="as-done-"))
    assert not left, f"narrators survived the reaper: {left}"


if __name__ == "__main__":
    unittest.main(verbosity=2)
