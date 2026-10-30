"""No test writes to his live speech store (#m1030-1241-6e0a).

His 2026-10-30 12:41 "messed up rows": 258 rows "req_0222".."req_0349" and
"success_item_3" in his speech player, timed 11:02 and 11:03. They were
written by tests/e2e/test_tier5_adversarial_lifecycle.py, run by hand with a
private AUTO_SPEECH_TMP_ROOT but no AUTO_SPEECH_HISTORY_DIR: its
NarratorService took SpeechHistory()'s default, ~/.local/state/auto-speech/history.

Here: every test file imports tests/isolation.py before any auto-speech
module; run_all.sh takes its exports from it; it names every state path the
product reads from a variable; and that very test, run in a subprocess the
way it was run at 11:03 (and with no variables at all), leaves his live
folder as it was and writes its line to the private store instead.
"""

from __future__ import annotations

import isolation  # noqa: F401,E402,I001  -- before any auto-speech module: private stores only (#m1030-1241-6e0a)

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
PRODUCT = ROOT / "plugin" / "scripts" / "python"
PYTHON = sys.executable
WRITER = TESTS / "e2e" / "test_tier5_adversarial_lifecycle.py"
WRITER_TEST = "TestTier5AdversarialWorkerAndHandlerRobustness.test_worker_resilience_to_synthesis_exceptions"
FIXTURE_TEXT = b"success_item_3"

# Variables the product reads a path from that it only ever reads (his own
# settings): a private HOME covers them; they need no private file.
READ_ONLY = {
    "AUTO_SPEECH_NARRATOR_CONFIG",
    "AUTO_SPEECH_AUTOPLAY_CONFIG",
    "AUTO_SPEECH_SPEAKER_CONFIG",
    "AUTO_SPEECH_SOUND_YIELD_CONFIG",
    "AUTO_SPEECH_AGENTS_DIR",
}


def _product_modules() -> set[str]:
    return {p.stem for p in PRODUCT.glob("*.py")}


def _test_files() -> list[Path]:
    return sorted([*TESTS.glob("test_*.py"), *(TESTS / "e2e").glob("test_*.py"), TESTS / "e2e" / "harness.py"])


def _snapshot(folder: Path) -> dict[str, tuple[float, int]]:
    out: dict[str, tuple[float, int]] = {}
    if not folder.is_dir():
        return out
    for dirpath, _dirs, files in os.walk(folder):
        for name in files:
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                continue
            out[str(p.relative_to(folder))] = (st.st_mtime, st.st_size)
    return out


def _changed_with_fixture(folder: Path, before: dict, after: dict) -> list[str]:
    """Files new or changed in `folder` that hold the fixture's text. His live
    narrator may write meanwhile; only what the test wrote counts."""
    hits = []
    for rel, stamp in after.items():
        if before.get(rel) == stamp:
            continue
        try:
            if FIXTURE_TEXT in (folder / rel).read_bytes():
                hits.append(rel)
        except OSError:
            continue
    return hits


