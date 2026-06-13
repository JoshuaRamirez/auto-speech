"""Unit tests for cli_rewrite.py.

The real script shells out to `claude -p`, which we don't want to do
in tests. Instead we monkeypatch ClaudeCliRewriter.rewrite to a stub
and verify cli_rewrite picks the right prompt template by mode/size.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

import cli_rewrite  # noqa: E402
import claude_cli_rewriter  # noqa: E402


class _StubRewriter:
    """Stub that records the template it was given and returns a fixed
    string instead of calling out to claude -p."""

    last_template: str = ""

    def __init__(self, prompt_template: str) -> None:
        type(self).last_template = prompt_template
        if "{SOURCE}" not in prompt_template:
            raise ValueError("prompt_template must contain {SOURCE} placeholder")

    def rewrite(self, text: str, timeout_seconds: float = 90.0) -> str:
        return f"REWRITTEN({len(text)} chars)"


def _run(argv: list[str], stdin_text: str) -> tuple[int, str]:
    """Invoke cli_rewrite.main with patched rewriter and captured stdout."""
    real_class = claude_cli_rewriter.ClaudeCliRewriter
    real_stdin = sys.stdin
    real_stdout = sys.stdout
    cli_rewrite.ClaudeCliRewriter = _StubRewriter  # type: ignore[assignment]
    sys.stdin = io.StringIO(stdin_text)
    sys.stdout = io.StringIO()
    try:
        rc = cli_rewrite.main(argv)
        return rc, sys.stdout.getvalue()
    finally:
        cli_rewrite.ClaudeCliRewriter = real_class  # type: ignore[assignment]
        sys.stdin = real_stdin
        sys.stdout = real_stdout


def _with_autoplay_override(toml_body: str):
    class _CM:
        def __enter__(self):
            self._tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".toml", delete=False, encoding="utf-8"
            )
            self._tmp.write(toml_body)
            self._tmp.close()
            self._prev = os.environ.get("AUTO_SPEECH_AUTOPLAY_CONFIG")
            os.environ["AUTO_SPEECH_AUTOPLAY_CONFIG"] = self._tmp.name
            return self._tmp.name

        def __exit__(self, *a):
            if self._prev is None:
                os.environ.pop("AUTO_SPEECH_AUTOPLAY_CONFIG", None)
            else:
                os.environ["AUTO_SPEECH_AUTOPLAY_CONFIG"] = self._prev
            Path(self._tmp.name).unlink(missing_ok=True)

    return _CM()


def test_empty_stdin_returns_failure() -> None:
    rc, out = _run([], stdin_text="")
    assert rc == cli_rewrite.EXIT_REWRITE_FAIL
    assert out == ""


def test_config_default_picks_summary_small_prompt() -> None:
    with _with_autoplay_override("") as _:
        rc, out = _run([], stdin_text="hello world")
    assert rc == cli_rewrite.EXIT_OK
    assert "ONE TO THREE SENTENCES" in _StubRewriter.last_template, (
        f"expected small (1-3 sentence) prompt loaded; got: {_StubRewriter.last_template[:200]}"
    )


def test_mode_flag_overrides_config_to_verbatim() -> None:
    with _with_autoplay_override('[autoplay]\nmode = "summary"\nsummary_size = "small"\n'):
        rc, out = _run(["--mode", "verbatim"], stdin_text="hello")
    assert rc == cli_rewrite.EXIT_OK
    # Verbatim template has "Transform form, never meaning." marker.
    assert "Transform form" in _StubRewriter.last_template, (
        f"--mode verbatim should load audio_rewrite_prompt.txt; "
        f"got: {_StubRewriter.last_template[:200]}"
    )


def test_size_flag_overrides_config() -> None:
    with _with_autoplay_override('[autoplay]\nmode = "summary"\nsummary_size = "medium"\n'):
        rc, _out = _run(["--size", "large"], stdin_text="hello")
    assert rc == cli_rewrite.EXIT_OK
    assert "LONG" in _StubRewriter.last_template or "180 to 240" in _StubRewriter.last_template, (
        f"--size large should load large prompt; got: {_StubRewriter.last_template[:200]}"
    )


def test_each_summary_size_uses_distinct_prompt() -> None:
    seen = set()
    for size in ("small", "medium", "large"):
        with _with_autoplay_override(f'[autoplay]\nmode = "summary"\nsummary_size = "{size}"\n'):
            rc, _ = _run([], stdin_text="hi")
        assert rc == cli_rewrite.EXIT_OK
        seen.add(_StubRewriter.last_template)
    assert len(seen) == 3, f"each size should pick a distinct template; got {len(seen)} unique"


def test_rewriter_error_returns_failure() -> None:
    real_class = claude_cli_rewriter.ClaudeCliRewriter

    class _ErrorRewriter:
        def __init__(self, prompt_template: str) -> None:
            pass

        def rewrite(self, text: str, timeout_seconds: float = 90.0) -> str:
            raise claude_cli_rewriter.ClaudeCliRewriteError("simulated failure")

    real_stdin = sys.stdin
    real_stdout = sys.stdout
    cli_rewrite.ClaudeCliRewriter = _ErrorRewriter  # type: ignore[assignment]
    sys.stdin = io.StringIO("source")
    sys.stdout = io.StringIO()
    try:
        rc = cli_rewrite.main([])
    finally:
        cli_rewrite.ClaudeCliRewriter = real_class  # type: ignore[assignment]
        sys.stdin = real_stdin
        sys.stdout = real_stdout
    assert rc == cli_rewrite.EXIT_REWRITE_FAIL


def main() -> int:
    tests = [
        test_empty_stdin_returns_failure,
        test_config_default_picks_summary_small_prompt,
        test_mode_flag_overrides_config_to_verbatim,
        test_size_flag_overrides_config,
        test_each_summary_size_uses_distinct_prompt,
        test_rewriter_error_returns_failure,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"cli_rewrite: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
