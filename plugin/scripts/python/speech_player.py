"""The player launcher: after a line plays, open the UI Utility's player on
its clip and its text.

Behind `[history] player` (default off; `auto-speech history player-on`,
MCP `history(action="player-on")`). It never opens while a hold stands (a
meeting, or another app's sound holding speech): nothing pops up during a
meeting. It does nothing while the UI Utility is not installed.

The UI Utility is driven by its published caller contract
(~/Developer/UI-Utility/docs/external/CONTRACT.md), which the Desk hands
over once it is published. Until it is wired in here, an installed utility
is reported as such and nothing opens: `after_line` says so in its outcome
and the daemon logs it once. See docs/decisions/ADR-020.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable, Optional

from speech_history import HistoryLine, HistorySettings, HistoryStore

UTILITY_COMMAND = "ui-utility"
UTILITY_APP_NAME = "UI Utility.app"

OFF = "off"
HELD = "held"
NOT_INSTALLED = "not installed"
CONTRACT_PENDING = "contract not wired yet"


def utility_installed() -> bool:
    """The utility's command on PATH, or its app in /Applications or ~/Applications."""
    if shutil.which(UTILITY_COMMAND):
        return True
    for base in (Path("/Applications"), Path.home() / "Applications"):
        if (base / UTILITY_APP_NAME).exists():
            return True
    return False


class PlayerLauncher:
    def __init__(
        self,
        store: HistoryStore | None = None,
        *,
        holding: Callable[[], bool] | None = None,
        installed: Callable[[], bool] = utility_installed,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._store = store or HistoryStore()
        self._holding = holding or (lambda: False)
        self._installed = installed
        self._log = log
        self._said: set[str] = set()

    def note(self, settings: HistorySettings | None = None) -> str:
        """Why the player would or would not open, for `history status`."""
        settings = settings if settings is not None else self._store.load()
        if not settings.player:
            return ""
        if not self._installed():
            return "the UI Utility is not installed; nothing opens"
        return "the UI Utility is installed; its contract is not wired in yet, so nothing opens"

    def _say_once(self, outcome: str, msg: str) -> None:
        if self._log is not None and outcome not in self._said:
            self._said.add(outcome)
            self._log(msg)

    def after_line(self, line: Optional[HistoryLine], settings: HistorySettings | None = None) -> str:
        """Called by the daemon after a line played and was exported.
        Returns what happened: off | held | not installed | contract not wired yet."""
        settings = settings if settings is not None else self._store.load()
        if not settings.player or line is None:
            return OFF
        try:
            if self._holding():
                return HELD
        except Exception:  # noqa: BLE001 — a gate that cannot say counts as holding: nothing pops up
            return HELD
        if not self._installed():
            self._say_once(NOT_INSTALLED, "player: on, but the UI Utility is not installed; nothing opens")
            return NOT_INSTALLED
        self._say_once(CONTRACT_PENDING, "player: on and the UI Utility is installed; its contract is not wired in yet, nothing opens")
        return CONTRACT_PENDING


def describe(launcher: Any, settings: HistorySettings) -> str:
    try:
        return launcher.note(settings)
    except Exception:  # noqa: BLE001
        return ""
