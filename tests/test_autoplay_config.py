"""Unit tests for autoplay_config.load_config().

Covers mode/size validation, prompt-path resolution by mode-size combo,
and bad input handling (fall back to defaults, don't crash).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

import autoplay_config


def _with_override(toml_body: str):
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


def test_verbatim_mode_resolves_to_audio_rewrite_prompt() -> None:
    body = '[autoplay]\nmode = "verbatim"\nsummary_size = "medium"\n'
    with _with_override(body) as _:
        cfg = autoplay_config.load_config()
    assert cfg["mode"] == "verbatim"
    # Verbatim mode points at the lossless audio_rewrite_prompt regardless
    # of summary_size.
    assert cfg["prompt_path"].endswith("audio_rewrite_prompt.txt"), cfg["prompt_path"]


def test_summary_size_each_resolves_to_distinct_prompt() -> None:
    for size in ("small", "medium", "large"):
        body = f'[autoplay]\nmode = "summary"\nsummary_size = "{size}"\n'
        with _with_override(body) as _:
            cfg = autoplay_config.load_config()
        assert cfg["mode"] == "summary"
        assert cfg["summary_size"] == size
        assert cfg["prompt_path"].endswith(f"audio_summary_{size}_prompt.txt"), (
            f"size={size} got {cfg['prompt_path']}"
        )


def test_invalid_mode_falls_back_to_summary() -> None:
    body = '[autoplay]\nmode = "shout-it-from-the-rooftops"\n'
    with _with_override(body) as _:
        cfg = autoplay_config.load_config()
    assert cfg["mode"] == "summary", "invalid mode must fall back to 'summary'"


def test_invalid_summary_size_falls_back_to_small() -> None:
    body = '[autoplay]\nmode = "summary"\nsummary_size = "humungous"\n'
    with _with_override(body) as _:
        cfg = autoplay_config.load_config()
    assert cfg["summary_size"] == "small"
    assert cfg["prompt_path"].endswith("audio_summary_small_prompt.txt")


def test_empty_toml_uses_defaults() -> None:
    with _with_override("") as _:
        cfg = autoplay_config.load_config()
    # Defaults: summary / small (a 1-to-3-sentence "what happened" read).
    assert cfg["mode"] == "summary"
    assert cfg["summary_size"] == "small"
    assert cfg["prompt_path"].endswith("audio_summary_small_prompt.txt")


def test_resolved_prompt_files_actually_exist_on_disk() -> None:
    # The plugin ships four prompt files; load_config resolves to their
    # actual paths. They must exist or the autoplay's cli_rewrite would
    # silently fall back to verbatim every time.
    project_root = Path(__file__).resolve().parents[1]
    prompts = project_root / "plugin" / "prompts"
    for filename in (
        "audio_rewrite_prompt.txt",
        "audio_summary_small_prompt.txt",
        "audio_summary_medium_prompt.txt",
        "audio_summary_large_prompt.txt",
    ):
        path = prompts / filename
        assert path.is_file(), f"missing shipped prompt: {path}"
        # Every prompt must contain the {SOURCE} placeholder or
        # claude_cli_rewriter will reject it at runtime.
        body = path.read_text(encoding="utf-8")
        assert "{SOURCE}" in body, f"prompt {filename} missing {{SOURCE}} placeholder"


def test_shipped_example_toml_defaults_to_small() -> None:
    # The shipped example TOML is candidate #3 in the search order, so its
    # values ARE the effective defaults for a fresh install. Pin them.
    example = Path(__file__).resolve().parents[1] / "config" / "autoplay.toml.example"
    assert example.is_file(), f"missing shipped example: {example}"
    import tomllib

    parsed = tomllib.loads(example.read_text(encoding="utf-8"))
    section = parsed.get("autoplay", {})
    assert section.get("mode") == "summary", section
    assert section.get("summary_size") == "small", section


def test_small_prompt_targets_one_to_three_sentences() -> None:
    # The default spoken output contract: a 1-to-3-sentence summary of
    # what happened. Pin the instruction in the shipped small prompt.
    prompt = (
        Path(__file__).resolve().parents[1]
        / "plugin" / "prompts" / "audio_summary_small_prompt.txt"
    )
    body = prompt.read_text(encoding="utf-8")
    assert "ONE TO THREE SENTENCES" in body, "small prompt must instruct 1-3 sentences"


def main() -> int:
    tests = [
        test_verbatim_mode_resolves_to_audio_rewrite_prompt,
        test_summary_size_each_resolves_to_distinct_prompt,
        test_invalid_mode_falls_back_to_summary,
        test_invalid_summary_size_falls_back_to_small,
        test_empty_toml_uses_defaults,
        test_resolved_prompt_files_actually_exist_on_disk,
        test_shipped_example_toml_defaults_to_small,
        test_small_prompt_targets_one_to_three_sentences,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"autoplay_config: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
