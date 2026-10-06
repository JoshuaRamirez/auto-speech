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

from transcript_locator import (
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


def test_session_id_kwarg_wins_over_env_and_mtime() -> None:
    with (
        tempfile.TemporaryDirectory() as project_root_str,
        tempfile.TemporaryDirectory() as cwd_str,
    ):
        project_root = Path(project_root_str)
        cwd = Path(cwd_str)
        slug_dir = project_root / _slug_for(cwd)
        slug_dir.mkdir()
        target = slug_dir / "arg-session.jsonl"
        env_target = slug_dir / "env-session.jsonl"
        newer = slug_dir / "newer.jsonl"
        _make_jsonl(target)
        _make_jsonl(env_target, mtime_offset=50)
        _make_jsonl(newer, mtime_offset=100)

        loc = _patched_locator(project_root)
        prev = os.environ.get("CLAUDE_SESSION_ID")
        os.environ["CLAUDE_SESSION_ID"] = "env-session"
        try:
            # Explicit kwarg should win over both the env var and mtime.
            result = loc.locate(cwd, session_id="arg-session")
        finally:
            if prev is None:
                os.environ.pop("CLAUDE_SESSION_ID", None)
            else:
                os.environ["CLAUDE_SESSION_ID"] = prev

        assert result == target, f"kwarg should win; got {result}"


def test_claude_code_session_id_env_is_also_honored() -> None:
    # Newer Claude Code uses CLAUDE_CODE_SESSION_ID rather than the
    # older CLAUDE_SESSION_ID. The locator now accepts either.
    with (
        tempfile.TemporaryDirectory() as project_root_str,
        tempfile.TemporaryDirectory() as cwd_str,
    ):
        project_root = Path(project_root_str)
        cwd = Path(cwd_str)
        slug_dir = project_root / _slug_for(cwd)
        slug_dir.mkdir()
        cc_target = slug_dir / "cc-session.jsonl"
        newer = slug_dir / "newer.jsonl"
        _make_jsonl(cc_target)
        _make_jsonl(newer, mtime_offset=100)

        loc = _patched_locator(project_root)
        prev_old = os.environ.pop("CLAUDE_SESSION_ID", None)
        prev_new = os.environ.get("CLAUDE_CODE_SESSION_ID")
        os.environ["CLAUDE_CODE_SESSION_ID"] = "cc-session"
        try:
            result = loc.locate(cwd)
        finally:
            if prev_old is not None:
                os.environ["CLAUDE_SESSION_ID"] = prev_old
            if prev_new is None:
                os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            else:
                os.environ["CLAUDE_CODE_SESSION_ID"] = prev_new

        assert result == cc_target, (
            f"CLAUDE_CODE_SESSION_ID should be honored; got {result}"
        )


def test_session_id_env_picks_named_file_when_present() -> None:
    with (
        tempfile.TemporaryDirectory() as project_root_str,
        tempfile.TemporaryDirectory() as cwd_str,
    ):
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
    with (
        tempfile.TemporaryDirectory() as project_root_str,
        tempfile.TemporaryDirectory() as cwd_str,
    ):
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
    with (
        tempfile.TemporaryDirectory() as project_root_str,
        tempfile.TemporaryDirectory() as cwd_str,
    ):
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
    with (
        tempfile.TemporaryDirectory() as project_root_str,
        tempfile.TemporaryDirectory() as cwd_str,
    ):
        project_root = Path(project_root_str)
        cwd = Path(cwd_str)
        # Do NOT create slug_dir under project_root.
        loc = _patched_locator(project_root)
        prev = os.environ.pop("CLAUDE_SESSION_ID", None)
        try:
            try:
                loc.locate(cwd)
            except TranscriptNotFoundError as exc:
                msg = str(exc).lower()
                assert "no project directory" in msg or "matched cwd" in msg
                return
            raise AssertionError("expected TranscriptNotFoundError")
        finally:
            if prev is not None:
                os.environ["CLAUDE_SESSION_ID"] = prev


def test_dot_in_cwd_basename_maps_to_dash_in_slug() -> None:
    """Regression: a project dir with a dot in its basename, e.g.
    /Users/me/Developer/Some-Project/Data.Pipeline, lives under
    -Users-me-...-Data-Pipeline (dot → dash). The old
    slug-only-replaces-slash logic missed this."""
    with tempfile.TemporaryDirectory() as project_root_str:
        project_root = Path(project_root_str)
        # Synthesize the slug Claude Code would create.
        cwd = Path("/Users/me/Developer/Some-Project/Data.Pipeline")
        # Substitute dots AND slashes with dashes — that's what we
        # expect the locator to look for.
        slug_dir = project_root / "-Users-me-Developer-Some-Project-Data-Pipeline"
        slug_dir.mkdir(parents=True)
        target = slug_dir / "session-x.jsonl"
        _make_jsonl(target)

        loc = _patched_locator(project_root)
        prev = os.environ.pop("CLAUDE_SESSION_ID", None)
        try:
            # We can't actually cwd into a fake /Users/me path; pass cwd
            # explicitly. The locator will .resolve() it but since it
            # doesn't exist on disk, resolve() leaves it as-is on macOS.
            # Pass it as-is.
            with _patch_resolve_to_self(cwd):
                result = loc.locate(cwd)
        finally:
            if prev is not None:
                os.environ["CLAUDE_SESSION_ID"] = prev
        assert result == target, f"expected {target}, got {result}"


from contextlib import contextmanager


@contextmanager
def _patch_resolve_to_self(target: Path):
    """Make Path.resolve() return the path unchanged so a synthetic
    /Users/me/... path doesn't get rewritten on macOS via /private/var
    or similar symlinks."""
    from unittest.mock import patch

    with patch.object(Path, "resolve", lambda self, strict=False: target):
        yield


def test_raises_when_slug_dir_empty() -> None:
    with (
        tempfile.TemporaryDirectory() as project_root_str,
        tempfile.TemporaryDirectory() as cwd_str,
    ):
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


def test_locate_from_subdirectory_resolves_parent_slug() -> None:
    with (
        tempfile.TemporaryDirectory() as project_root_str,
        tempfile.TemporaryDirectory() as repo_str,
    ):
        project_root = Path(project_root_str)
        repo_dir = Path(repo_str)
        # Create a .git marker in repo_dir
        (repo_dir / ".git").mkdir()
        sub_dir = repo_dir / "tests" / "unit"
        sub_dir.mkdir(parents=True)

        slug_dir = project_root / _slug_for(repo_dir)
        slug_dir.mkdir()
        target = slug_dir / "session-sub.jsonl"
        _make_jsonl(target)

        loc = _patched_locator(project_root)
        result = loc.locate(sub_dir)
        assert result == target, f"expected target in parent slug dir; got {result}"


def test_global_session_id_lookup_finds_transcript_when_slug_missing() -> None:
    with (
        tempfile.TemporaryDirectory() as project_root_str,
        tempfile.TemporaryDirectory() as foreign_cwd_str,
    ):
        project_root = Path(project_root_str)
        foreign_cwd = Path(foreign_cwd_str)
        # Project exists under another slug
        other_slug_dir = project_root / "-some-other-project-dir"
        other_slug_dir.mkdir()
        target = other_slug_dir / "globally-unique-sid.jsonl"
        _make_jsonl(target)

        loc = _patched_locator(project_root)
        result = loc.locate(foreign_cwd, session_id="globally-unique-sid")
        assert result == target, f"expected global match for session_id; got {result}"


def test_deep_nested_subdirectory_resolves_git_root_slug() -> None:
    with (
        tempfile.TemporaryDirectory() as project_root_str,
        tempfile.TemporaryDirectory() as repo_str,
    ):
        project_root = Path(project_root_str)
        repo_dir = Path(repo_str)
        (repo_dir / ".git").mkdir()
        deep_dir = repo_dir / "src" / "pkg" / "sub" / "deep" / "nested"
        deep_dir.mkdir(parents=True)

        slug_dir = project_root / _slug_for(repo_dir)
        slug_dir.mkdir()
        target = slug_dir / "session-deep.jsonl"
        _make_jsonl(target)

        loc = _patched_locator(project_root)
        result = loc.locate(deep_dir)
        assert result == target, f"expected target in repo slug dir; got {result}"


def test_global_session_id_lookup_honors_claude_code_session_id_env() -> None:
    with (
        tempfile.TemporaryDirectory() as project_root_str,
        tempfile.TemporaryDirectory() as foreign_cwd_str,
    ):
        project_root = Path(project_root_str)
        foreign_cwd = Path(foreign_cwd_str)
        other_slug_dir = project_root / "-another-random-project"
        other_slug_dir.mkdir()
        target = other_slug_dir / "env-global-sid.jsonl"
        _make_jsonl(target)

        loc = _patched_locator(project_root)
        prev = os.environ.get("CLAUDE_CODE_SESSION_ID")
        try:
            os.environ["CLAUDE_CODE_SESSION_ID"] = "env-global-sid"
            result = loc.locate(foreign_cwd)
            assert result == target, f"expected global match from CLAUDE_CODE_SESSION_ID; got {result}"
        finally:
            if prev is not None:
                os.environ["CLAUDE_CODE_SESSION_ID"] = prev
            else:
                os.environ.pop("CLAUDE_CODE_SESSION_ID", None)


def main() -> int:
    tests = [
        test_session_id_kwarg_wins_over_env_and_mtime,
        test_claude_code_session_id_env_is_also_honored,
        test_session_id_env_picks_named_file_when_present,
        test_env_var_set_but_file_missing_falls_back_to_newest,
        test_no_env_var_uses_newest_jsonl,
        test_raises_when_slug_dir_missing,
        test_raises_when_slug_dir_empty,
        test_dot_in_cwd_basename_maps_to_dash_in_slug,
        test_locate_from_subdirectory_resolves_parent_slug,
        test_global_session_id_lookup_finds_transcript_when_slug_missing,
        test_deep_nested_subdirectory_resolves_git_root_slug,
        test_global_session_id_lookup_honors_claude_code_session_id_env,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"TranscriptLocator: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
