"""Unit tests for config_validation.

Covers the schema checks (unknown key, wrong type, bad enum, out-of-range,
bool-is-not-int) for both sections, and the file-level wrapper (missing
file = clean, malformed TOML = one problem, valid file = clean). Hermetic.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

import config_validation as cv  # noqa: E402


def test_valid_autoplay_section_clean() -> None:
    section = {"mode": "summary", "summary_size": "small", "coalesce_seconds": 1.0,
               "narration_wait_max_seconds": 90.0}
    assert cv.validate_section(section, cv.AUTOPLAY_FIELDS, "autoplay") == []


def test_unknown_key_flagged() -> None:
    problems = cv.validate_section({"moed": "summary"}, cv.AUTOPLAY_FIELDS, "autoplay")
    assert any("unknown key" in p and "moed" in p for p in problems)


def test_bad_enum_flagged() -> None:
    problems = cv.validate_section({"mode": "whisper"}, cv.AUTOPLAY_FIELDS, "autoplay")
    assert any("mode must be one of" in p for p in problems)


def test_wrong_type_number_flagged() -> None:
    problems = cv.validate_section({"coalesce_seconds": "soon"}, cv.AUTOPLAY_FIELDS, "autoplay")
    assert any("coalesce_seconds should be a number" in p for p in problems)


def test_negative_number_flagged() -> None:
    problems = cv.validate_section({"narration_wait_max_seconds": -5}, cv.AUTOPLAY_FIELDS, "autoplay")
    assert any("must be >= 0" in p for p in problems)


def test_int_field_rejects_bool_and_below_min() -> None:
    # bool is a subclass of int but is not a valid integer config value.
    p_bool = cv.validate_section({"max_tokens": True}, cv.NARRATOR_FIELDS, "narrator")
    assert any("max_tokens should be an integer" in p for p in p_bool)
    p_min = cv.validate_section({"min_events_per_phase": 0}, cv.NARRATOR_FIELDS, "narrator")
    assert any("min_events_per_phase must be >= 1" in p for p in p_min)


def test_valid_narrator_section_clean() -> None:
    section = {"provider": "mlx", "model": "x", "max_tokens": 60,
               "silence_seconds": 8.0, "idle_shutdown_seconds": 600.0,
               "min_events_per_phase": 1, "max_queue_depth": 32}
    assert cv.validate_section(section, cv.NARRATOR_FIELDS, "narrator") == []


def test_narrator_bad_provider() -> None:
    problems = cv.validate_section({"provider": "openai"}, cv.NARRATOR_FIELDS, "narrator")
    assert any("provider must be one of" in p for p in problems)


def test_toml_file_missing_is_clean() -> None:
    with tempfile.TemporaryDirectory() as d:
        assert cv.validate_toml_file(Path(d) / "nope.toml", "autoplay", cv.AUTOPLAY_FIELDS) == []


def test_toml_file_malformed_is_one_problem() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "autoplay.toml"
        p.write_text("this is = = not toml", encoding="utf-8")
        problems = cv.validate_toml_file(p, "autoplay", cv.AUTOPLAY_FIELDS)
        assert len(problems) == 1
        assert "could not parse" in problems[0]


def test_validate_user_configs_aggregates() -> None:
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d)
        (cfg / "autoplay.toml").write_text('[autoplay]\nmode = "bogus"\n', encoding="utf-8")
        (cfg / "narrator.toml").write_text('[narrator]\nmax_queue_depth = 0\n', encoding="utf-8")
        problems = cv.validate_user_configs(cfg)
        assert any("mode must be one of" in p for p in problems)
        assert any("max_queue_depth must be >= 1" in p for p in problems)


def main() -> int:
    tests = [
        test_valid_autoplay_section_clean,
        test_unknown_key_flagged,
        test_bad_enum_flagged,
        test_wrong_type_number_flagged,
        test_negative_number_flagged,
        test_int_field_rejects_bool_and_below_min,
        test_valid_narrator_section_clean,
        test_narrator_bad_provider,
        test_toml_file_missing_is_clean,
        test_toml_file_malformed_is_one_problem,
        test_validate_user_configs_aggregates,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"config_validation: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
