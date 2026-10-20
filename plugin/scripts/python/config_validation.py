"""Config validation — turn silent config typos into visible warnings.

The loaders intentionally default on bad values so the tool keeps running,
but that hides misconfiguration (an unknown key, a wrong-typed number, an
invalid enum). This module validates the user's TOML against the known
schema and returns human-readable problems WITHOUT changing runtime
behavior; the doctor surfaces them as a loud WARN. Nothing here raises.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Field:
    name: str
    kind: str  # "str" | "int" | "number" | "enum" | "bool" | "str_list" | "str_map"
    allowed: tuple = ()  # for kind == "enum"
    minimum: float | None = None  # inclusive lower bound for numeric kinds


# Mirrors the keys read by autoplay_config.load_config / resolve_config.
AUTOPLAY_FIELDS = (
    Field("mode", "enum", allowed=("verbatim", "summary")),
    Field("summary_size", "enum", allowed=("small", "medium", "large")),
    Field("coalesce_seconds", "number", minimum=0),
    Field("narration_wait_max_seconds", "number", minimum=0),
    Field("bypass_llm", "bool"),
    Field("bypass_rewrite", "bool"),  # the older name autoplay_config still reads
)

# Mirrors the keys read by narrator_config.load_config.
NARRATOR_FIELDS = (
    Field("provider", "enum", allowed=("mlx", "ollama", "mock")),
    Field("model", "str"),
    Field("prompt_template", "str"),
    Field("max_tokens", "int", minimum=1),
    Field("silence_seconds", "number", minimum=0),
    Field("idle_shutdown_seconds", "number", minimum=0),
    Field("min_events_per_phase", "int", minimum=1),
    Field("max_queue_depth", "int", minimum=1),
    Field("ollama_host", "str"),
    Field("synthesis_timeout_factor", "number", minimum=0),
    Field("play_timeout_margin_seconds", "number", minimum=0),
    Field("play_stall_seconds", "number", minimum=0),
    Field("load_timeout_scale_cap", "number", minimum=0),
    Field("timeout_retries", "int", minimum=0),
    Field("retry_load_per_core", "number", minimum=0),
    Field("retry_wait_seconds", "number", minimum=0),
)


# Mirrors the keys read by sound_yield.SoundYieldSettings.from_mapping.
SOUND_YIELD_FIELDS = (
    Field("enabled", "bool"),
    Field("exceptions", "str_list"),
    Field("hold_for", "enum", allowed=("players", "any")),
    Field("players", "str_list"),
    Field("max_wait_seconds", "number", minimum=0),
    Field("poll_seconds", "number", minimum=0),
    Field("meeting_hold", "bool"),
    Field("meeting_apps", "str_list"),
    Field("meeting_summary", "bool"),
)


# Mirrors the keys read by speaker.SpeakerSettings.from_mapping.
SPEAKER_FIELDS = (
    Field("enabled", "bool"),
    Field("quiet_gap_seconds", "number", minimum=0),
    Field("agents_dir", "str"),
    Field("names", "str_map"),
)


# Mirrors the keys read by speech_history.HistorySettings.from_mapping.
HISTORY_FIELDS = (
    Field("enabled", "bool"),
    Field("keep", "int", minimum=1),
    Field("player", "bool"),
)


def _is_int(v: object) -> bool:
    # bool is a subclass of int in Python; TOML booleans are not integers.
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_section(section: dict, fields: tuple[Field, ...], section_name: str) -> list[str]:
    """Validate a parsed [section_name] table against `fields`.

    Reports unknown keys, type mismatches, out-of-range numbers, and invalid
    enum values. Present keys only — absent keys fall back to defaults and
    are not problems."""
    problems: list[str] = []
    by_name = {f.name: f for f in fields}

    for key in section:
        if key not in by_name:
            problems.append(f"[{section_name}] unknown key {key!r} (ignored)")

    for f in fields:
        if f.name not in section:
            continue
        val = section[f.name]
        if f.kind == "str":
            if not isinstance(val, str):
                problems.append(
                    f"[{section_name}] {f.name} should be a string, got {type(val).__name__}"
                )
        elif f.kind == "int":
            if not _is_int(val):
                problems.append(f"[{section_name}] {f.name} should be an integer, got {val!r}")
            elif f.minimum is not None and val < f.minimum:
                problems.append(f"[{section_name}] {f.name} must be >= {f.minimum:g}, got {val}")
        elif f.kind == "number":
            if not _is_number(val):
                problems.append(f"[{section_name}] {f.name} should be a number, got {val!r}")
            elif f.minimum is not None and val < f.minimum:
                problems.append(f"[{section_name}] {f.name} must be >= {f.minimum:g}, got {val}")
        elif f.kind == "enum" and (not isinstance(val, str) or val.lower() not in f.allowed):
            problems.append(f"[{section_name}] {f.name} must be one of {f.allowed}, got {val!r}")
        elif f.kind == "bool" and not isinstance(val, bool):
            problems.append(f"[{section_name}] {f.name} should be true or false, got {val!r}")
        elif f.kind == "str_list" and (
            not isinstance(val, list) or any(not isinstance(x, str) for x in val)
        ):
            problems.append(f"[{section_name}] {f.name} should be a list of strings, got {val!r}")
        elif f.kind == "str_map" and (
            not isinstance(val, dict) or any(not isinstance(x, str) for x in val.values())
        ):
            problems.append(f"[{section_name}] {f.name} should be a table of string = string, got {val!r}")

    return problems


def validate_toml_file(path: Path, section_name: str, fields: tuple[Field, ...]) -> list[str]:
    """Validate the [section_name] table of a TOML file. A missing file is
    not a problem (defaults apply); a parse error IS one. Never raises."""
    if not path.exists():
        return []
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [f"{path}: could not parse TOML ({exc})"]
    section = raw.get(section_name)
    if section is None:
        return []
    if not isinstance(section, dict):
        return [f"{path}: [{section_name}] is not a table"]
    return validate_section(section, fields, section_name)


def validate_user_configs(config_dir: Path) -> list[str]:
    """All problems across the user's autoplay.toml, narrator.toml, sound-yield.toml, speaker.toml and history.toml."""
    problems: list[str] = []
    problems += validate_toml_file(config_dir / "autoplay.toml", "autoplay", AUTOPLAY_FIELDS)
    problems += validate_toml_file(config_dir / "narrator.toml", "narrator", NARRATOR_FIELDS)
    problems += validate_toml_file(config_dir / "sound-yield.toml", "sound_yield", SOUND_YIELD_FIELDS)
    problems += validate_toml_file(config_dir / "speaker.toml", "speaker", SPEAKER_FIELDS)
    problems += validate_toml_file(config_dir / "history.toml", "history", HISTORY_FIELDS)
    return problems