class TestEveryTestIsIsolated(unittest.TestCase):
    def test_every_test_file_imports_the_bootstrap_before_any_auto_speech_module(self):
        product = _product_modules()
        late: list[str] = []
        for f in _test_files():
            tree = ast.parse(f.read_text(encoding="utf-8"))
            first_product = None
            bootstrap = None
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    top = name.split(".")[0]
                    if name == "isolation" and bootstrap is None:
                        bootstrap = node.lineno
                    elif (top in product or name.startswith("tests.")) and first_product is None:
                        first_product = node.lineno
            if bootstrap is None or (first_product is not None and first_product < bootstrap):
                late.append(f"{f.relative_to(ROOT)} (bootstrap line {bootstrap}, first auto-speech import line {first_product})")
        self.assertEqual(late, [], "these import auto-speech before tests/isolation.py")

    def test_the_runner_takes_its_exports_from_the_bootstrap(self):
        runner = (TESTS / "run_all.sh").read_text(encoding="utf-8")
        self.assertIn('isolation.py" --exports "$STATE_DIR"', runner)
        out = subprocess.run([PYTHON, str(TESTS / "isolation.py"), "--exports", "/tmp/as-iso-x"],
                             capture_output=True, text=True, check=True).stdout
        for name in [*isolation.PATHS, "HOME", isolation.MARK_ENV]:
            self.assertIn(f"export {name}=", out)
        self.assertNotIn(str(isolation.real_home()) + "/.local", out)
        shutil.rmtree("/tmp/as-iso-x", ignore_errors=True)

    def test_every_state_path_the_product_reads_from_a_variable_is_given_a_private_one(self):
        names: set[str] = set()
        pattern = re.compile(r"""["']((?:AUTO_SPEECH|UI_UTILITY)_[A-Z_]*(?:FILE|DIR|SOCK|CONFIG|ROOT))["']""")
        for f in PRODUCT.glob("*.py"):
            names.update(pattern.findall(f.read_text(encoding="utf-8")))
        missing = sorted(n for n in names if n not in isolation.PATHS and n not in READ_ONLY)
        self.assertEqual(missing, [], "add each to tests/isolation.py PATHS (or READ_ONLY if the product only reads it)")

    def test_this_process_is_isolated(self):
        live = isolation.live_state_dir()
        self.assertNotEqual(Path.home(), isolation.real_home())
        for name in isolation.PATHS:
            self.assertFalse(os.environ[name].startswith(str(live)), name)


class TestTheWriterOfTheJunkNowWritesPrivately(unittest.TestCase):
    """The 11:03 test, run in a subprocess as it was run then."""

    def setUp(self) -> None:
        self.scratch = Path(tempfile.mkdtemp(prefix="as-live-guard-", dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.home = self.scratch / "home"
        self.home.mkdir()
        self.live = isolation.live_state_dir()

    def run_writer(self, extra: dict[str, str]) -> subprocess.CompletedProcess:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.home),
               isolation.REAL_HOME_ENV: str(isolation.real_home()), "AUTO_SPEECH_MPV_AO": "null", **extra}
        return subprocess.run([PYTHON, str(WRITER), WRITER_TEST], env=env, cwd=str(self.scratch),
                              capture_output=True, text=True, timeout=120)

    def check(self, extra: dict[str, str]) -> None:
        before = _snapshot(self.live)
        proc = self.run_writer(extra)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        after = _snapshot(self.live)
        self.assertEqual(_changed_with_fixture(self.live, before, after), [],
                         f"the test wrote into his live store {self.live}")
        self.assertFalse((self.home / ".local" / "state" / "auto-speech").exists(),
                         "nor into the HOME it was given: the bootstrap's private home took it")

    def test_run_by_hand_as_at_11_03_without_a_history_dir(self):
        # The 11:02/11:03 command: a private TMP_ROOT, socket and missed
        # list, and no AUTO_SPEECH_HISTORY_DIR.
        s = self.scratch / "s"
        s.mkdir()
        self.check({"AUTO_SPEECH_TMP_ROOT": str(s), "AUTO_SPEECH_DAEMON_SOCK": str(s / "auto-speech-daemon.sock"),
                    "AUTO_SPEECH_MISSED_FILE": str(s / "missed.json"), "AUTO_SPEECH_START_VIA_LAUNCHD": "never",
                    "AUTO_SPEECH_PLAYER_LAUNCH": "never", "UI_UTILITY_SOCK": str(s / "ui.sock"),
                    "UI_UTILITY_SILENT": "1"})
        written = [p for p in (s / "history").glob("*.json") if FIXTURE_TEXT in p.read_bytes()]
        self.assertEqual(len(written), 1, "its line went to the private history under its TMP_ROOT")

    def test_run_with_no_variables_at_all(self):
        self.check({})


if __name__ == "__main__":
    unittest.main()
