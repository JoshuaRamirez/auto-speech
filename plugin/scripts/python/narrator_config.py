"""Narrator config loader.

Search order:
  1. $AUTO_SPEECH_NARRATOR_CONFIG (explicit override)
  2. ~/.config/auto-speech/narrator.toml (user-level)
  3. <plugin>/config/narrator.toml.example (shipped default)

Returns a flat dict with keys: provider, model, prompt_template_path,
max_tokens, silence_seconds, idle_shutdown_seconds.
"""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path


def _project_root() -> Path:
    # plugin/scripts/python/narrator_config.py → up 3
    return Path(__file__).resolve().parents[3]


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    override = os.environ.get("AUTO_SPEECH_NARRATOR_CONFIG")
    if override:
        paths.append(Path(override))
    paths.append(Path.home() / ".config" / "auto-speech" / "narrator.toml")
    paths.append(_project_root() / "config" / "narrator.toml.example")
    return paths


def load_config() -> dict:
    config_path: Path | None = None
    raw: dict = {}
    for p in _candidate_paths():
        if p.exists() and p.is_file():
            try:
                raw = tomllib.loads(p.read_text(encoding="utf-8"))
                config_path = p
                break
            except Exception as exc:
                print(f"[narrator] could not parse {p}: {exc}", file=sys.stderr)

    section = (raw.get("narrator") if isinstance(raw, dict) else {}) or {}

    prompt_tmpl = section.get("prompt_template")
    if prompt_tmpl:
        prompt_path = Path(prompt_tmpl)
        if not prompt_path.is_absolute():
            base = config_path.parent if config_path is not None else _project_root() / "config"
            prompt_path = (base / prompt_tmpl).resolve()
    else:
        prompt_path = _project_root() / "config" / "narrator_prompt_newscaster.txt"

    return {
        "config_path": str(config_path) if config_path else None,
        "provider": section.get("provider", "mock"),
        "model": section.get("model", "mlx-community/Qwen2.5-3B-Instruct-4bit"),
        "prompt_template_path": str(prompt_path),
        "max_tokens": int(section.get("max_tokens", 60)),
        "silence_seconds": float(section.get("silence_seconds", 8.0)),
        "idle_shutdown_seconds": float(section.get("idle_shutdown_seconds", 600.0)),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(load_config(), indent=2))
