"""The speech control: the UI Utility's floating control (contract v1 rev
1.2, "The control piece"), with the transcript below it, driven by the
daemon.

His 2026-10-21 20:07-20:09: "i want the same thing that shows up like in
the screenshot" / "in a mac native" / "and i wanted a transcript"
(#m1021-2008-7850). Behind `[history] player` (`auto-speech history
player-on`): at daemon start, and whenever the setting is on, one control
is open (panel control, id auto-speech). Never opened while a hold stands
(a meeting, or another app's sound): nothing pops up during a meeting.
Nothing while the UI Utility is not installed.

The control plays nothing. The daemon plays, tells the control its state
and its list, and acts on what the person asks for (ControlActions). The
domain stays here: the list is the speech history newest first, every
line on the missed list as an unplayed item, and the line playing now.

Threads: the narrator's calls only change the model and wake the worker;
the worker owns the connection and sends every request. The client's
reader thread hands events to the worker as well, since a request sent
from the reader would wait on a reply only the reader can read.
See docs/decisions/ADR-022.

Beside the control, while `[history] player` is on, one settings piece
(rev 1.3, id auto-speech-settings, source "Speech") puts auto-speech's
settings in the UI Utility's settings window; speech_settings applies each
change where the setting lives, and the worker confirms it with `values`
(or sends the old value back). Only when the UI Utility offers the piece.
See docs/decisions/ADR-023.
"""

from __future__ import annotations

import contextlib
import itertools
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import ui_utility_client
from missed_speech import MissedLine, MissedList
from speech_history import HistoryLine, HistorySettings, HistoryStore, SpeechHistory
from speech_player import CONNECT_RETRY_SECONDS, LAUNCH_WAIT_SECONDS, utility_installed
from speech_settings import SETTINGS, SETTINGS_ID, SOURCE_NAME, SettingError, SettingsPlaces, SpeechSettings
from ui_utility_client import CONTROL, ContractError, UiUtilityClient

CONTROL_ID = "auto-speech"
MISSED_PREFIX = "missed:"  # a missed-list line's item id
NOW_PREFIX = "now-"  # the line playing now, before the history names it
MAX_ITEMS = 50  # the newest history lines skip-back walks; the list shows [history] keep
# Missed and held lines are never cut from the list, whatever keep is.
RATES = [1.0, 1.25, 1.5, 2.0, 0.75]

IDLE = "idle"
PLAYING = "playing"
HELD = "held"

# Model changes the worker sends, in this order.
# _NOW is the row playing: the now-row of a fresh line, or (contract 1.6) the
# history or missed row a replay plays, sent again with its words.
_SOURCE, _ITEMS, _NOW, _STATE, _QUEUE = "source", "items", "now", "state", "queue"
_ORDER = (_SOURCE, _ITEMS, _NOW, _STATE, _QUEUE)
QUEUE_DEBOUNCE_SECONDS = 0.25  # the queue tab is sent at most this often while it churns
SEEK_CHOICES = (1, 5, 10)  # contract 1.6: skip-back and skip-forward with seconds

ICON = Path(__file__).resolve().parents[3] / "chrome-extension" / "icons" / "icon128.png"


def _hhmm(epoch: float) -> str | None:
    if epoch <= 0:
        return None
    try:
        return time.strftime("%H:%M", time.localtime(epoch))
    except (OverflowError, OSError, ValueError):
        return None


def _rfc3339(epoch: float) -> str | None:
    """The moment as RFC 3339 with the local offset, to the second (contract
    1.5's `time`, read by the control's Date and Time columns)."""
    if epoch <= 0:
        return None
    try:
        return datetime.fromtimestamp(int(epoch)).astimezone().isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


rfc3339 = _rfc3339  # the narrator's queue tab stamps its rows the same way


