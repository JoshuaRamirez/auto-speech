"""The player launcher: after a line plays, open the UI Utility's player on
its clip and its text.

Behind `[history] player` (default off; `auto-speech history player-on`,
MCP `history(action="player-on")`). It never opens while a hold stands (a
meeting, or another app's sound holding speech): nothing pops up during a
meeting. It does nothing while the UI Utility is not installed.

The UI Utility is driven by its published caller contract, v1 rev 1.1
(~/Developer/UI-Utility/docs/external/CONTRACT.md) through
ui_utility_client. `after_line` runs on the narrator's playback thread, so
it only runs the cheap gates and leaves the line in a one-slot mailbox (the
newest line wins); a worker thread does the launch and the socket I/O. The
player opens ready to play and never plays on its own: the line was just
heard. The last hand-over's outcome is kept in auto-speech-player.json in
the state dir, for `history status` in other processes. See
docs/decisions/ADR-020 and ADR-021.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import ui_utility_client
from speech_history import HistoryLine, HistorySettings, HistoryStore
from state_paths import state_path
from ui_utility_client import ContractError, UiUtilityClient

UTILITY_COMMAND = "ui-utility"
UTILITY_APP_NAME = "UI Utility.app"

OFF = "off"
HELD = "held"
NOT_INSTALLED = "not installed"
QUEUED = "queued"
OPENED = "opened"
SKIPPED = "skipped"
NO_SOCKET = "no socket"
REFUSED = "refused"
TIMEOUT = "timeout"
WRONG_CONTRACT = "wrong contract"

PANEL_ID = "auto-speech"
LAUNCH_WAIT_SECONDS = 10.0
CONNECT_RETRY_SECONDS = 0.25
OUTCOME_FILE_NAME = "auto-speech-player.json"

# ContractError kinds → outcomes. "closed" is the connection lost mid-request.
_KIND_OUTCOME = {
    "no socket": NO_SOCKET,
    "refused": REFUSED,
    "timeout": TIMEOUT,
    "wrong contract": WRONG_CONTRACT,
    "closed": NO_SOCKET,
}


def utility_installed() -> bool:
    """The utility's command on PATH, or its app in /Applications or ~/Applications."""
    if shutil.which(UTILITY_COMMAND):
        return True
    for base in (Path("/Applications"), Path.home() / "Applications"):
        if (base / UTILITY_APP_NAME).exists():
            return True
    return False


def outcome_path() -> Path:
    return state_path(OUTCOME_FILE_NAME)


def _title(line: HistoryLine) -> str:
    sender = line.sidecar.get("sender")
    name = sender.get("name") if isinstance(sender, dict) else None
    at = line.spoken_at
    stamp = time.strftime("%H:%M:%S", time.localtime(at)) if at > 0 else "--:--:--"
    return f"{name or 'auto-speech'} · {stamp}"


def _words(line: HistoryLine) -> list[dict[str, Any]]:
    timings = line.sidecar.get("word_timings")
    words = timings.get("words") if isinstance(timings, dict) else None
    return list(words) if isinstance(words, list) else []


