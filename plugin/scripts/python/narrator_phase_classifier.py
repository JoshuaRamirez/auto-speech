"""PhaseClassifier: turn a stream of hook events into phase transitions.

A "phase" is a contiguous run of tool calls of one category (Explore /
Edit / Run / Delegate / Reason / Other). The classifier consumes hook
events one at a time and yields a closed Phase whenever:

  * the next event's category differs,
  * silence_seconds elapsed since the last event,
  * a Stop event arrives (turn ended), or
  * a UserPromptSubmit arrives (new turn — also resets state).

Designed for in-process use by the narrator service.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Category(str, Enum):
    EXPLORE = "explore"
    EDIT = "edit"
    RUN = "run"
    DELEGATE = "delegate"
    REASON = "reason"
    OTHER = "other"


TOOL_CATEGORY: dict[str, Category] = {
    "Read": Category.EXPLORE,
    "Glob": Category.EXPLORE,
    "Grep": Category.EXPLORE,
    "LS": Category.EXPLORE,
    "NotebookRead": Category.EXPLORE,
    "WebFetch": Category.EXPLORE,
    "WebSearch": Category.EXPLORE,
    "Edit": Category.EDIT,
    "Write": Category.EDIT,
    "NotebookEdit": Category.EDIT,
    "MultiEdit": Category.EDIT,
    "Bash": Category.RUN,
    "BashOutput": Category.RUN,
    "Agent": Category.DELEGATE,
    "Task": Category.DELEGATE,
}


@dataclass
class ToolEvent:
    ts: float
    tool_name: str
    category: Category
    summary: str


@dataclass
class Phase:
    category: Category
    events: list[ToolEvent] = field(default_factory=list)
    started_ts: float | None = None
    ended_ts: float | None = None

    def add(self, ev: ToolEvent) -> None:
        if not self.events:
            self.started_ts = ev.ts
        self.events.append(ev)
        self.ended_ts = ev.ts

    @property
    def duration_s(self) -> float:
        if self.started_ts is None or self.ended_ts is None:
            return 0.0
        return max(0.0, self.ended_ts - self.started_ts)


class PhaseClassifier:
    def __init__(self, silence_seconds: float = 8.0):
        self._silence_seconds = silence_seconds
        self._current: Phase | None = None

    def feed(self, raw_event: dict[str, Any]) -> Phase | None:
        event_type = raw_event.get("event", "")
        ts = float(raw_event.get("ts", 0.0))

        if event_type in ("UserPromptSubmit", "Stop"):
            return self._flush()

        if event_type != "PostToolUse":
            return None

        payload = raw_event.get("payload", {}) or {}
        tool_name = payload.get("tool_name", "") or ""
        if not tool_name:
            return None

        category = TOOL_CATEGORY.get(tool_name, Category.OTHER)
        summary = _summarize_event(tool_name, payload)
        ev = ToolEvent(ts=ts, tool_name=tool_name, category=category, summary=summary)

        closed: Phase | None = None
        if self._current is not None:
            silence_exceeded = (
                self._current.ended_ts is not None
                and (ts - self._current.ended_ts) > self._silence_seconds
            )
            if silence_exceeded or self._current.category != category:
                closed = self._current
                self._current = None

        if self._current is None:
            self._current = Phase(category=category)

        self._current.add(ev)
        return closed

    def flush(self) -> Phase | None:
        """Externally-driven flush (e.g., on shutdown)."""
        return self._flush()

    def _flush(self) -> Phase | None:
        if self._current is None or not self._current.events:
            self._current = None
            return None
        closed = self._current
        self._current = None
        return closed


def _summarize_event(tool_name: str, payload: dict) -> str:
    ti = payload.get("tool_input", {}) or {}
    if tool_name in ("Bash", "BashOutput"):
        cmd = (ti.get("command") or ti.get("description") or "").strip().splitlines()
        first = cmd[0] if cmd else ""
        return f"Bash: {first[:100]}"
    if tool_name in ("Read", "Edit", "Write", "MultiEdit", "NotebookRead", "NotebookEdit"):
        return f"{tool_name}: {ti.get('file_path', '')}"
    if tool_name == "Grep":
        return f"Grep: {ti.get('pattern', '')[:80]}"
    if tool_name == "Glob":
        return f"Glob: {ti.get('pattern', '')[:80]}"
    if tool_name == "LS":
        return f"LS: {ti.get('path', '')}"
    if tool_name == "WebFetch":
        return f"WebFetch: {ti.get('url', '')[:80]}"
    if tool_name == "WebSearch":
        return f"WebSearch: {ti.get('query', '')[:80]}"
    if tool_name in ("Agent", "Task"):
        return f"{tool_name}: {ti.get('description', '')[:80]}"
    return tool_name