def _seek_seconds(value: Any) -> float | None:
    """A skip's `seconds` (contract 1.6): a positive number, else None (the
    skip as before 1.6: the line's start, or the next line)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return None


def _words(line: HistoryLine) -> list[dict[str, Any]]:
    """The sidecar's word timings, in the player panel's `text.words` shape."""
    timings = line.sidecar.get("word_timings")
    words = timings.get("words") if isinstance(timings, dict) else None
    return list(words) if isinstance(words, list) else []


def _line_key(line: HistoryLine) -> str:
    """One spoken line, however often its record was written: its speak id,
    else the file it came from, else its own id."""
    for key in ("line_id", "origin_file"):
        value = line.sidecar.get(key)
        if isinstance(value, str) and value:
            return f"{key}:{value}"
    return f"id:{line.id}"


def _sender(line: HistoryLine) -> str | None:
    sender = line.sidecar.get("sender")
    name = sender.get("name") if isinstance(sender, dict) else None
    return str(name) if name else None


def note(settings: HistorySettings, installed: Callable[[], bool] = utility_installed) -> str:
    """For `history status`: what the setting does now."""
    if not settings.player:
        return ""
    if not installed():
        return "the UI Utility is not installed; nothing opens"
    return "the UI Utility's speech control, with what was said listed below it"


_PLAYS = ("play-item", "play-all", "play-from")  # the control's play events, which his stop cancels


@dataclass
class ControlActions:
    """What the daemon does for the control's events. Each returns quickly:
    a play is queued, never played on the caller's thread."""

    pause: Callable[[], bool] = lambda: False  # the line now playing; False: nothing to pause
    resume: Callable[[], bool] = lambda: False
    stop: Callable[[], None] = lambda: None  # stop and clear the queue
    restart: Callable[[], bool] = lambda: False  # the line now playing, from its start
    skip: Callable[[], None] = lambda: None  # end the line now playing; the next one plays
    set_rate: Callable[[float], float] = lambda rate: rate  # returns the rate now in effect
    replay: Callable[[HistoryLine], None] = lambda line: None  # a heard line, queued to play again
    play_missed: Callable[[list[str]], int] = lambda ids: 0  # missed-list ids, in order; how many queued
    # A new run of plays from the control (play-from, play-all) begins: the
    # replays still waiting from an earlier run are taken out of the queue,
    # and the replays queued until end_run belong to this one. Returns how
    # many were taken out (ADR-027, #m1030-0939-c4f9).
    begin_run: Callable[[], int] = lambda: 0
    end_run: Callable[[], None] = lambda: None
    # Ends his stop (ADR-024) and queues the lines that arrived while stopped;
    # how many were queued, or -1 when speech was not stopped.
    unstop: Callable[[], int] = lambda: -1
    # Contract 1.6: within the line playing, and the queue tab.
    seek: Callable[[float], bool] = lambda seconds: False  # move by seconds, negative back
    position: Callable[[], float | None] = lambda: None  # seconds into the line playing, from the player
    queue_items: Callable[[], list[dict[str, Any]]] = lambda: []  # what waits, in play order
    queue_clear: Callable[[], int] = lambda: 0
    queue_remove: Callable[[list[str]], int] = lambda ids: 0
    queue_move: Callable[[str, int], int | None] = lambda queue_id, to: None


@dataclass
class _Model:
    state: str = IDLE
    current: str | None = None
    source: str | None = None
    rate: float = 1.0
    paused: bool = False  # held by the person's hold, not by another app's sound
    now: dict[str, Any] | None = None
    pending: set[str] = field(default_factory=set)
    words: list[dict[str, Any]] | None = None  # the words of the line playing
    position: float = 0.0  # seconds into the line playing at `since`
    since: float | None = None  # the clock when `position` was taken; None while not advancing