class PlayerLauncher:
    def __init__(
        self,
        store: HistoryStore | None = None,
        *,
        holding: Callable[[], bool] | None = None,
        installed: Callable[[], bool] = utility_installed,
        log: Callable[[str], None] | None = None,
        client_factory: Callable[..., Any] = UiUtilityClient,
        launch: Callable[[], Optional[list[str]]] = ui_utility_client.launch,
        outcome_file: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        start_worker: bool = True,
    ) -> None:
        self._store = store or HistoryStore()
        self._holding = holding or (lambda: False)
        self._installed = installed
        self._log = log
        self._said: set[str] = set()
        self._client_factory = client_factory
        self._launch = launch
        self._outcome_file = outcome_file if outcome_file is not None else outcome_path()
        self._sleep = sleep
        self._clock = clock
        self._start_worker = start_worker
        self._cond = threading.Condition()
        self._pending: Optional[HistoryLine] = None
        self._closed = False
        self._worker: Optional[threading.Thread] = None
        self._client: Any = None
        self._opened_once = False

    # -- status -------------------------------------------------------------

    def last_outcome(self) -> Optional[dict[str, Any]]:
        try:
            data = json.loads(self._outcome_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and isinstance(data.get("outcome"), str) else None

    def note(self, settings: HistorySettings | None = None) -> str:
        """Why the player would or would not open, for `history status`."""
        settings = settings if settings is not None else self._store.load()
        if not settings.player:
            return ""
        if not self._installed():
            return "the UI Utility is not installed; nothing opens"
        text = "opens after each line"
        last = self.last_outcome()
        if last is not None:
            at = str(last.get("at") or "")
            text += f"; last: {last['outcome']} {at[11:19] if len(at) >= 19 else at}".rstrip()
            if last.get("detail"):
                text += f": {last['detail']}"
        return text

    def _say_once(self, outcome: str, msg: str) -> None:
        if self._log is not None and outcome not in self._said:
            self._said.add(outcome)
            self._log(msg)

    # -- the playback thread's side: gates, then the mailbox ----------------

    def _held(self) -> bool:
        try:
            return bool(self._holding())
        except Exception:  # noqa: BLE001 — a gate that cannot say counts as holding: nothing pops up
            return True

    def after_line(self, line: Optional[HistoryLine], settings: HistorySettings | None = None) -> str:
        """Called by the daemon after a line played and was exported. No I/O
        beyond reading the settings: the hand-over runs on the worker.
        Returns off | held | not installed | queued."""
        settings = settings if settings is not None else self._store.load()
        if not settings.player or line is None:
            return OFF
        if self._held():
            return HELD
        if not self._installed():
            self._say_once(NOT_INSTALLED, "player: on, but the UI Utility is not installed; nothing opens")
            return NOT_INSTALLED
        with self._cond:
            if self._closed:
                return OFF
            self._pending = line  # one slot: an older line not yet shown is dropped
            if self._start_worker and self._worker is None:
                self._worker = threading.Thread(target=self._run, name="player-launcher", daemon=True)
                self._worker.start()
            self._cond.notify()
        return QUEUED

    def close(self) -> None:
        """Stop the worker and close the connection. The panel stays open:
        it is the user's once shown. Idempotent."""
        with self._cond:
            self._closed = True
            self._pending = None
            client, self._client = self._client, None
            self._cond.notify_all()
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    # -- the worker's side ------------------------------------------------------

    def _take(self, timeout: float | None = None) -> Optional[HistoryLine]:
        with self._cond:
            if timeout is None:
                while self._pending is None and not self._closed:
                    self._cond.wait()
            elif self._pending is None and not self._closed:
                self._cond.wait(timeout)
            line, self._pending = self._pending, None
            return None if self._closed else line

    def _run(self) -> None:
        while True:
            line = self._take()
            if line is None:
                return
            self._hand_over(line)

    def _on_event(self, evt: dict[str, Any]) -> None:
        if evt.get("event") == "error":
            self._say_once("event error", f"player: the UI Utility reported {evt.get('code')}: {evt.get('detail')}")

    def _drop_client(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    def _greeted_client(self) -> Any:
        """A connected client that has said hello; launches the utility when
        its socket is not there. Raises ContractError."""
        client = self._client
        if client is not None and client.connected:
            return client
        self._drop_client()
        client = self._client_factory(on_event=self._on_event)
        try:
            client.connect()
        except ContractError:
            try:
                argv = self._launch()
            except Exception as exc:  # noqa: BLE001 — `open` missing, or the launch hung
                raise ContractError("no socket", f"the launch failed: {exc!r}") from None
            if argv is None:
                raise ContractError("no socket", "launching is off") from None
            deadline = self._clock() + LAUNCH_WAIT_SECONDS
            while True:
                self._sleep(CONNECT_RETRY_SECONDS)
                try:
                    client.connect()
                    break
                except ContractError:
                    if self._clock() >= deadline:
                        raise ContractError(
                            "no socket", f"launched ({' '.join(argv)}) but no socket in {LAUNCH_WAIT_SECONDS:g} s"
                        ) from None
        try:
            client.hello()
        except ContractError:
            client.close()
            raise
        self._client = client
        return client

    def _hand_over(self, line: HistoryLine) -> str:
        """Show one line in the player. Records and returns its outcome; never raises."""
        try:
            if not line.wav.is_file():
                return self._record(SKIPPED, "the WAV was pruned before the hand-over", line)
            if self._held():  # the hold, again: one may have started while the line waited
                return self._record(HELD, "", line)
            client = self._greeted_client()
            client.open_player(panel_id=PANEL_ID, clip=line.wav, words=_words(line), title=_title(line), play=False)
            return self._record(OPENED, "", line)
        except ContractError as exc:
            if exc.kind != "refused":
                self._drop_client()
            return self._record(_KIND_OUTCOME[exc.kind], exc.detail, line)
        except Exception as exc:  # noqa: BLE001 — the worker never dies on one line
            self._drop_client()
            return self._record(REFUSED, repr(exc), line)

    def _record(self, outcome: str, detail: str, line: Optional[HistoryLine]) -> str:
        data = {
            "outcome": outcome,
            "detail": detail,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "line_id": line.id if line is not None else None,
        }
        try:
            self._outcome_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._outcome_file.with_name(f".{self._outcome_file.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self._outcome_file)
        except OSError as exc:
            self._say_once("outcome file", f"player: could not write {self._outcome_file}: {exc!r}")
        if outcome == OPENED:
            if not self._opened_once and self._log is not None:
                self._opened_once = True
                self._log("player: the UI Utility's player shows the line just heard")
        elif outcome != HELD:
            self._say_once(outcome, f"player: {outcome}" + (f" ({detail})" if detail else ""))
        return outcome


def describe(launcher: Any, settings: HistorySettings) -> str:
    try:
        return launcher.note(settings)
    except Exception:  # noqa: BLE001
        return ""
