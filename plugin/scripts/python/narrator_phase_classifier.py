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
    session_id: str = ""
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
    def __init__(self, silence_seconds: float = 2.0, max_events_per_phase: int = 100):
        self._silence_seconds = silence_seconds
        self._max_events_per_phase = max_events_per_phase
        self._current: dict[str, Phase] = {}

    def feed(self, raw_event: dict[str, Any]) -> Phase | None:
        event_type = raw_event.get("event", "")
        ts = float(raw_event.get("ts", 0.0))

        session_id = raw_event.get("payload", {}).get("session_id", "")
        if event_type in ("UserPromptSubmit", "Stop"):
            return self._flush(session_id)

        if event_type != "PostToolUse":
            return None

        payload = raw_event.get("payload", {}) or {}
        tool_name = payload.get("tool_name", "") or ""
        if not tool_name:
            return None

        category = TOOL_CATEGORY.get(tool_name, Category.OTHER)
        summary = _summarize_event(tool_name, payload)
        if not summary:
            return None
        ev = ToolEvent(ts=ts, tool_name=tool_name, category=category, summary=summary)

        closed: Phase | None = None
        current = self._current.get(session_id)
        if current is not None:
            silence_exceeded = (
                current.ended_ts is not None and (ts - current.ended_ts) > self._silence_seconds
            )
            if (
                silence_exceeded
                or current.category != category
                or len(current.events) >= self._max_events_per_phase
            ):
                closed = current
                del self._current[session_id]

        if session_id not in self._current:
            self._current[session_id] = Phase(category=category, session_id=session_id)

        self._current[session_id].add(ev)
        return closed

    def flush(self, session_id: str = "") -> Phase | None:
        """Externally-driven flush (e.g., on shutdown)."""
        return self._flush(session_id)

    def _flush(self, session_id: str = "") -> Phase | None:
        if not session_id:
            # Just flush the first one if no session id (shutdown)
            if not self._current:
                return None
            session_id = list(self._current.keys())[0]
        current = self._current.get(session_id)
        if current is None or not current.events:
            if session_id in self._current:
                del self._current[session_id]
            return None
        closed = current
        del self._current[session_id]
        return closed


def _summarize_event(tool_name: str, payload: dict) -> str:
    import json
    import os

    thinking = ""
    transcript_path = payload.get("transcriptPath")
    if transcript_path and os.path.exists(transcript_path):
        try:
            with open(transcript_path, "r") as f:
                lines = f.readlines()
                for line in reversed(lines):
                    try:
                        record = json.loads(line)
                        if record.get("type") == "PLANNER_RESPONSE":
                            thinking = record.get("thinking", "").strip()
                            if len(thinking) > 300:
                                # Take the LAST 300 chars, since the first 300 are often generic system prompt boilerplate
                                thinking = "..." + thinking[-300:]
                            break
                    except Exception:
                        pass
        except Exception:
            pass

    ti = payload.get("tool_input", {}) or {}
    action = ti.get("toolAction")

    def format_output(base: str) -> str:
        if action:
            base = f"Goal: {action} (Action: {base})"
        if thinking:
            base += f"\\nReasoning: {thinking}"
        return base

    if tool_name in ("Bash", "BashOutput", "run_command"):
        cmd = (
            (ti.get("command") or ti.get("description") or ti.get("CommandLine") or "")
            .strip()
            .splitlines()
        )
        first = cmd[0] if cmd else ""
        if "tail" in first and "auto-speech-narrator-daemon.log" in first:
            return ""
        if "cat" in first and ".system_generated/tasks/task-" in first:
            return ""
        if "grep" in first and "auto-speech-narrator-daemon.log" in first:
            return ""
        return format_output(f"Bash: {first[:100]}")
    if tool_name in (
        "Read",
        "Edit",
        "Write",
        "MultiEdit",
        "NotebookRead",
        "NotebookEdit",
        "view_file",
        "replace_file_content",
    ):
        fp = ti.get("file_path", "") or ti.get("TargetFile", "") or ti.get("AbsolutePath", "")
        if fp:
            fp = os.path.basename(fp)
            if fp == "auto-speech-narrator-daemon.log":
                return ""
        return format_output(f"{tool_name}: {fp}")
    if tool_name == "Grep":
        return format_output(f"Grep: {ti.get('pattern', '')[:80]}")
    if tool_name == "Glob":
        return format_output(f"Glob: {ti.get('pattern', '')[:80]}")
    if tool_name == "LS":
        return format_output(f"LS: {ti.get('path', '')}")
    if tool_name == "WebFetch":
        return format_output(f"WebFetch: {ti.get('url', '')[:80]}")
    if tool_name == "ask_question":
        return format_output("Asking User a Question")

    return format_output(f"{tool_name}: ...")
