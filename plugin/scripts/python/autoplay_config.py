"""End-of-turn autoplay configuration loader.

Controls how cli_rewrite.py shapes the spoken end-of-turn response:
either verbatim (lossless audio-friendly rewrite) or a summary at one
of three sizes.

Search order:
  1. $AUTO_SPEECH_AUTOPLAY_CONFIG (explicit override)
  2. ~/.config/auto-speech/autoplay.toml (user-level)
  3. <plugin>/config/autoplay.toml.example (shipped default)
  4. hardcoded defaults (mode=summary, summary_size=small)

Returns a dict with: mode, summary_size, prompt_path, config_path.
Mode is one of "verbatim" | "summary".
summary_size is one of "small" | "medium" | "large".
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
VALID_MODES = ("verbatim", "summary", "raw", "bypass", "as_is", "direct")
VALID_SIZES = ("small", "medium", "large")

_PROMPT_FILES = {
    "verbatim": "audio_rewrite_prompt.txt",
    "summary-small": "audio_summary_small_prompt.txt",
    "summary-medium": "audio_summary_medium_prompt.txt",
    "summary-large": "audio_summary_large_prompt.txt",
}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _prompts_dir() -> Path:
    return _project_root() / "plugin" / "prompts"


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    override = os.environ.get("AUTO_SPEECH_AUTOPLAY_CONFIG")
    if override:
        paths.append(Path(override))
    paths.append(Path.home() / ".config" / "auto-speech" / "autoplay.toml")
    paths.append(_project_root() / "config" / "autoplay.toml.example")
    return paths


def _resolve_prompt(mode: str, size: str) -> Path:
    if mode in ("raw", "bypass", "as_is", "direct", "verbatim"):
        key = "verbatim"
    else:
        key = f"summary-{size}"
    filename = _PROMPT_FILES.get(key)
    if filename is None:
        # unknown combination — fall back to medium
        filename = _PROMPT_FILES["summary-medium"]
    return _prompts_dir() / filename


def load_config() -> dict:
    config_path: Path | None = None
    raw: dict = {}
    for p in _candidate_paths():
        if p.exists() and p.is_file():
            try:
                raw = tomllib.loads(p.read_text(encoding="utf-8"))
                config_path = p
                break
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, TypeError) as exc:
                print(f"[autoplay] could not parse {p}: {exc}", file=sys.stderr)

    section = (raw.get("autoplay") if isinstance(raw, dict) else {}) or {}

    env_mode = os.environ.get("AUTO_SPEECH_AUTOPLAY_MODE", "").strip().lower()
    raw_mode = env_mode or str(section.get("mode", "summary")).lower()
    if raw_mode not in VALID_MODES:
        print(
            f"[autoplay] invalid mode {raw_mode!r}; falling back to 'summary'",
            file=sys.stderr,
        )
        mode = "summary"
    elif raw_mode in ("raw", "bypass", "as_is", "direct"):
        mode = "raw"
    else:
        mode = raw_mode

    bypass_env = os.environ.get("AUTO_SPEECH_BYPASS_LLM") or os.environ.get("AUTO_SPEECH_BYPASS_REWRITE")
    if bypass_env is not None:
        bypass_llm = bypass_env.strip().lower() in ("1", "true", "yes", "on")
    else:
        bypass_llm = bool(section.get("bypass_llm", section.get("bypass_rewrite", False)))

    if mode == "raw":
        bypass_llm = True
    elif bypass_llm:
        mode = "raw"

    size = str(section.get("summary_size", "small")).lower()
    if size not in VALID_SIZES:
        print(
            f"[autoplay] invalid summary_size {size!r}; falling back to 'small'",
            file=sys.stderr,
        )
        size = "small"

    coalesce = float(section.get("coalesce_seconds", 1.0))
    narration_wait_max = float(section.get("narration_wait_max_seconds", 90.0))

    return {
        "config_path": str(config_path) if config_path else None,
        "mode": mode,
        "bypass_llm": bypass_llm,
        "summary_size": size,
        "prompt_path": str(_resolve_prompt(mode, size)),
        "coalesce_seconds": coalesce,
        "narration_wait_max_seconds": narration_wait_max,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(load_config(), indent=2))
