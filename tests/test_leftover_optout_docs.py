"""Regression: current user-facing surfaces must not keep 0.1.0 opt-OUT copy.

Autoplay is opt-IN as of 0.2.0. Enrollment is only

  ~/.claude/auto-speech-autoplay-enabled/<session_id>

The pre-inversion directory ~/.claude/auto-speech-autoplay-sessions/ must
never appear as current gating, and no current-facing surface may say
autoplay is on by default / enabled by default / every session reads.

Historical ADRs, CHANGELOG entries, enrollment comments, and uninstall
cleanup of the legacy dir are excluded on purpose.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Current-facing copy only. Do not add CHANGELOG, ADRs, micro-design,
# autoplay_enrollment.py, or setup/uninstall.sh — those name the old
# directory to explain the inversion or clean it up.
SURFACE_GLOBS = (
    "README.md",
    "docs/OPERATIONS.md",
    "plugin/commands/*.md",
    "plugin/commands-extra/*.md",
    "plugin/scripts/shell/autoplay_status.sh",
    "plugin/scripts/python/doctor.py",
    "plugin/scripts/python/autoplay_scope.py",
)

LEGACY_DIR = "auto-speech-autoplay-sessions"
ENROLL_DIR = "auto-speech-autoplay-enabled"

# Phrases that describe the OLD default as if it were still current.
FORBIDDEN_SUBSTRINGS = (
    "enabled (default)",
    "default ON",
    "opt-OUT",
    "opt-out",
    "opted out",
    "every session reads",
)

ON_BY_DEFAULT = re.compile(r"(?<![\w-])on by default", re.IGNORECASE)


def _surfaces() -> list[Path]:
    files: list[Path] = []
    for pattern in SURFACE_GLOBS:
        files.extend(sorted(ROOT.glob(pattern)))
    # Dedup while keeping a stable order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in files:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def test_surfaces_exist() -> None:
    files = _surfaces()
    assert files, "leftover-docs: no surfaces matched"
    # The confirmed-drift files must be in the scan set.
    names = {p.name for p in files}
    for required in (
        "autoplay_status.sh",
        "auto-speech-autoplay-status.md",
        "doctor.py",
        "OPERATIONS.md",
        "auto-speech-scope.md",
    ):
        assert required in names, f"leftover-docs: {required} not in scan set"


def test_no_legacy_optout_dir_on_current_surfaces() -> None:
    hits = []
    for path in _surfaces():
        text = path.read_text(encoding="utf-8")
        if LEGACY_DIR in text:
            hits.append(str(path.relative_to(ROOT)))
    assert hits == [], (
        "legacy opt-OUT dir must not appear on current surfaces "
        f"(enrollment is only {ENROLL_DIR}/): {hits}"
    )


def test_no_leftover_optout_wording() -> None:
    hits: list[str] = []
    for path in _surfaces():
        text = path.read_text(encoding="utf-8")
        rel = str(path.relative_to(ROOT))
        for phrase in FORBIDDEN_SUBSTRINGS:
            if phrase in text:
                hits.append(f"{rel}: {phrase!r}")
        if ON_BY_DEFAULT.search(text):
            hits.append(f"{rel}: 'on by default'")
    assert hits == [], "leftover opt-OUT copy still on current surfaces:\n  " + "\n  ".join(hits)


def test_status_script_enrolls_from_enabled_dir_only() -> None:
    status = ROOT / "plugin" / "scripts" / "shell" / "autoplay_status.sh"
    text = status.read_text(encoding="utf-8")
    assert ENROLL_DIR in text, "autoplay_status.sh must report the enrollment dir"
    assert LEGACY_DIR not in text, "autoplay_status.sh must never read the legacy opt-OUT dir"
    assert "default OFF" in text
    assert "default ON" not in text


def test_doctor_reports_jq() -> None:
    doctor = (ROOT / "plugin" / "scripts" / "python" / "doctor.py").read_text(
        encoding="utf-8"
    )
    assert '"jq"' in doctor, "doctor.py must probe jq"
    assert "enabled (default)" not in doctor
    assert "every enrolled session reads" in doctor


def main() -> int:
    tests = [
        test_surfaces_exist,
        test_no_legacy_optout_dir_on_current_surfaces,
        test_no_leftover_optout_wording,
        test_status_script_enrolls_from_enabled_dir_only,
        test_doctor_reports_jq,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"leftover_optout_docs: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
