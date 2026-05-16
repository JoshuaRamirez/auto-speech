"""Unit tests for narrator_config.load_config().

Covers the precedence order:
  1. $AUTO_SPEECH_NARRATOR_CONFIG (explicit override)
  2. ~/.config/auto-speech/narrator.toml (user-level, not tested directly
     to avoid touching the real user's config)
  3. <plugin>/config/narrator.toml.example (shipped default)
  4. hardcoded fallback when nothing parses

And the per-field defaults / overrides.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

# Import the module so we can both call load_config() AND clear the env
# var between cases without leaking state.
import narrator_config  # noqa: E402


def _with_override(toml_body: str):
    """Returns a context-manager-like helper: write toml to a temp file,
    point AUTO_SPEECH_NARRATOR_CONFIG at it, load, then clean up."""

    class _CM:
        def __enter__(self):
            self._tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".toml", delete=False, encoding="utf-8"
            )
            self._tmp.write(toml_body)
            self._tmp.close()
            self._prev = os.environ.get("AUTO_SPEECH_NARRATOR_CONFIG")
            os.environ["AUTO_SPEECH_NARRATOR_CONFIG"] = self._tmp.name
            return self._tmp.name

        def __exit__(self, *a):
            if self._prev is None:
                os.environ.pop("AUTO_SPEECH_NARRATOR_CONFIG", None)
            else:
                os.environ["AUTO_SPEECH_NARRATOR_CONFIG"] = self._prev
            Path(self._tmp.name).unlink(missing_ok=True)

    return _CM()


def test_env_override_takes_precedence() -> None:
    body = """
[narrator]
provider = "mock"
model = "test-model-id"
max_tokens = 7
silence_seconds = 1.5
idle_shutdown_seconds = 2.5
min_events_per_phase = 4
"""
    with _with_override(body) as path:
        cfg = narrator_config.load_config()
    assert cfg["config_path"] == path
    assert cfg["provider"] == "mock"
    assert cfg["model"] == "test-model-id"
    assert cfg["max_tokens"] == 7
    assert cfg["silence_seconds"] == 1.5
    assert cfg["idle_shutdown_seconds"] == 2.5
    assert cfg["min_events_per_phase"] == 4


def test_defaults_when_section_missing() -> None:
    # Empty TOML — load_config falls back through to hardcoded defaults.
    with _with_override("") as _:
        cfg = narrator_config.load_config()
    assert cfg["provider"] == "mock"
    assert cfg["model"].startswith("mlx-community/")
    assert cfg["max_tokens"] == 60
    assert cfg["silence_seconds"] == 8.0
    assert cfg["idle_shutdown_seconds"] == 600.0
    assert cfg["min_events_per_phase"] == 1


def test_relative_prompt_path_resolves_against_config_dir() -> None:
    # Write the config in a temp dir and the prompt file next to it.
    with tempfile.TemporaryDirectory() as td:
        prompt = Path(td) / "my_prompt.txt"
        prompt.write_text("hello {events}", encoding="utf-8")
        cfg_path = Path(td) / "narrator.toml"
        cfg_path.write_text(
            f'[narrator]\nprovider = "mock"\nprompt_template = "my_prompt.txt"\n',
            encoding="utf-8",
        )
        prev = os.environ.get("AUTO_SPEECH_NARRATOR_CONFIG")
        os.environ["AUTO_SPEECH_NARRATOR_CONFIG"] = str(cfg_path)
        try:
            cfg = narrator_config.load_config()
        finally:
            if prev is None:
                os.environ.pop("AUTO_SPEECH_NARRATOR_CONFIG", None)
            else:
                os.environ["AUTO_SPEECH_NARRATOR_CONFIG"] = prev
        assert cfg["prompt_template_path"] == str(prompt.resolve()), (
            f"expected {prompt}, got {cfg['prompt_template_path']}"
        )


def test_absolute_prompt_path_kept_as_is() -> None:
    body = """
[narrator]
provider = "mock"
prompt_template = "/abs/path/prompt.txt"
"""
    with _with_override(body) as _:
        cfg = narrator_config.load_config()
    assert cfg["prompt_template_path"] == "/abs/path/prompt.txt"


def test_malformed_toml_falls_through_to_next_candidate() -> None:
    # Garbage TOML in the override → load_config logs to stderr and falls
    # back to the next candidate (the shipped example), so we still get a
    # usable config dict.
    body = "this is not = valid [toml at all"
    with _with_override(body) as _:
        cfg = narrator_config.load_config()
    # Either the shipped example provided values, or hardcoded defaults
    # kicked in. Either way the dict is well-formed.
    assert "provider" in cfg
    assert "min_events_per_phase" in cfg
    assert isinstance(cfg["max_tokens"], int)


def main() -> int:
    tests = [
        test_env_override_takes_precedence,
        test_defaults_when_section_missing,
        test_relative_prompt_path_resolves_against_config_dir,
        test_absolute_prompt_path_kept_as_is,
        test_malformed_toml_falls_through_to_next_candidate,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"narrator_config: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
