"""Children start clean and the process list is read directly (his rule, 26 Nov 2026:
"ban the clone-first call and start child programs clean, with nothing inherited unless named")."""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import clean_spawn
import kernel_procs

LEAK = {"CLAUDE_CODE_SESSION_ID": "s-1", "SOME_TOKEN": "secret", "CG_FOLLOW_FROM_LSN": "7", "AUTO_SPEECH_VOICE": "v"}


def _counting_spawn(calls):
    real = os.posix_spawn

    def spawn(path, argv, env, **kw):
        calls.append((path, list(argv), dict(env), kw))
        return real(path, argv, env, **kw)
    return spawn


class TestCleanSpawn(unittest.TestCase):
    def test_run_uses_posix_spawn_and_a_built_environment(self) -> None:
        calls: list = []
        with mock.patch.dict(os.environ, LEAK), mock.patch.object(os, "posix_spawn", _counting_spawn(calls)):
            res = clean_spawn.run(["env"], capture_output=True, text=True, timeout=20)
        self.assertEqual(res.returncode, 0)
        self.assertEqual(len(calls), 1, "not started by posix_spawn")
        self.assertTrue(calls[0][0].startswith("/"))
        names = {ln.split("=", 1)[0] for ln in res.stdout.splitlines() if "=" in ln}
        for leaked in ("CLAUDE_CODE_SESSION_ID", "SOME_TOKEN", "CG_FOLLOW_FROM_LSN"):
            self.assertNotIn(leaked, names)
        self.assertIn("AUTO_SPEECH_VOICE", names)
        self.assertIn("PATH", names)

    def test_names_and_extra_are_passed(self) -> None:
        with mock.patch.dict(os.environ, LEAK):
            env = clean_spawn.child_env(names=("CLAUDE_CODE_SESSION_ID",), extra={"X": "1"})
        self.assertEqual(env["CLAUDE_CODE_SESSION_ID"], "s-1")
        self.assertEqual(env["X"], "1")
        self.assertNotIn("SOME_TOKEN", env)

    def test_group_child_runs_in_its_own_group_and_talks_over_pipes(self) -> None:
        calls: list = []
        with mock.patch.object(os, "posix_spawn", _counting_spawn(calls)):
            child = clean_spawn.GroupChild(["/bin/sh", "-c", "read l; echo got:$l; echo err >&2; ps -o pgid= -p $$"],
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert child.stdin is not None
            child.stdin.write(b"hi\n")
            out, err = child.communicate(timeout=20)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][3].get("setpgroup"), 0)
        lines = out.decode().split()
        self.assertEqual(lines[0], "got:hi")
        self.assertEqual(int(lines[1]), child.pid)
        self.assertEqual(err, b"err\n")
        self.assertEqual(child.returncode, 0)

    def test_group_child_past_its_limit_is_killed_with_what_it_started(self) -> None:
        child = clean_spawn.GroupChild(["/bin/sh", "-c", "sleep 30 & sleep 30"], stdout=subprocess.PIPE)
        with self.assertRaises(subprocess.TimeoutExpired):
            child.communicate(timeout=0.3)
        child.kill_group(signal.SIGKILL)
        self.assertEqual(child.wait(5), -signal.SIGKILL)
        child.close()
        time.sleep(0.1)
        with self.assertRaises(ProcessLookupError):
            os.killpg(child.pid, 0)

    def test_a_missing_program_is_refused(self) -> None:
        with self.assertRaises(OSError):
            clean_spawn.GroupChild(["no-such-program-auto-speech"])


@unittest.skipUnless(sys.platform == "darwin", "the kernel read is macOS only")
class TestKernelProcs(unittest.TestCase):
    def test_the_table_agrees_with_ps_for_this_process_and_its_parent(self) -> None:
        table = kernel_procs.process_table()
        assert table is not None
        ppid, command = table[os.getpid()]
        self.assertEqual(ppid, os.getppid())
        self.assertIn("test_clean_spawn", command + " ".join(sys.argv))
        self.assertGreater(len(table), 20)
        self.assertEqual(kernel_procs.parent_and_command(os.getpid())[0], os.getppid())
        self.assertIsNone(kernel_procs.parent_and_command(999_999))

    def test_a_child_command_line_is_read_whole(self) -> None:
        child = subprocess.Popen(["/bin/sleep", "5"])
        try:
            self.assertEqual(kernel_procs.command_of(child.pid), "/bin/sleep 5")
        finally:
            child.kill()
            child.wait()


if __name__ == "__main__":
    unittest.main()