class ControlDriver:
    def __init__(
        self,
        store: HistoryStore | None = None,
        *,
        history: SpeechHistory | None = None,
        missed: MissedList | None = None,
        actions: ControlActions | None = None,
        holding: Callable[[], bool] | None = None,
        installed: Callable[[], bool] = utility_installed,
        log: Callable[[str], None] | None = None,
        names: Callable[[str | None], str | None] | None = None,
        client_factory: Callable[..., Any] = UiUtilityClient,
        launch: Callable[[], list[str] | None] = ui_utility_client.launch,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        icon: Path | None = ICON,
        start_worker: bool = True,
        settings: SpeechSettings | None = None,
    ) -> None:
        self._store = store or HistoryStore()
        self._history = history or SpeechHistory(store=self._store)
        self._missed = missed or MissedList()
        self.actions = actions or ControlActions()
        self._holding = holding or (lambda: False)
        self._installed = installed
        self._log = log
        self._names = names or (lambda session_id: None)
        self._client_factory = client_factory
        self._launch = launch
        self._sleep = sleep
        self._clock = clock
        self._icon = str(icon) if icon is not None and Path(icon).is_file() else None
        self._start_worker = start_worker
        self._said: set[str] = set()
        self._now_ids = itertools.count(1)

        self._cond = threading.Condition()
        self._model = _Model()
        self._events: deque[dict[str, Any]] = deque()
        self._stops = 0  # his stops so far: a run being queued gives up when it changes
        self._wake = False
        self._closed = False
        self._worker: threading.Thread | None = None
        self._client: Any = None
        self._open = False
        self._closed_by_user = False
        self._pieces: set[str] = set()  # what the UI Utility offered at hello
        self._settings_open = False
        self._queue_offered = True  # False once this connection's UI Utility refused `queue` (before 1.6)
        self._queue_sent_at: float | None = None
        self.settings = settings or SpeechSettings(
            SettingsPlaces(history=self._store, missed=self._missed), set_rate=self.actions.set_rate
        )

    # -- the narrator's side: change the model, wake the worker ------------------

    def _change(self, *kinds: str, **fields: Any) -> None:
        with self._cond:
            if self._closed:
                return
            for k, v in fields.items():
                setattr(self._model, k, v)
            self._model.pending.update(kinds)
            self._wake = True
            if self._start_worker and self._worker is None:
                self._worker = threading.Thread(target=self._run, name="speech-control", daemon=True)
                self._worker.start()
            self._cond.notify()

    def start(self) -> None:
        """At daemon start: the kept rate goes to the sink, and the control
        opens when the setting is on."""
        try:
            rate = self._store.load().rate
            if rate != 1.0:
                rate = float(self.actions.set_rate(rate))
                self._say_once("rate", f"control: playback rate {rate:g}, as kept")
        except Exception as exc:  # noqa: BLE001 — a rate that cannot apply leaves 1
            self._say_once("rate", f"control: the kept rate did not apply: {exc!r}")
            rate = 1.0
        self._change(rate=rate)

    def playing(self, text: str, source: str | None, item_id: str | None = None,
                words: list[dict[str, Any]] | None = None) -> None:
        """A line starts playing (after any hold). A heard line played again
        (`item_id` its history id) or a missed one (`missed:<id>`) is its own
        row, made current: no new row (contract 1.6). A fresh line is a
        now-row on top, with its sender. Either way the row is sent with the
        line's words, and the position starts at 0."""
        at = time.time()
        if item_id and not item_id.startswith(NOW_PREFIX):
            now = None
            current = item_id
            if words is None and not item_id.startswith(MISSED_PREFIX):
                words = self._history_words(item_id)
        else:
            current = f"{NOW_PREFIX}{next(self._now_ids)}"
            now = {"id": current, "text": text, "at": _hhmm(at), "time": _rfc3339(at), "clip": None}
            if source:
                now["source"] = source
        kinds = [_NOW, _STATE]
        with self._cond:
            self._closed_by_user = False  # a new line brings back a control he closed
            if source and source != self._model.source:
                kinds.append(_SOURCE)
        self._change(*kinds, state=PLAYING, current=current, now=now, paused=False, words=words or None,
                     position=0.0, since=self._clock(), **({"source": source} if source else {}))

    def _history_words(self, item_id: str) -> list[dict[str, Any]] | None:
        try:
            line = self._history.get(item_id)
        except Exception:  # noqa: BLE001
            return None
        return _words(line) if line is not None else None

    def queue_changed(self) -> None:
        """The narrator's queue changed: the queue tab is sent again, at most
        every QUEUE_DEBOUNCE_SECONDS. Only marks and wakes: never blocks."""
        self._change(_QUEUE)

    def _position(self) -> float | None:
        """Seconds into the line playing: the player's own when it says,
        else the control's clock since the line started, at the rate."""
        with self._cond:
            m = self._model
            if m.current is None or m.state == IDLE:
                return None
            estimate = m.position + ((self._clock() - m.since) * m.rate if m.since is not None else 0.0)
        told = self.actions.position()
        return round(float(told if told is not None else estimate), 2)

    def _freeze_position(self, **fields: Any) -> dict[str, Any]:
        """The position now, held: the fields for a pause, or for a change of
        rate or a seek before the clock runs again."""
        with self._cond:
            m = self._model
            pos = m.position + ((self._clock() - m.since) * m.rate if m.since is not None else 0.0)
        return {"position": pos, "since": None, **fields}

    def held(self) -> None:
        """Another app's sound (or a meeting) holds the next line."""
        self._change(_STATE, state=HELD, paused=False)

    def ended(self, line: HistoryLine | None = None) -> None:
        """The line playing has ended: heard (and in the history as `line`),
        set aside, cut off or stopped. The list is sent again."""
        self._change(_ITEMS, _STATE, state=IDLE, now=None, paused=False, words=None, position=0.0, since=None,
                     current=line.id if line is not None else None)

    def list_changed(self) -> None:
        """The missed list or the history changed outside a play."""
        self._change(_ITEMS)

    def close(self) -> None:
        """Stop the worker and close the connection. The control stays open:
        it is his once shown. Idempotent."""
        with self._cond:
            self._closed = True
            client, self._client = self._client, None
            self._cond.notify_all()
        if client is not None:
            with contextlib.suppress(Exception):
                client.close()

    # -- the worker ---------------------------------------------------------------

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._wake and not self._closed:
                    self._cond.wait(self._queue_wait())
                    if not self._wake and self._queue_due():
                        self._wake = True
                if self._closed:
                    return
                self._wake = False
                events = list(self._events)
                self._events.clear()
            for evt in events:
                try:
                    self.handle_event(evt)
                except Exception as exc:  # noqa: BLE001 — the worker never dies on one event
                    self._say_once("event", f"control: event {evt.get('event')!r} failed: {exc!r}")
            self.sync()

    def _on_event(self, evt: dict[str, Any]) -> None:
        """On the client's reader thread: a transport press (play, hold,
        stop, skip) is a playback act done here and now, never waiting behind
        a play-from being queued or a sync to the UI Utility (his 09:48:
        "its just supposed to be a playback controller"). A stop also drops
        the plays still waiting to be handled and cancels a run being queued.
        Everything else goes to the worker. Nothing here sends a request: the
        reply would come in on this very thread."""
        with self._cond:
            if self._closed:
                return
            transport = evt.get("id") == CONTROL_ID and evt.get("event") == "control"
            if transport and evt.get("action") == "stop":
                self._stops += 1
                kept = [e for e in self._events if not (e.get("id") == CONTROL_ID and e.get("event") in _PLAYS)]
                self._events.clear()
                self._events.extend(kept)
        if transport:
            try:
                self._control(str(evt.get("action") or ""), evt.get("seconds"))
            except Exception as exc:  # noqa: BLE001 — the reader never dies on one press
                self._say_once("transport", f"control: {evt.get('action')!r} failed: {exc!r}")
            return
        if evt.get("id") == CONTROL_ID and evt.get("event") == "queue":
            # The queue tab's edits are the narrator's queue, changed in place:
            # quick, no request, so done here too (contract 1.6).
            try:
                self._queue_event(evt)
            except Exception as exc:  # noqa: BLE001
                self._say_once("queue event", f"control: queue {evt.get('action')!r} failed: {exc!r}")
            return
        with self._cond:
            if self._closed:
                return
            self._events.append(evt)
            self._wake = True
            self._cond.notify()

    def _queue_wait(self) -> float | None:
        """While a queue send is held back, how long the worker may sleep."""
        if _QUEUE not in self._model.pending or self._queue_sent_at is None:
            return None
        return max(0.0, self._queue_sent_at + QUEUE_DEBOUNCE_SECONDS - self._clock()) + 0.001

    def _queue_due(self) -> bool:
        return _QUEUE in self._model.pending and self._debounce_over()

    def _debounce_over(self) -> bool:
        return self._queue_sent_at is None or self._clock() - self._queue_sent_at >= QUEUE_DEBOUNCE_SECONDS

    def _say_once(self, key: str, msg: str) -> None:
        if self._log is not None and key not in self._said:
            self._said.add(key)
            self._log(msg)

    def _held(self) -> bool:
        try:
            return bool(self._holding())
        except Exception:  # noqa: BLE001 — a gate that cannot say counts as holding
            return True

    def sync(self) -> str:
        """Bring the control up to the model: open it when the setting is on
        and nothing holds; close it when the setting is off. Returns what it
        did: off | held | not installed | closed by him | opened | sent | none."""
        settings: HistorySettings = self._store.load()
        with self._cond:
            pending = set(self._model.pending)
            self._model.pending.clear()
            if _QUEUE in pending and not self._debounce_over():
                pending.discard(_QUEUE)
                self._model.pending.add(_QUEUE)  # sent when the debounce is over
        try:
            if self._client is None or not self._client.connected:
                self._open = False  # the connection was lost: open again, with everything
                self._settings_open = False
            if not settings.player:
                if self._open:
                    self._request({"cmd": "close", "id": CONTROL_ID})
                    self._open = False
                if self._settings_open:
                    self._request({"cmd": "close", "id": SETTINGS_ID})
                    self._settings_open = False
                return "off"
            if not self._open:
                with self._cond:
                    closed_by_user = self._closed_by_user
                if closed_by_user:
                    if self._client is not None and self._client.connected:
                        self._sync_settings()  # never a launch for this alone
                    return "closed by him"
                if not self._installed():
                    self._say_once("not installed", "control: on, but the UI Utility is not installed; nothing opens")
                    return "not installed"
                if self._held():
                    with self._cond:  # sent with the open, when the hold ends
                        self._model.pending.update(pending)
                    return "held"
                self._request(self._open_request())
                self._open = True
                self._send_queue()
                self._say_once("opened", "control: the UI Utility's speech control is open")
                self._sync_settings()
                return "opened"
            sent = False
            for kind in _ORDER:
                if kind in pending:
                    if kind == _QUEUE:
                        sent = self._send_queue() or sent
                        continue
                    request = self._update_request(kind)
                    if request is not None:
                        self._request(request)
                        sent = True
            self._sync_settings()
            return "sent" if sent else "none"
        except ContractError as exc:
            self._open = False
            if exc.kind != "refused":
                self._drop_client()
            self._say_once(f"error {exc.kind}", f"control: {exc}")
            return exc.kind

    def _send_queue(self) -> bool:
        """The queue tab (contract 1.6): what waits, in play order. A copy of
        the UI Utility before 1.6 refuses the request: then it is not sent
        again on this connection, and the control is left alone."""
        self._queue_sent_at = self._clock()
        if not self._queue_offered:
            return False
        try:
            items = list(self.actions.queue_items())
        except Exception as exc:  # noqa: BLE001
            self._say_once("queue items", f"control: the queue could not be read: {exc!r}")
            return False
        try:
            self._request({"cmd": "queue", "id": CONTROL_ID, "items": items})
        except ContractError as exc:
            if exc.kind != "refused":
                raise
            self._queue_offered = False
            self._say_once("queue refused", f"control: this UI Utility has no queue tab (before contract 1.6): {exc}")
            return False
        return True

    def _request(self, obj: dict[str, Any]) -> dict[str, Any]:
        return self._greeted_client().request(obj)

    def _sync_settings(self) -> None:
        """Open the settings piece beside the control, once per connection,
        when this copy of the UI Utility offers it. A refusal is logged and
        leaves the control alone; a lost connection goes up to sync."""
        if self._settings_open or SETTINGS not in self._pieces:
            return
        try:
            self._request({
                "cmd": "open",
                "panel": SETTINGS,
                "id": SETTINGS_ID,
                "source": {"name": SOURCE_NAME, "icon": self._icon},
                "groups": self.settings.groups(),
            })
        except ContractError as exc:
            if exc.kind != "refused":
                raise
            self._say_once("settings refused", f"settings: the UI Utility refused the settings piece: {exc}")
            return
        self._settings_open = True
        self._say_once("settings opened", "settings: auto-speech's settings are in the UI Utility's settings window")

    def _send_values(self, values: dict[str, Any]) -> None:
        if self._settings_open and values:
            self._request({"cmd": "values", "id": SETTINGS_ID, "values": values})

    def _drop_client(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            with contextlib.suppress(Exception):
                client.close()

    def _greeted_client(self) -> Any:
        client = self._client
        if client is not None and client.connected:
            return client
        self._drop_client()
        self._open = False  # a new connection has no control of ours yet
        self._settings_open = False
        client = self._client_factory(on_event=self._on_event)
        try:
            client.connect()
        except ContractError:
            try:
                argv = self._launch()
            except Exception as exc:  # noqa: BLE001
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
                        raise ContractError("no socket", f"launched but no socket in {LAUNCH_WAIT_SECONDS:g} s") from None
        try:
            reply = client.hello(CONTROL)
        except ContractError:
            client.close()
            raise
        pieces = reply.get("pieces") if isinstance(reply, dict) else None
        self._pieces = {str(p) for p in pieces} if isinstance(pieces, list) else set()
        self._settings_open = False
        self._queue_offered = True
        self._client = client
        return client

    # -- requests from the model ------------------------------------------------------

    def items(self) -> list[dict[str, Any]]:
        """The list, newest first: the line playing now, then the history and
        the missed list by time. Only the history is capped: every missed or
        held line is in the list, however old, so he can play what he missed."""
        entries: list[tuple[float, dict[str, Any]]] = []
        try:
            lines = self._history.load(limit=max(1, self._store.load().keep))
        except Exception:  # noqa: BLE001 — an unreadable history shows as empty
            lines = []
        keys: set[str] = set()
        for line in sorted(lines, key=lambda h: h.spoken_at, reverse=True):
            key = _line_key(line)
            if key in keys:
                continue  # the same spoken line written twice: one row, the newest
            keys.add(key)
            item = {"id": line.id, "text": line.text, "at": _hhmm(line.spoken_at), "time": _rfc3339(line.spoken_at),
                    "clip": str(line.wav)}
            name = _sender(line)
            if name:
                item["source"] = name
            entries.append((line.spoken_at, item))
        try:
            missed: list[MissedLine] = self._missed.load()
        except Exception:  # noqa: BLE001
            missed = []
        for m in missed:
            item = {"id": MISSED_PREFIX + m.id, "text": m.label, "at": _hhmm(m.queued_at), "time": _rfc3339(m.queued_at),
                    "clip": None, "unplayed": True}
            # The sender recorded with the line, else its session's desk, else
            # the label of the caller that handed it over (#m1030-1058-d1ad).
            name = m.sender or self._names(m.session_id) or m.source
            if name:
                item["source"] = name
            entries.append((m.queued_at, item))
        entries.sort(key=lambda e: e[0], reverse=True)
        with self._cond:
            now = dict(self._model.now) if self._model.now else None
            current, words = self._model.current, self._model.words
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for _, item in entries:  # newest first: one row per id
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            if words and item["id"] == current:
                item["words"] = words
            out.append(item)
        if now is not None:
            if words:
                now["words"] = words
            if now["id"] not in seen:
                out.insert(0, now)
        return out

    def _source_obj(self) -> dict[str, Any]:
        with self._cond:
            name = self._model.source
        return {"name": name or "auto-speech", "icon": self._icon}

    def _open_request(self) -> dict[str, Any]:
        with self._cond:
            m = self._model
            state, current, rate = m.state, m.current, m.rate
        return {
            "cmd": "open",
            "panel": CONTROL,
            "id": CONTROL_ID,
            "source": self._source_obj(),
            "items": self.items(),
            "current": current,
            "state": state,
            "rate": rate,
            **({"position": pos} if (pos := self._position()) is not None else {}),
            "rates": list(RATES) if rate in RATES else [rate, *RATES],
            "show": {"list": self._store.load().show_list},
        }

    def _update_request(self, kind: str) -> dict[str, Any] | None:
        if kind == _SOURCE:
            return {"cmd": "source", "id": CONTROL_ID, "source": self._source_obj()}
        if kind == _ITEMS:
            return {"cmd": "items", "id": CONTROL_ID, "items": self.items()}
        if kind == _NOW:
            with self._cond:
                now = dict(self._model.now) if self._model.now else None
                current, words = self._model.current, self._model.words
            if now is not None:
                if words:
                    now["words"] = words
                return {"cmd": "item", "id": CONTROL_ID, "item": now}
            if current is None or not words:
                return None
            row = next((r for r in self.items() if r.get("id") == current), None)
            return {"cmd": "item", "id": CONTROL_ID, "item": row} if row is not None else None
        position = self._position()
        with self._cond:
            m = self._model
            state = {"cmd": "state", "id": CONTROL_ID, "state": m.state, "current": m.current, "rate": m.rate}
        if position is not None:
            state["position"] = position
        return state

    # -- the control's events -------------------------------------------------------

    def handle_event(self, evt: dict[str, Any]) -> None:
        if evt.get("id") == SETTINGS_ID:
            self._settings_event(evt)
            return
        if evt.get("id") != CONTROL_ID:
            return
        kind = evt.get("event")
        if kind == "control":
            self._control(str(evt.get("action") or ""))
        elif kind == "rate":
            rate = evt.get("rate")
            if isinstance(rate, (int, float)) and not isinstance(rate, bool) and rate > 0:
                rate = float(self.actions.set_rate(float(rate)))
                with self._cond:
                    running = self._model.state == PLAYING and not self._model.paused
                frozen = self._freeze_position(rate=rate)
                if running:
                    frozen["since"] = self._clock()
                self._change(_STATE, **frozen)
                self._keep_rate(rate)
        elif kind == "play-item":
            self._play_items([evt.get("item")])
        elif kind == "play-all":
            ids = evt.get("items")
            self._as_run(lambda: self._play_items(list(ids) if isinstance(ids, list) else []))
        elif kind == "play-from":
            self._play_from(evt.get("from"), evt.get("items"))
        elif kind == "closed":
            self._open = False
            with self._cond:
                self._closed_by_user = evt.get("by") == "user"

    def _keep_rate(self, rate: float) -> None:
        """A rate set at the control is kept for the next start, and shown in
        the settings window."""
        try:
            self._store.set_rate(rate)
        except (OSError, ValueError) as exc:
            if self._log is not None:
                self._log(f"control: rate {rate:g} not kept: {exc}")
        self._send_values({"rate": self.settings.values()["rate"]})

    def _settings_event(self, evt: dict[str, Any]) -> None:
        kind = evt.get("event")
        if kind == "closed":
            self._settings_open = False
            return
        if kind != "setting":
            return
        item = str(evt.get("item") or "")
        value = evt.get("value")
        try:
            old = self.settings.values()
        except Exception:  # noqa: BLE001
            old = {}
        try:
            applied = self.settings.apply(item, value)
        except SettingError as exc:
            if self._log is not None:
                self._log(f"settings: {item} = {value!r} not applied: {exc}")
            if item in old:
                self._send_values({item: old[item]})
            return
        if self._log is not None:
            self._log(f"settings: {item} = {value!r}" if item != "clear_missed" else "settings: missed list cleared")
        self._send_values(applied)
        if item == "rate":
            self._change(_STATE, rate=float(applied["rate"]))
        elif item in ("keep", "clear_missed"):
            self._change(_ITEMS)
        elif item == "player":
            self._change()  # the next sync opens or closes both pieces

    def _control(self, action: str, seconds: Any = None) -> None:
        """A transport press: a playback act on the line playing or the
        queue, never on the narrator process (his 09:48)."""
        with self._cond:
            state, paused = self._model.state, self._model.paused
        playing_now = state != IDLE
        if action == "play":
            if self.actions.unstop() > 0:
                return  # the lines his stop set aside play, in order
            if state == HELD and paused:
                if self.actions.resume():
                    with self._cond:
                        position = self._model.position
                    self._change(_STATE, state=PLAYING, paused=False, position=position, since=self._clock())
            elif state == IDLE:
                self._replay_near(0)
        elif action == "hold":
            if state == PLAYING and self.actions.pause():
                self._change(_STATE, **self._freeze_position(state=HELD, paused=True))
        elif action == "stop":
            self.actions.stop()
            self._change(_ITEMS, _STATE, state=IDLE, now=None, paused=False, words=None, position=0.0, since=None)
        elif action == "start":  # 1.6: to the start of the line playing
            if playing_now and self.actions.restart():
                self._restart_clock(0.0)
        elif action == "end":  # 1.6: to the line's end, so the next one plays
            if playing_now:
                self.actions.skip()
        elif action in ("skip-back", "skip-forward"):
            step = _seek_seconds(seconds)
            if step is not None:  # 1.6: within the line playing
                if playing_now:
                    delta = -step if action == "skip-back" else step
                    if self.actions.seek(delta):
                        with self._cond:
                            pos = self._model.position + (
                                (self._clock() - self._model.since) * self._model.rate if self._model.since is not None else 0.0)
                        self._restart_clock(max(0.0, pos + delta))
            elif action == "skip-back":
                if playing_now:
                    if self.actions.restart():
                        self._restart_clock(0.0)
                else:
                    self._replay_near(+1)
            elif playing_now:
                self.actions.skip()

    def _restart_clock(self, position: float) -> None:
        """The line playing is now at `position`; its clock runs on unless held."""
        with self._cond:
            running = self._model.state == PLAYING and not self._model.paused
        self._change(_STATE, position=position, since=self._clock() if running else None)

    def _queue_event(self, evt: dict[str, Any]) -> None:
        action = evt.get("action")
        if action == "clear":
            self.actions.queue_clear()
        elif action == "remove":
            ids = evt.get("items")
            self.actions.queue_remove([i for i in ids if isinstance(i, str)] if isinstance(ids, list) else [])
        elif action == "move":
            item, to = evt.get("item"), evt.get("to")
            if isinstance(item, str) and isinstance(to, int) and not isinstance(to, bool) and to >= 0:
                self.actions.queue_move(item, to)
        self._change(_QUEUE)  # the tab shows what the queue is now, even when nothing changed

    def _replay_near(self, step: int) -> None:
        """Replay the current history line (step 0) or the one before it (+1);
        the newest when none is current."""
        try:
            lines = list(reversed(self._history.load(limit=MAX_ITEMS)))  # newest first
        except Exception:  # noqa: BLE001
            return
        if not lines:
            return
        with self._cond:
            current = self._model.current
        at = next((i for i, line in enumerate(lines) if line.id == current), None)
        index = 0 if at is None else min(len(lines) - 1, at + step)
        self.actions.replay(lines[index])

    def _play_items(self, ids: list[Any], *, restart_now: bool = True) -> int:
        """Each id queued in the order given: a heard line replayed from its
        WAV, a missed line through the missed list (off it once heard), the
        line playing now restarted (only when restart_now). Every play goes
        on the narrator's explicit band, first in, first out: none cuts
        another off. Unknown ids are skipped. Returns how many were queued."""
        queued = 0
        with self._cond:
            stops = self._stops
        for raw in ids:
            with self._cond:
                if self._stops != stops:
                    break  # his stop came while this was being queued: none of the rest
            if not isinstance(raw, str) or not raw:
                continue
            if raw.startswith(MISSED_PREFIX):
                queued += int(self.actions.play_missed([raw[len(MISSED_PREFIX):]]) or 0)
            elif raw.startswith(NOW_PREFIX):
                if restart_now:
                    self.actions.restart()
                    queued += 1
            else:
                line = self._history.get(raw)
                if line is not None:
                    self.actions.replay(line)
                    queued += 1
        return queued

    def _as_run(self, play: Callable[[], int]) -> int:
        """A play-from or play-all is a run: it replaces the replays still
        waiting from an earlier run, never stacking a second copy behind them
        (#m1030-0939-c4f9). The line playing now plays on to its end; a
        play-item, a fresh line and a missed line are never taken out."""
        replaced = int(self.actions.begin_run() or 0)
        if replaced and self._log is not None:
            self._log(f"control: replaced {replaced} queued replays")
        try:
            return play()
        finally:
            self.actions.end_run()

    def _play_from(self, since: Any, ids: Any) -> None:
        """Contract 1.5's play-from: every listed line at or after `since`,
        oldest first, as the control sends them (ADR-027). The line playing
        now is not restarted: it plays on to its end, and the rest follow.
        One line in the narrator's log says what was asked."""
        queued = self._as_run(lambda: self._play_items(list(ids) if isinstance(ids, list) else [], restart_now=False))
        if self._log is not None:
            self._log(f"control: play-from {since} ({queued} lines)")
