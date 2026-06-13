"""Unit tests for AutoplayGate.

Covers global-disable classification, suppress-hooks classification and
its precedence over disable, the ENABLED default, and the scoped
worker_gated_off() check (global-disable only).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from autoplay_gate import (  # noqa: E402
    DISABLED_GLOBAL,
    ENABLED,
    SUPPRESSED,
    AutoplayGate,
)


def _home() -> Path:
    h = Path(tempfile.mkdtemp(prefix="auto-speech-gate-test-"))
    (h / ".claude").mkdir(parents=True)
    return h


def test_enabled_by_default() -> None:
    g = AutoplayGate(home=_home(), env={})
    assert g.classify() == ENABLED
    assert g.worker_gated_off() is False


def test_disabled_global() -> None:
    h = _home()
    (h / ".claude" / "auto-speech.disabled").touch()
    g = AutoplayGate(home=h, env={})
    assert g.classify() == DISABLED_GLOBAL
    assert g.worker_gated_off() is True


def test_suppressed() -> None:
    g = AutoplayGate(home=_home(), env={"AUTO_SPEECH_SUPPRESS_HOOKS": "1"})
    assert g.classify() == SUPPRESSED


def test_suppress_takes_precedence_over_disable() -> None:
    h = _home()
    (h / ".claude" / "auto-speech.disabled").touch()
    g = AutoplayGate(home=h, env={"AUTO_SPEECH_SUPPRESS_HOOKS": "1"})
    assert g.classify() == SUPPRESSED


def test_worker_scope_is_global_disable_only() -> None:
    # The worker check ignores suppress-hooks (hook scope): with only the
    # suppress var set and no disable marker, the worker is NOT gated off.
    g = AutoplayGate(home=_home(), env={"AUTO_SPEECH_SUPPRESS_HOOKS": "1"})
    assert g.worker_gated_off() is False


def main() -> int:
    tests = [
        test_enabled_by_default,
        test_disabled_global,
        test_suppressed,
        test_suppress_takes_precedence_over_disable,
        test_worker_scope_is_global_disable_only,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"autoplay_gate: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
