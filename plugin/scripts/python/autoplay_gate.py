"""AutoplayGate: classifier of whether autoplay may proceed.

Recognizes the gating conditions that silence autoplay:

  ENABLED          nothing suppresses autoplay
  DISABLED_GLOBAL  ~/.claude/auto-speech.disabled present (global mute)
  SUPPRESSED       AUTO_SPEECH_SUPPRESS_HOOKS=1 (nested claude -p guard)

SCOPE NOTE — parity with bash: the worker only RE-CHECKS the global
disable marker (the user could toggle it off between hook fire and the
worker reaching its disable check). Per-session enrollment (autoplay is
opt-IN; see autoplay_enrollment.py) and the suppress-hooks guard are
enforced in autoplay_hook.sh BEFORE the worker is ever spawned. They are modeled here as recognized states for
completeness, but only worker_gated_off() — the global-disable check —
gates the worker, preserving the exact bash scope.
"""
from __future__ import annotations

import os
from pathlib import Path

ENABLED = "enabled"
DISABLED_GLOBAL = "disabled_global"
SUPPRESSED = "suppressed"


def _disabled_marker_path() -> Path:
    return Path(os.path.expanduser("~/.claude/auto-speech.disabled"))


class AutoplayGate:
    """Classifies autoplay gating. `home` overridable for tests."""

    def __init__(self, home: Path | None = None, env: dict | None = None) -> None:
        self._home = Path(home) if home is not None else Path(os.path.expanduser("~"))
        self._env = env if env is not None else os.environ

    def disabled_marker_path(self) -> Path:
        return self._home / ".claude" / "auto-speech.disabled"

    def classify(self) -> str:
        """Full classification (all recognized states).

        Suppression precedence matches the hook ordering: the nested-claude
        suppress guard is checked before the global disable marker.
        """
        if self._env.get("AUTO_SPEECH_SUPPRESS_HOOKS") == "1":
            return SUPPRESSED
        if self.disabled_marker_path().exists():
            return DISABLED_GLOBAL
        return ENABLED

    def worker_gated_off(self) -> bool:
        """The ONLY check the worker performs: global disable marker present.

        Mirrors the bash worker's second disable check. Per-session opt-out
        and suppress-hooks are NOT re-checked here (hook scope), by design.
        """
        return self.disabled_marker_path().exists()
