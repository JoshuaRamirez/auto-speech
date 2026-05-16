"""Unit tests for TranscriptLocator.

Tests cover:
  - CLAUDE_SESSION_ID env-var preferred when its file exists
  - newest-jsonl fallback when env var unset
  - newest-jsonl fallback when env var set but file missing
  - error when project slug dir missing
  - error when slug dir empty

We patch the locator's PROJECTS_ROOT class attribute to a temp dir so we
can synthesize jsonl files without touching the real ~/.claude.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from transcript_locator import (  # noqa: E402
    TranscriptLocator,
    TranscriptNotFoundError,
)


def _make_jsonl(path: Path, mtime_offset: float = 0.0) -> None:
    path.write_text("{}\n", encoding="utf-8")
    if mtime_offset:
        now = time.time()
        os.utime(path, (now + mtime_offset, now + mtime_offset))


def _patched_locator(tmp_root: Path) -> TranscriptLocator:
    loc = TranscriptLocator()
    loc.PROJECTS_ROOT = tmp_root  # type: ignore[assignment]
    return loc


def _slug_for(cwd: Path) -> str:
    # Match the locator's own behavior — it calls cwd.resolve(), which on
    # macOS turns /var/... into /private/var/... due to a symlink. The
    # slug must be built from the same resolved path or the project-dir
    # lookup will miss.
    return str(cwd.resolve()).replace("/", "-")


def test_session_id_env_picks_named_file_when_present() -> None:
    with tempfile.TemporaryDirectory() as project_root_str:
        with tempfile.TemporaryDirectory() as cwd_str:
            project_root = Path(project_root_str)
            cwd = Path(cwd_str)
            slug_dir = project_root / _slug_for(cwd)
            slug_dir.mkdir()
            named = slug_dir / "session-id-abc.jsonl"
            other = slug_dir / "other.jsonl"
            _make_jsonl(named)
            _make_jsonl(other, mtime_offset=100)  # newer; would beat named if env var ignored

            loc = _patched_locator(project_root)
            prev = os.environ.get("CLAUDE_SESSION_ID")
            os.environ["CLAUDE_SESSION_ID"] = "session-id-abc"
            try:
                result = loc.locate(cwd)
            finally:
                if prev is None:
                    os.environ.pop("CLAUDE_SESSION_ID", None)
                else:
                    os.environ["CLAUDE_SESSION_ID"] = prev

            assert result == named, (
                f"env-var session should win over newer file; got {result}"
            )


def test_env_var_set_but_file_missing_falls_back_to_newest() -> None:
    with tempfile.TemporaryDirectory() as project_root_str:
        with tempfile.TemporaryDirectory() as cwd_str:
            project_root = Path(project_root_str)
            cwd = Path(cwd_str)
            slug_dir = project_root / _slug_for(cwd)
            slug_dir.mkdir()
            old = slug_dir / "old.jsonl"
            new = slug_dir / "new.jsonl"
            _make_jsonl(old)
            _make_jsonl(new, mtime_offset=100)

            loc = _patched_locator(project_root)
            prev = os.environ.get("CLAUDE_SESSION_ID")
            os.environ["CLAUDE_SESSION_ID"] = "missing-session"
            try:
                result = loc.locate(cwd)
            finally:
                if prev is None:
                    os.environ.pop("CLAUDE_SESSION_ID", None)
                else:
                    os.environ["CLAUDE_SESSION_ID"] = prev

            assert result == new, f"expected newest fallback; got {result}"


def test_no_env_var_uses_newest_jsonl() -> None:
    with tempfile.TemporaryDirectory() as project_root_str:
        with tempfile.TemporaryDirectory() as cwd_str:
            project_root = Path(project_root_str)
            cwd = Path(cwd_str)
            slug_dir = project_root / _slug_for(cwd)
            slug_dir.mkdir()
            for i, name in enumerate(["a.jsonl", "b.jsonl", "c.jsonl"]):
                _make_jsonl(slug_dir / name, mtime_offset=i * 10)
            # c.jsonl is newest (largest mtime offset)

            loc = _patched_locator(project_root)
            prev = os.environ.pop("CLAUDE_SESSION_ID", None)
            try:
                result = loc.locate(cwd)
            finally:
                if prev is not None:
                    os.environ["CLAUDE_SESSION_ID"] = prev

            assert result.name == "c.jsonl", (
                f"expected newest jsonl c.jsonl; got {result.name}"
            )


def test_raises_when_slug_dir_missing() -> None:
    with tempfile.TemporaryDirectory() as project_root_str:
        with tempfile.TemporaryDirectory() as cwd_str:
            project_root = Path(project_root_str)
            cwd = Path(cwd_str)
            # Do NOT create slug_dir under project_root.
            loc = _patched_locator(project_root)
            prev = os.environ.pop("CLAUDE_SESSION_ID", None)
            try:
                try:
                    loc.locate(cwd)
                except TranscriptNotFoundError as exc:
                    assert "no project directory" in str(exc).lower() or "project" in str(exc).lower()
                    return
                raise AssertionError("expected TranscriptNotFoundError")
            finally:
                if prev is not None:
                    os.environ["CLAUDE_SESSION_ID"] = prev


def test_raises_when_slug_dir_empty() -> None:
    with tempfile.TemporaryDirectory() as project_root_str:
        with tempfile.TemporaryDirectory() as cwd_str:
            project_root = Path(project_root_str)
            cwd = Path(cwd_str)
            slug_dir = project_root / _slug_for(cwd)
            slug_dir.mkdir()  # empty

            loc = _patched_locator(project_root)
            prev = os.environ.pop("CLAUDE_SESSION_ID", None)
            try:
                try:
                    loc.locate(cwd)
                except TranscriptNotFoundError as exc:
                    assert "no" in str(exc).lower() and "jsonl" in str(exc).lower()
                    return
                raise AssertionError("expected TranscriptNotFoundError")
            finally:
                if prev is not None:
                    os.environ["CLAUDE_SESSION_ID"] = prev


def main() -> int:
    tests = [
        test_session_id_env_picks_named_file_when_present,
        test_env_var_set_but_file_missing_falls_back_to_newest,
        test_no_env_var_uses_newest_jsonl,
        test_raises_when_slug_dir_missing,
        test_raises_when_slug_dir_empty,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"TranscriptLocator: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
