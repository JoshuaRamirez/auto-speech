"""auto-speech MCP server: full lifecycle automation tool suite.

Stdio transport, newline-delimited JSON-RPC 2.0, standard library only.
Exposes 7 tools:

  speak(text)
    Say text aloud verbatim with local Apple neural voice.
  autoplay_turn([session_id, transcript_path, mode, size, ordinal, bypass_llm])
    Synthesize and speak latest turn response from session transcript.
  narrate_event(event, [payload, session_id, cwd])
    Ingest activity event for real-time narration pipeline.
  playback_control(action, [target, ordinal])
    Manage audio playback transport (pause, resume, stop, restart, seek, replay).
  session_configure([session_id, autoplay, narrate, mode, size, scope])
    Configure session enrollment, mode, and scoping.
  sound_yield(action, [apps])
    Hold speech while another app plays sound: switch, exception list, live check.
  doctor([as_json])
    Run system health checks and diagnostics.

Global mute (~/.claude/auto-speech.disabled) silences all speech-producing tools.
stdout carries protocol messages only; diagnostics go to stderr.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

from state_paths import state_path
from typing import Any

from auto_speech_log import rotate_if_oversize
from autoplay_config import _resolve_prompt, load_config as load_autoplay_config
from autoplay_enrollment import SessionEnrollment, format_status as format_enrollment_status
from autoplay_gate import AutoplayGate
from autoplay_scope import SoloScope, format_status as format_scope_status
from cache_store import CacheStore
from claude_cli_rewriter import (
    ClaudeCliRewriter,
    load_default_template,
)
from config_constants import DEFAULT_SPEED, DEFAULT_VOICE_ID
from daemon_client import DaemonClient, Priority
from doctor import Doctor
from message_selector import MessageSelector
from mpv_ipc import MpvIpc
from missed_speech import ACTIONS as MISSED_ACTIONS, run_action as run_missed_action
from sound_yield import ACTIONS as SOUND_YIELD_ACTIONS, run_action as run_sound_yield_action
from speaker import ACTIONS as SPEAKER_ACTIONS, run_action as run_speaker_action
from speech_history import ACTIONS as HISTORY_ACTIONS, run_action as run_history_action
import speech_control
from transcript_locator import TranscriptLocator

SERVER_NAME = "auto-speech"
SERVER_VERSION = "0.6.0"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_CHARS = 20000  # matches web_server's /api/synthesize cap

_PYTHON_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
SPEAK = _PYTHON_DIR / "speak.py"
SAY_LOG = state_path("auto-speech-say.log")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


def _default_cache_root() -> Path:
    return _PROJECT_ROOT / "config" / "cache"


SPEAK_TOOL = {
    "name": "speak",
    "title": "Speak text aloud (local text-to-speech)",
    "description": (
        "Speak text aloud: say, read out loud, voice, narrate, or vocalize "
        "the given words as audio on this Mac's speakers. Local text-to-"
        "speech (TTS) with Apple neural voice — no cloud service, no API key. "
        "The text is spoken verbatim, so write it the way it should sound "
        "(no markdown, code, or tables). Returns immediately; speech is "
        "queued behind anything already playing."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": f"The words to say (1-{MAX_CHARS} characters).",
            },
            "session_id": {
                "type": "string",
                "description": (
                    "The Claude Code session speaking (defaults to CLAUDE_CODE_SESSION_ID). "
                    "The daemon names the desk only when the speaker changes."
                ),
            },
        },
        "required": ["text"],
        "additionalProperties": False,
    },
}

AUTOPLAY_TURN_TOOL = {
    "name": "autoplay_turn",
    "title": "Synthesize and speak latest turn response",
    "description": (
        "Extracts the latest assistant turn for a session, rewrites/summarizes it "
        "per the active autoplay mode (verbatim, summary-small, summary-medium, summary-large, "
        "or raw), and queues audio playback without requiring OS shell hooks."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": (
                    "The Claude Code session speaking (defaults to CLAUDE_CODE_SESSION_ID). "
                    "The daemon names the desk only when the speaker changes."
                ),
            },
            "transcript_path": {
                "type": "string",
                "description": "Optional explicit path to the session transcript JSONL.",
            },
            "mode": {
                "type": "string",
                "enum": ["summary", "verbatim", "raw"],
                "description": "Optional override for autoplay mode (defaults to configured mode).",
            },
            "size": {
                "type": "string",
                "enum": ["small", "medium", "large"],
                "description": "Optional override for summary size (defaults to configured size).",
            },
            "ordinal": {
                "type": "integer",
                "default": 1,
                "description": "Nth-most-recent assistant turn to extract (default 1 = latest).",
            },
            "bypass_llm": {
                "type": "boolean",
                "description": "If true, bypasses the rewriting LLM and speaks the raw assistant text.",
            },
        },
        "additionalProperties": False,
    },
}

NARRATE_EVENT_TOOL = {
    "name": "narrate_event",
    "title": "Ingest activity event for real-time narration",
    "description": (
        "Ingests a lifecycle activity event (PreToolUse, PostToolUse, UserPromptSubmit, "
        "Stop, TaskProgress) into the auto-speech narrator pipeline for background "
        "narration. Honors session narration enrollment."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "event": {
                "type": "string",
                "description": "The event type (e.g., 'PreToolUse', 'PostToolUse', 'UserPromptSubmit', 'Stop').",
            },
            "payload": {
                "type": "object",
                "description": "Event metadata dictionary (tool_name, tool_input, session_id, etc.).",
            },
            "session_id": {
                "type": "string",
                "description": "Session ID (if not provided inside payload).",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory for the event.",
            },
        },
        "required": ["event"],
        "additionalProperties": False,
    },
}

PLAYBACK_CONTROL_TOOL = {
    "name": "playback_control",
    "title": "Manage audio playback transport and replay",
    "description": (
        "Controls audio playback: pause, resume, stop (halt and purge queue), "
        "restart, seek, or replay recent cached audio."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["pause", "resume", "stop", "restart", "seek", "replay"],
                "description": "The transport action to execute.",
            },
            "target": {
                "type": "string",
                "description": "Target position for seek (e.g. '+15', '-10', '0', 'end'). Required for seek.",
            },
            "ordinal": {
                "type": "integer",
                "default": 1,
                "description": "Recency index for replay (1 = most recent cached speech).",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}

SESSION_CONFIGURE_TOOL = {
    "name": "session_configure",
    "title": "Configure session enrollment, mode, and scoping",
    "description": (
        "Updates session enrollment for autoplay and narration, sets "
        "autoplay rewriting modes, and adjusts solo vs all scoping."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Target session ID. Defaults to current session if unset.",
            },
            "autoplay": {
                "type": "string",
                "enum": ["on", "off", "status"],
                "description": "Enable, disable, or query autoplay enrollment for this session.",
            },
            "narrate": {
                "type": "string",
                "enum": ["on", "off", "status"],
                "description": "Enable, disable, or query narration enrollment for this session.",
            },
            "mode": {
                "type": "string",
                "enum": ["summary", "verbatim", "raw"],
                "description": "Autoplay speech rewrite mode.",
            },
            "size": {
                "type": "string",
                "enum": ["small", "medium", "large"],
                "description": "Autoplay summary length.",
            },
            "scope": {
                "type": "string",
                "enum": ["all", "solo", "status"],
                "description": "Autoplay scope: 'solo' (only this session speaks) or 'all' (every enrolled session speaks).",
            },
            "subsystem": {
                "type": "string",
                "enum": ["autoplay", "narrate"],
                "description": "Optional subsystem selector for alternative dispatch.",
            },
            "action": {
                "type": "string",
                "description": "Optional action selector for alternative dispatch.",
            },
            "value": {
                "type": "string",
                "description": "Optional value for alternative dispatch.",
            },
        },
        "additionalProperties": False,
    },
}

SOUND_YIELD_TOOL = {
    "name": "sound_yield",
    "title": "Hold speech while another app plays sound",
    "description": (
        "By default auto-speech holds its speech while a player app (Music, "
        "Spotify, a browser, Zoom, the ChatGPT app, ...) plays sound and speaks "
        "once that sound stops; an unknown app's open stream never holds. "
        "'status' shows the switch, the rule, the players list, the exception "
        "list and the longest hold; 'on'/'off' switch the behaviour; "
        "'hold-for' with apps=['players'] or ['any'] sets whose sound holds; "
        "'player-add'/'player-remove'/'player-reset' edit the players list; "
        "'add'/'remove'/'clear' edit the exceptions, apps whose sound never "
        "holds (bundle ids like com.apple.Music or names like Music); "
        "'check' says who is playing right now, who holds the microphone, and "
        "whether speech would hold. A meeting app holding the microphone "
        "(Teams, Zoom, FaceTime, a browser on Meet, Slack, Webex, Discord) is "
        "a meeting: speech is set aside as missed at once, and one line after "
        "the meeting says how many were missed. 'meeting' with apps=['on'] "
        "(60 min), ['off'], ['for', '45m'] (8 h at most) or ['status'] is "
        "his own meeting mode. Same verbs as `auto-speech yield` and the "
        "[sound_yield] config."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(SOUND_YIELD_ACTIONS),
                "description": "status | on | off | add | remove | clear | check | hold-for | player-add | player-remove | player-reset | meeting",
            },
            "apps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "For add/remove/player-add/player-remove: bundle ids or app names; for hold-for: [\"players\"] or [\"any\"]; for meeting: [\"status\"], [\"on\"], [\"off\"] or [\"for\", \"45m\"].",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}

MISSED_TOOL = {
    "name": "missed",
    "title": "Speech held or set aside while another app played sound",
    "description": (
        "A line auto-speech held for another app's sound is never dropped. "
        "While it waits it is 'held'; past the longest wait, or when it "
        "turns out to be stale backlog once the sound stops, it is 'missed' "
        "and stays on this list until it plays. 'list' (default) shows the "
        "lines, oldest first; 'play' replays the missed ones in that order, "
        "over any other sound, and each leaves the list once it has played; "
        "'clear' forgets them all. Same verbs as `auto-speech missed`, "
        "`missed --play`, `missed --clear`."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(MISSED_ACTIONS),
                "description": "list | play | clear (default list)",
            },
        },
        "additionalProperties": False,
    },
}

SPEAKER_TOOL = {
    "name": "speaker",
    "title": "Name the desk only when the speaker changes",
    "description": (
        "Speech opens with '<desk> here,' only when the session speaking "
        "differs from the last one heard, or after a quiet gap (15 minutes "
        "by default). Names come from the desk agents' notes; a line that "
        "already opens with its own name is not doubled. 'status' shows the "
        "switch, the gap and how many desks are known; 'on'/'off' switch it; "
        "'gap' sets the quiet gap in seconds; 'name' says what to call a "
        "session (wins over the notes); 'forget' drops that; 'who' says what "
        "a session would be announced as. Same verbs as `auto-speech speaker` "
        "and the [speaker] config."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(SPEAKER_ACTIONS),
                "description": "status | on | off | gap | name | forget | who (default status)",
            },
            "seconds": {"type": "number", "description": "For gap: the quiet gap in seconds."},
            "session_id": {
                "type": "string",
                "description": "For name/forget/who: the session (who defaults to this server's own).",
            },
            "name": {"type": "string", "description": "For name: what to say for the session."},
        },
        "additionalProperties": False,
    },
}

HISTORY_TOOL = {
    "name": "history",
    "title": "Every line played, kept as a WAV and a sidecar, up to max_gb on disk",
    "description": (
        "Each line auto-speech plays is kept as a WAV with a JSON sidecar "
        "(its text, when it was spoken, who sent it, priority, duration, "
        "and word timings, estimated and marked so when the synthesizer "
        "gives none). The store holds up to `max_gb` on disk (20 GB by "
        "default), the oldest pruned first. A line set aside by a meeting or "
        "another app's sound is kept only once it plays. 'list' (default) "
        "shows the newest `keep` lines (200) with their WAV paths ('count' "
        "how many, 'as_json' for the sidecars); 'status' shows the settings; "
        "'on'/'off' switch the export; 'max-gb' bounds the store ('gb'); "
        "'keep' sets how many of the newest lines the player's view shows "
        "('count'; deletes nothing); 'player-on'/'player-off' switch the UI Utility's "
        "speech control: what plays, with the history and missed lines below it "
        "(default off, never opened during a hold). Same verbs as "
        "`auto-speech history` and the [history] config."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(HISTORY_ACTIONS),
                "description": "list | status | on | off | max-gb | keep | player-on | player-off (default list)",
            },
            "count": {
                "type": "integer",
                "minimum": 1,
                "description": "For keep: N, how many of the newest lines the player's view shows. For list: how many to show (default keep).",
            },
            "gb": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "For max-gb: the most the store may hold on disk, in GB (default 20).",
            },
            "as_json": {"type": "boolean", "default": False, "description": "For list: the sidecars as JSON."},
        },
        "additionalProperties": False,
    },
}

DOCTOR_TOOL = {
    "name": "doctor",
    "title": "Run system health checks and diagnostics",
    "description": (
        "Runs comprehensive health checks across binaries, Python virtual "
        "environment, disk headroom, logs, daemon status, queue depths, "
        "scopes, and configuration."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "as_json": {
                "type": "boolean",
                "default": False,
                "description": "If true, returns structured JSON output. If false, returns human-readable text report.",
            },
            "json_format": {
                "type": "boolean",
                "default": False,
                "description": "Alias for as_json.",
            },
        },
        "additionalProperties": False,
    },
}

ALL_TOOLS = [
    SPEAK_TOOL,
    AUTOPLAY_TURN_TOOL,
    NARRATE_EVENT_TOOL,
    PLAYBACK_CONTROL_TOOL,
    SESSION_CONFIGURE_TOOL,
    SOUND_YIELD_TOOL,
    MISSED_TOOL,
    SPEAKER_TOOL,
    HISTORY_TOOL,
    DOCTOR_TOOL,
]


def spawn_say_worker(text: str) -> None:
    """Offline speech fallback: spawns speak.py directly with text on stdin."""
    rotate_if_oversize(SAY_LOG)
    try:
        log = open(SAY_LOG, "ab")
    except OSError:
        log = subprocess.DEVNULL
    try:
        proc = subprocess.Popen(
            [sys.executable, str(SPEAK)],
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        if proc.stdin is not None:
            try:
                proc.stdin.write(text.encode("utf-8", errors="replace"))
                proc.stdin.close()
            except OSError:
                pass
    except Exception as exc:
        print(f"[{SERVER_NAME}] spawn_say_worker failed: {exc}", file=sys.stderr)
    finally:
        if log is not subprocess.DEVNULL:
            log.close()


def _tool_result(message: str, *, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": is_error}


class McpServer:
    """Handles one JSON-RPC message at a time. Collaborators injectable."""

    def __init__(
        self,
        *,
        spawner=None,
        gate: AutoplayGate | None = None,
        client: DaemonClient | None = None,
        home: Path | None = None,
        locator: TranscriptLocator | None = None,
        selector: MessageSelector | None = None,
        rewriter: ClaudeCliRewriter | None = None,
        events_log: Path | None = None,
        narrator_events_file: Path | None = None,
        narrator_sessions_dir: Path | None = None,
        voice_calibration_path: Path | None = None,
        mpv_sock: Path | None = None,
        mpv_sock_path: Path | None = None,
        mpv_pid: Path | None = None,
        mpv_pid_path: Path | None = None,
        mpv_ipc_sender=None,
        cache_store: CacheStore | None = None,
        enrollment: SessionEnrollment | None = None,
        scope: SoloScope | None = None,
        doctor: Doctor | None = None,
        is_mpv_alive=None,
        sound_yield_store=None,
        sound_yield_gate=None,
        missed_list=None,
        speaker_store=None,
        speaker_names=None,
        history_store=None,
        speech_history=None,
    ) -> None:
        self._home = Path(home) if home is not None else Path(os.path.expanduser("~"))
        self._spawn = spawner
        self._client = client
        self._gate = gate or AutoplayGate(home=self._home)
        self._locator = locator
        self._selector = selector
        self._rewriter = rewriter

        ev_log = events_log or narrator_events_file
        self._narrator_events_file = (
            Path(ev_log) if ev_log is not None else state_path("auto-speech-narrator-events.jsonl")
        )
        self._narrator_sessions_dir = (
            Path(narrator_sessions_dir)
            if narrator_sessions_dir is not None
            else (self._home / ".claude" / "auto-speech-narrate-sessions")
        )
        self._voice_calibration_path = (
            Path(voice_calibration_path) if voice_calibration_path is not None else None
        )

        sock_p = mpv_sock or mpv_sock_path
        self._mpv_sock_path = (
            Path(sock_p) if sock_p is not None else state_path("auto-speech-mpv.sock")
        )

        pid_p = mpv_pid or mpv_pid_path
        self._mpv_pid_path = (
            Path(pid_p) if pid_p is not None else state_path("auto-speech-mpv.pid")
        )

        self._mpv_ipc_send = mpv_ipc_sender or MpvIpc.send
        self._cache_store = cache_store
        self._enrollment = enrollment or SessionEnrollment(home=self._home)
        self._scope = scope or SoloScope(home=self._home)
        self._doctor = doctor
        self._is_mpv_alive_fn = is_mpv_alive
        self._sound_yield_store = sound_yield_store
        self._sound_yield_gate = sound_yield_gate
        self._missed_list = missed_list
        self._speaker_store = speaker_store
        self._speaker_names = speaker_names
        self._history_store = history_store
        self._speech_history = speech_history

    def _is_mpv_socket_alive(self) -> bool:
        if self._is_mpv_alive_fn is not None:
            return self._is_mpv_alive_fn()
        try:
            return self._mpv_sock_path.is_socket() or self._mpv_sock_path.exists()
        except OSError:
            return False

    # ---- dispatch --------------------------------------------------------
    def handle_line(self, line: str) -> dict | None:
        """Process one raw input line; return the response, or None."""
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return _error(None, PARSE_ERROR, "parse error")
        return self.handle(msg)

    def handle(self, msg) -> dict | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return _error(_id_of(msg), INVALID_REQUEST, "invalid request")
        method = msg.get("method")
        if not isinstance(method, str):
            # A response to something we never sent, or garbage: ignore
            # responses, reject requests.
            if "id" in msg and ("result" in msg or "error" in msg):
                return None
            return _error(_id_of(msg), INVALID_REQUEST, "invalid request")
        if "id" not in msg:
            return None  # notification (e.g. notifications/initialized)

        req_id = msg["id"]
        params = msg.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(req_id, INVALID_PARAMS, "params must be an object")

        if method == "initialize":
            return _ok(req_id, self._initialize(params))
        if method == "ping":
            return _ok(req_id, {})
        if method == "tools/list":
            return _ok(req_id, {"tools": ALL_TOOLS})
        if method == "tools/call":
            return self._tools_call(req_id, params)
        return _error(req_id, METHOD_NOT_FOUND, f"method not found: {method}")

    # ---- methods ---------------------------------------------------------
    def _initialize(self, params: dict) -> dict:
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "Use the speak tool whenever the user asks to hear something "
                "out loud — say it, read it aloud, speak it, voice it, text-"
                "to-speech it. It plays audio on this Mac. Write the text "
                "exactly as it should be heard."
            ),
        }

    def _tools_call(self, req_id, params: dict) -> dict:
        name = params.get("name")
        args = params.get("arguments")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return _error(req_id, INVALID_PARAMS, "arguments must be an object")

        handlers = {
            "speak": self._call_speak,
            "autoplay_turn": self._call_autoplay_turn,
            "narrate_event": self._call_narrate_event,
            "playback_control": self._call_playback_control,
            "session_configure": self._call_session_configure,
            "sound_yield": self._call_sound_yield,
            "missed": self._call_missed,
            "speaker": self._call_speaker,
            "history": self._call_history,
            "doctor": self._call_doctor,
        }

        handler = handlers.get(name)
        if handler is None:
            return _error(req_id, INVALID_PARAMS, f"unknown tool: {name}")

        return handler(req_id, args)

    # ---- tool handlers ---------------------------------------------------
    def _call_speak(self, req_id, args: dict) -> dict:
        text = args.get("text")
        if not isinstance(text, str) or not text.strip():
            return _ok(req_id, _tool_result("text must be a non-empty string", is_error=True))
        if len(text) > MAX_CHARS:
            return _ok(
                req_id,
                _tool_result(f"text exceeds {MAX_CHARS} characters", is_error=True),
            )
        if self._gate.worker_gated_off():
            return _ok(
                req_id,
                _tool_result(
                    "auto-speech is globally muted (~/.claude/auto-speech.disabled); "
                    "nothing was spoken",
                    is_error=True,
                ),
            )
        session_id = args.get("session_id")
        if session_id is not None and (not isinstance(session_id, str) or not session_id.strip()):
            return _ok(req_id, _tool_result("session_id must be a non-empty string", is_error=True))
        session_id = session_id or self._own_session_id()
        try:
            if self._spawn is not None:
                self._spawn(text)
            else:
                client = self._client or DaemonClient()
                try:
                    resp = client.speak(text, priority=Priority.EXPLICIT_MCP, session_id=session_id)
                    if resp.status == "error":
                        spawn_say_worker(text)
                except ConnectionError:
                    spawn_say_worker(text)
        except OSError as exc:
            return _ok(req_id, _tool_result(f"could not start speech: {exc}", is_error=True))
        except Exception as exc:
            return _ok(req_id, _tool_result(f"could not start speech: {exc}", is_error=True))
        return _ok(req_id, _tool_result(f"Queued {len(text)} characters to speak."))

    def _call_autoplay_turn(self, req_id, args: dict) -> dict:
        session_id = args.get("session_id")
        if session_id is not None and not isinstance(session_id, str):
            return _ok(req_id, _tool_result("session_id must be a string", is_error=True))
        if session_id is not None and not session_id.strip():
            return _ok(
                req_id, _tool_result("session_id must be a non-empty string", is_error=True)
            )

        transcript_path_str = args.get("transcript_path")
        if transcript_path_str is not None and not isinstance(transcript_path_str, str):
            return _ok(req_id, _tool_result("transcript_path must be a string", is_error=True))

        mode_arg = args.get("mode")
        if mode_arg is not None and mode_arg not in ("summary", "verbatim", "raw"):
            return _ok(
                req_id,
                _tool_result("mode must be 'summary', 'verbatim', or 'raw'", is_error=True),
            )

        size_arg = args.get("size")
        if size_arg is not None and size_arg not in ("small", "medium", "large"):
            return _ok(
                req_id,
                _tool_result("size must be 'small', 'medium', or 'large'", is_error=True),
            )

        ordinal = args.get("ordinal", 1)
        if not isinstance(ordinal, int) or ordinal < 1:
            return _ok(req_id, _tool_result("ordinal must be an integer >= 1", is_error=True))

        bypass_arg = args.get("bypass_llm")
        if bypass_arg is not None and not isinstance(bypass_arg, bool):
            return _ok(req_id, _tool_result("bypass_llm must be a boolean", is_error=True))

        # Global Mute Check
        if self._gate.worker_gated_off():
            return _ok(
                req_id,
                _tool_result(
                    "auto-speech is globally muted (~/.claude/auto-speech.disabled); nothing was spoken",
                    is_error=True,
                ),
            )

        # Transcript Resolution
        t_path: Path
        if transcript_path_str:
            cand = Path(transcript_path_str)
            if cand.is_file():
                t_path = cand
            else:
                return _ok(
                    req_id,
                    _tool_result(f"transcript not found: {transcript_path_str}", is_error=True),
                )
        else:
            locator = self._locator or TranscriptLocator()
            try:
                t_path = locator.locate(cwd=Path.cwd(), session_id=session_id)
            except Exception as exc:
                return _ok(
                    req_id,
                    _tool_result(f"no transcript found for session: {exc}", is_error=True),
                )
            if session_id and (t_path.name != f"{session_id}.jsonl" and t_path.stem != session_id):
                return _ok(
                    req_id,
                    _tool_result(
                        f"session transcript not found for session_id: {session_id}",
                        is_error=True,
                    ),
                )

        # Turn Extraction
        selector = self._selector or MessageSelector()
        try:
            msg = selector.select(t_path, n=ordinal)
        except Exception as exc:
            return _ok(
                req_id,
                _tool_result(f"no qualifying assistant turn found: {exc}", is_error=True),
            )

        raw_text = msg.text
        min_len = int(os.environ.get("AUTO_SPEECH_AUTOPLAY_MIN_LEN", "20"))
        raw_len = len(raw_text.encode("utf-8"))
        if raw_len < min_len:
            return _ok(
                req_id,
                _tool_result(
                    f"assistant turn text too short ({raw_len} chars < {min_len} chars); nothing to speak",
                    is_error=True,
                ),
            )

        # Cache Key Calculation
        voice_id = DEFAULT_VOICE_ID
        speed = DEFAULT_SPEED
        voice_cfg_path = (
            self._voice_calibration_path
            or (_PROJECT_ROOT / "config" / "voice_calibration.json")
        )
        if voice_cfg_path and voice_cfg_path.is_file():
            try:
                v_data = json.loads(voice_cfg_path.read_text(encoding="utf-8"))
                voice_id = str(v_data.get("voice_id", voice_id))
                speed = float(v_data.get("speed", speed))
            except Exception:
                pass

        key_input = raw_text.encode("utf-8") + b"\x00" + f"{voice_id}:{speed}".encode("utf-8")
        source_hash = hashlib.sha256(key_input).hexdigest()

        # Mode & Rewriting Pipeline
        cfg = load_autoplay_config()
        active_mode = mode_arg or cfg.get("mode") or "summary"
        active_size = size_arg or cfg.get("summary_size") or "small"
        bypass = bypass_arg
        if bypass is None:
            bypass = cfg.get("bypass_llm", False) or (active_mode == "raw")
        if active_mode == "raw":
            bypass = True

        if bypass:
            text_to_speak = raw_text
        else:
            prompt_path = _resolve_prompt(active_mode, active_size)
            template: str
            if prompt_path and Path(prompt_path).is_file():
                try:
                    template = Path(prompt_path).read_text(encoding="utf-8")
                except Exception:
                    template = load_default_template()
            else:
                template = load_default_template()

            rewriter = self._rewriter or ClaudeCliRewriter(template)
            try:
                rewritten = rewriter.rewrite(raw_text, timeout_seconds=90.0)
                text_to_speak = rewritten.strip() if rewritten and rewritten.strip() else raw_text
            except Exception as exc:
                print(
                    f"[{SERVER_NAME}] rewriter failed ({exc}); falling back to raw message",
                    file=sys.stderr,
                )
                text_to_speak = raw_text

        # Enqueueing to Daemon
        try:
            if self._spawn is not None:
                self._spawn(text_to_speak)
            else:
                client = self._client or DaemonClient()
                try:
                    resp = client.speak(
                        text_to_speak,
                        priority=Priority.AUTOPLAY,
                        source_hash=source_hash,
                        session_id=session_id or None,
                        voice_id=voice_id,
                        speed=speed,
                    )
                    if resp.status == "error":
                        spawn_say_worker(text_to_speak)
                except ConnectionError:
                    spawn_say_worker(text_to_speak)
        except Exception as exc:
            return _ok(req_id, _tool_result(f"could not queue speech: {exc}", is_error=True))

        return _ok(
            req_id,
            _tool_result(
                f"Queued autoplay turn ({len(text_to_speak)} chars, mode={active_mode}, hash={source_hash[:16]})."
            ),
        )

    def _call_narrate_event(self, req_id, args: dict) -> dict:
        event = args.get("event")
        if not isinstance(event, str) or not event.strip():
            return _ok(req_id, _tool_result("event must be a non-empty string", is_error=True))

        payload = args.get("payload")
        if payload is None:
            payload = {}
        elif not isinstance(payload, dict):
            return _ok(req_id, _tool_result("payload must be an object", is_error=True))

        cwd = args.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            return _ok(req_id, _tool_result("cwd must be a string", is_error=True))

        # Global Mute Check
        if self._gate.worker_gated_off():
            return _ok(
                req_id,
                _tool_result(
                    "auto-speech is globally muted (~/.claude/auto-speech.disabled); event not narrated",
                    is_error=True,
                ),
            )

        # Session Enrollment Check
        sid = (
            args.get("session_id")
            or (payload.get("session_id") if isinstance(payload, dict) else None)
            or (payload.get("conversationId") if isinstance(payload, dict) else None)
            or os.environ.get("CLAUDE_CODE_SESSION_ID")
            or os.environ.get("CLAUDE_SESSION_ID")
            or ""
        )
        if isinstance(sid, str):
            sid = sid.strip()
        if not sid:
            return _ok(
                req_id,
                _tool_result(
                    "No session_id provided and session is not enrolled in narration; event ignored.",
                    is_error=False,
                ),
            )

        marker = self._narrator_sessions_dir / sid
        if not marker.exists():
            return _ok(
                req_id,
                _tool_result(
                    f"Session '{sid}' is not enrolled in narration; event ignored.",
                    is_error=False,
                ),
            )

        # Daemon Liveness Check
        try:
            client = self._client or DaemonClient()
            client.ensure_daemon_running()
        except Exception as exc:
            print(f"[{SERVER_NAME}] ensure_daemon_running warning: {exc}", file=sys.stderr)

        record = {
            "ts": int(time.time()),
            "cwd": cwd or str(Path.cwd()),
            "pid": os.getpid(),
            "event": event,
            "payload": payload,
            "session_id": sid,
        }

        try:
            self._narrator_events_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._narrator_events_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            return _ok(req_id, _tool_result(f"could not log narrator event: {exc}", is_error=True))

        return _ok(
            req_id,
            _tool_result(f"Narrator event '{event}' ingested for session '{sid}'."),
        )

    def _call_playback_control(self, req_id: Any, args: dict) -> dict:
        action = args.get("action")
        if not isinstance(action, str) or action not in (
            "pause",
            "resume",
            "stop",
            "restart",
            "seek",
            "replay",
        ):
            return _ok(
                req_id,
                _tool_result(
                    f"unknown action: {action!r} (expected pause, resume, stop, restart, seek, replay)",
                    is_error=True,
                ),
            )

        # 1. STOP
        if action == "stop":
            client = self._client or DaemonClient()
            try:
                client.interrupt()
            except Exception:
                pass

            if self._is_mpv_socket_alive():
                try:
                    self._mpv_ipc_send(["stop"], self._mpv_sock_path)
                except Exception:
                    pass

            mpv_pid_file = self._mpv_pid_path
            if mpv_pid_file.is_file():
                try:
                    pid = int(mpv_pid_file.read_text().strip())
                    os.kill(pid, 15)
                    time.sleep(0.05)
                    try:
                        os.kill(pid, 0)
                        os.kill(pid, 9)
                    except OSError:
                        pass
                except (ValueError, OSError):
                    pass
                finally:
                    try:
                        mpv_pid_file.unlink()
                    except OSError:
                        pass

            return _ok(req_id, _tool_result(
                "Playback stopped and queue cleared. Speech stays stopped: lines that arrive go on the missed list "
                "until playback_control resume (or the control's play)."
            ))

        # 2. PAUSE / RESUME / RESTART
        if action in ("pause", "resume", "restart"):
            if action == "resume" and self._gate.worker_gated_off():
                return _ok(
                    req_id,
                    _tool_result(
                        "auto-speech is globally muted (~/.claude/auto-speech.disabled); cannot resume playback",
                        is_error=True,
                    ),
                )

            if action == "resume":
                # His stop (ADR-024) ends here too; the lines it set aside play.
                try:
                    raw = (self._client or DaemonClient(max_retries=0)).resume().raw or {}
                except Exception:  # noqa: BLE001
                    raw = {}
                if isinstance(raw, dict) and raw.get("was_stopped") is True:
                    n = int(raw.get("queued") or 0)
                    return _ok(req_id, _tool_result(
                        f"Speech resumed after a stop; {n} line{'s' if n != 1 else ''} that arrived meanwhile queued again."
                    ))

            if not self._is_mpv_socket_alive():
                return _ok(req_id, _tool_result("No active playback session.", is_error=True))

            if action == "pause":
                cmd = ["set_property", "pause", True]
                msg = "Playback paused."
            elif action == "resume":
                cmd = ["set_property", "pause", False]
                msg = "Playback resumed."
            else:  # restart
                cmd = ["seek", 0, "absolute"]
                msg = "Playback restarted."

            try:
                reply = self._mpv_ipc_send(cmd, self._mpv_sock_path)
            except Exception as exc:
                return _ok(req_id, _tool_result(f"mpv IPC failure: {exc}", is_error=True))

            if isinstance(reply, dict) and reply.get("error") != "success":
                return _ok(
                    req_id,
                    _tool_result(
                        f"mpv command returned error: {reply.get('error')}", is_error=True
                    ),
                )
            return _ok(req_id, _tool_result(msg))

        # 3. SEEK
        if action == "seek":
            target = args.get("target")
            if not target or not isinstance(target, str) or not target.strip():
                return _ok(
                    req_id,
                    _tool_result("seek requires a target (+N, -N, N, or 'end')", is_error=True),
                )
            target = target.strip()

            if target == "end":
                cmd = ["seek", 999999, "absolute"]
            elif target.startswith("+"):
                try:
                    val = float(target[1:])
                    if not math.isfinite(val):
                        raise ValueError
                    cmd = ["seek", val, "relative"]
                except ValueError:
                    return _ok(
                        req_id,
                        _tool_result(
                            "seek requires a target (+N, -N, N, or 'end')", is_error=True
                        ),
                    )
            elif target.startswith("-"):
                try:
                    val = float(target)
                    if not math.isfinite(val):
                        raise ValueError
                    cmd = ["seek", val, "relative"]
                except ValueError:
                    return _ok(
                        req_id,
                        _tool_result(
                            "seek requires a target (+N, -N, N, or 'end')", is_error=True
                        ),
                    )
            else:
                try:
                    val = float(target)
                    if not math.isfinite(val) or val < 0:
                        raise ValueError
                    cmd = ["seek", val, "absolute"]
                except ValueError:
                    return _ok(
                        req_id,
                        _tool_result(
                            "seek requires a target (+N, -N, N, or 'end')", is_error=True
                        ),
                    )

            if not self._is_mpv_socket_alive():
                return _ok(req_id, _tool_result("No active playback session.", is_error=True))

            try:
                reply = self._mpv_ipc_send(cmd, self._mpv_sock_path)
            except Exception as exc:
                return _ok(req_id, _tool_result(f"mpv IPC failure: {exc}", is_error=True))

            if isinstance(reply, dict) and reply.get("error") != "success":
                return _ok(
                    req_id,
                    _tool_result(
                        f"mpv command returned error: {reply.get('error')}", is_error=True
                    ),
                )
            return _ok(req_id, _tool_result(f"Seek to {target} successful."))

        # 4. REPLAY
        if action == "replay":
            if self._gate.worker_gated_off():
                return _ok(
                    req_id,
                    _tool_result(
                        "auto-speech is globally muted (~/.claude/auto-speech.disabled); replay suppressed",
                        is_error=True,
                    ),
                )

            ordinal = args.get("ordinal", 1)
            if not isinstance(ordinal, int) or ordinal < 1:
                return _ok(
                    req_id,
                    _tool_result(
                        f"ordinal must be an integer >= 1, got {ordinal!r}", is_error=True
                    ),
                )

            store = self._cache_store or CacheStore(_default_cache_root())
            entries = store.list_by_recency()
            if not entries or ordinal > len(entries):
                return _ok(
                    req_id,
                    _tool_result(
                        f"No cached audio available for replay (ordinal={ordinal}).",
                        is_error=True,
                    ),
                )

            _wav_path, entry = entries[ordinal - 1]
            client = self._client or DaemonClient()
            try:
                resp = client.play_cache(entry.source_hash, session_id=self._own_session_id())
                if resp.status not in ("ok", "queued"):
                    return _ok(
                        req_id,
                        _tool_result(
                            f"Replay request rejected by daemon: {resp.message}",
                            is_error=True,
                        ),
                    )
            except Exception as exc:
                return _ok(
                    req_id,
                    _tool_result(f"Could not connect to daemon for replay: {exc}", is_error=True),
                )

            return _ok(
                req_id,
                _tool_result(
                    f"Replaying cached audio (ordinal={ordinal}, voice={entry.voice_id}, duration={entry.duration_seconds:.1f}s)."
                ),
            )

    def _call_session_configure(self, req_id: Any, args: dict) -> dict:
        raw_sid = args.get("session_id")
        session_id = (
            raw_sid.strip() if isinstance(raw_sid, str) else ""
        ) or os.environ.get("CLAUDE_CODE_SESSION_ID", "")

        subsystem = args.get("subsystem")
        action = args.get("action")
        value = args.get("value")

        if subsystem is not None and subsystem not in ("autoplay", "narrate"):
            return _ok(
                req_id,
                _tool_result(
                    f"invalid subsystem: {subsystem!r} (expected autoplay, narrate)",
                    is_error=True,
                ),
            )

        if subsystem == "autoplay":
            if action in ("on", "off", "status"):
                args.setdefault("autoplay", action)
            elif action == "mode":
                if not value or not str(value).strip():
                    return _ok(
                        req_id,
                        _tool_result("action 'mode' requires 'value'", is_error=True),
                    )
                if value in ("small", "medium", "large"):
                    args.setdefault("mode", "summary")
                    args.setdefault("size", value)
                else:
                    args.setdefault("mode", value)
            elif action == "scope":
                if not value or not str(value).strip():
                    return _ok(
                        req_id,
                        _tool_result("action 'scope' requires 'value'", is_error=True),
                    )
                args.setdefault("scope", value)
            elif action is not None:
                return _ok(
                    req_id,
                    _tool_result(
                        f"invalid autoplay action: {action!r} (expected on, off, status, mode, scope)",
                        is_error=True,
                    ),
                )
        elif subsystem == "narrate":
            if action in ("on", "off", "status"):
                args.setdefault("narrate", action)
            elif action is not None:
                return _ok(
                    req_id,
                    _tool_result(
                        f"invalid narrate action: {action!r} (expected on, off, status)",
                        is_error=True,
                    ),
                )
        elif subsystem is None and action is not None:
            if action == "mode":
                if not value or not str(value).strip():
                    return _ok(
                        req_id,
                        _tool_result("action 'mode' requires 'value'", is_error=True),
                    )
                if value in ("small", "medium", "large"):
                    args.setdefault("mode", "summary")
                    args.setdefault("size", value)
                else:
                    args.setdefault("mode", value)
            elif action == "scope":
                if not value or not str(value).strip():
                    return _ok(
                        req_id,
                        _tool_result("action 'scope' requires 'value'", is_error=True),
                    )
                args.setdefault("scope", value)
            else:
                return _ok(
                    req_id,
                    _tool_result(
                        f"unhandled action {action!r} without valid subsystem",
                        is_error=True,
                    ),
                )

        results: list[str] = []

        # 1. AUTOPLAY
        if "autoplay" in args:
            ap_act = args.get("autoplay")
            if ap_act not in ("on", "off", "status"):
                return _ok(
                    req_id,
                    _tool_result(
                        f"invalid autoplay action: {ap_act!r} (expected on, off, status)",
                        is_error=True,
                    ),
                )
            if ap_act == "on":
                if not session_id:
                    return _ok(
                        req_id, _tool_result("autoplay on requires a session_id", is_error=True)
                    )
                disabled_marker = self._home / ".claude" / "auto-speech.disabled"
                try:
                    disabled_marker.unlink(missing_ok=True)
                except OSError:
                    pass
                self._enrollment.enroll(session_id)
                results.append(f"autoplay: enabled for session {session_id}")
            elif ap_act == "off":
                if session_id:
                    self._enrollment.withdraw(session_id)
                    results.append(f"autoplay: disabled for session {session_id}")
                else:
                    mute_path = self._home / ".claude" / "auto-speech.disabled"
                    mute_path.parent.mkdir(parents=True, exist_ok=True)
                    mute_path.touch()
                    results.append(
                        "autoplay: no session_id provided — set global mute marker (~/.claude/auto-speech.disabled)"
                    )
            elif ap_act == "status":
                results.append(format_enrollment_status(self._enrollment, session_id or None))

        # 2. NARRATE
        if "narrate" in args:
            narrate_act = args.get("narrate")
            if narrate_act not in ("on", "off", "status"):
                return _ok(
                    req_id,
                    _tool_result(
                        f"invalid narrate action: {narrate_act!r} (expected on, off, status)",
                        is_error=True,
                    ),
                )
            narrate_dir = self._home / ".claude" / "auto-speech-narrate-sessions"
            if narrate_act == "on":
                if not session_id:
                    return _ok(
                        req_id, _tool_result("narrate on requires a session_id", is_error=True)
                    )
                narrate_dir.mkdir(parents=True, exist_ok=True)
                (narrate_dir / session_id).touch()
                try:
                    (Path.cwd() / ".claude" / "narrate.enabled").unlink(missing_ok=True)
                except OSError:
                    pass
                client = self._client or DaemonClient()
                try:
                    client.ensure_daemon_running()
                except Exception:
                    pass
                results.append(f"narrate: enabled for session {session_id}")
            elif narrate_act == "off":
                if session_id:
                    marker = narrate_dir / session_id
                    try:
                        marker.unlink(missing_ok=True)
                    except OSError:
                        pass
                    results.append(f"narrate: disabled for session {session_id}")
                else:
                    results.append("narrate off: no session_id provided")
            elif narrate_act == "status":
                is_enrolled = (
                    (narrate_dir / session_id).is_file()
                    if session_id and narrate_dir.is_dir()
                    else False
                )
                total = len(list(narrate_dir.glob("*"))) if narrate_dir.is_dir() else 0
                client = self._client or DaemonClient()
                daemon_alive = False
                try:
                    daemon_alive = client.is_alive()
                except Exception:
                    pass
                results.append(
                    f"narrate: session={session_id or '<none>'}, enrolled={is_enrolled} (total enrolled: {total}), daemon_alive={daemon_alive}"
                )

        # 3. SCOPE
        if "scope" in args:
            scope_act = args.get("scope")
            if scope_act not in ("all", "solo", "status"):
                return _ok(
                    req_id,
                    _tool_result(
                        f"invalid scope action: {scope_act!r} (expected all, solo, status)",
                        is_error=True,
                    ),
                )
            if scope_act == "solo":
                if not session_id:
                    return _ok(
                        req_id, _tool_result("scope solo requires a session_id", is_error=True)
                    )
                self._scope.set_solo(session_id)
                self._enrollment.enroll(session_id)
                results.append(
                    f"autoplay scope: SOLO — only THIS session reads (spotlight={session_id})"
                )
            elif scope_act == "all":
                self._scope.clear()
                results.append("autoplay scope: ALL — every enrolled session reads (default)")
            elif scope_act == "status":
                results.append(format_scope_status(self._scope, session_id or None))

        # 4. MODE / SIZE
        if "mode" in args or "size" in args:
            mode_val = args.get("mode")
            size_val = args.get("size")
            if mode_val is not None and mode_val not in ("summary", "verbatim", "raw"):
                return _ok(
                    req_id,
                    _tool_result(
                        f"invalid mode: {mode_val!r} (expected summary, verbatim, raw)",
                        is_error=True,
                    ),
                )
            if size_val is not None and size_val not in ("small", "medium", "large"):
                return _ok(
                    req_id,
                    _tool_result(
                        f"invalid size: {size_val!r} (expected small, medium, large)",
                        is_error=True,
                    ),
                )

            cfg = load_autoplay_config()
            cur_mode = mode_val or cfg.get("mode", "summary")
            cur_size = size_val or cfg.get("summary_size", "small")
            if cur_mode in ("verbatim", "raw") and size_val is None:
                cur_size = "small"

            config_dir = self._home / ".config" / "auto-speech"
            config_dir.mkdir(parents=True, exist_ok=True)
            config_file = config_dir / "autoplay.toml"
            config_file.write_text(
                f'[autoplay]\nmode = "{cur_mode}"\nsummary_size = "{cur_size}"\n',
                encoding="utf-8",
            )
            results.append(f"wrote {config_file}: mode={cur_mode} summary_size={cur_size}")

        # 5. NO ARGS -> RETURN SUMMARY
        if not results:
            results.append(format_enrollment_status(self._enrollment, session_id or None))
            results.append(format_scope_status(self._scope, session_id or None))
            cfg = load_autoplay_config()
            results.append(f"mode={cfg.get('mode')} summary_size={cfg.get('summary_size')}")

        return _ok(req_id, _tool_result("\n".join(results)))

    def _call_sound_yield(self, req_id: Any, args: dict) -> dict:
        action = args.get("action")
        if not isinstance(action, str) or action.strip().lower() not in SOUND_YIELD_ACTIONS:
            return _ok(
                req_id,
                _tool_result(
                    f"invalid action: {action!r} (expected {', '.join(SOUND_YIELD_ACTIONS)})",
                    is_error=True,
                ),
            )
        apps = args.get("apps", [])
        if isinstance(apps, str):
            apps = [apps]
        if not isinstance(apps, list) or any(not isinstance(a, str) for a in apps):
            return _ok(req_id, _tool_result("apps must be a list of strings", is_error=True))
        try:
            result = run_sound_yield_action(
                action, apps, store=self._sound_yield_store, gate=self._sound_yield_gate
            )
        except Exception as exc:  # noqa: BLE001
            return _ok(req_id, _tool_result(f"sound_yield failed: {exc}", is_error=True))
        return _ok(req_id, _tool_result(result.text, is_error=not result.ok))

    def _call_missed(self, req_id: Any, args: dict) -> dict:
        action = args.get("action", "list")
        if not isinstance(action, str) or action.strip().lower() not in MISSED_ACTIONS:
            return _ok(
                req_id,
                _tool_result(
                    f"invalid action: {action!r} (expected {', '.join(MISSED_ACTIONS)})",
                    is_error=True,
                ),
            )
        try:
            result = run_missed_action(action, missed=self._missed_list, client=self._client)
        except Exception as exc:  # noqa: BLE001
            return _ok(req_id, _tool_result(f"missed failed: {exc}", is_error=True))
        return _ok(req_id, _tool_result(result.text, is_error=not result.ok))

    @staticmethod
    def _own_session_id() -> str | None:
        """The Claude Code session this server serves, from its environment."""
        return os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID") or None

    def _call_speaker(self, req_id: Any, args: dict) -> dict:
        action = args.get("action", "status")
        if not isinstance(action, str) or action.strip().lower() not in SPEAKER_ACTIONS:
            return _ok(
                req_id,
                _tool_result(
                    f"invalid action: {action!r} (expected {', '.join(SPEAKER_ACTIONS)})",
                    is_error=True,
                ),
            )
        seconds = args.get("seconds")
        if seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, (int, float))):
            return _ok(req_id, _tool_result("seconds must be a number", is_error=True))
        session_id = args.get("session_id")
        if session_id is not None and not isinstance(session_id, str):
            return _ok(req_id, _tool_result("session_id must be a string", is_error=True))
        name = args.get("name")
        if name is not None and not isinstance(name, str):
            return _ok(req_id, _tool_result("name must be a string", is_error=True))
        try:
            result = run_speaker_action(
                action,
                seconds=seconds,
                session_id=session_id or self._own_session_id(),
                name=name,
                store=self._speaker_store,
                names=self._speaker_names,
            )
        except Exception as exc:  # noqa: BLE001
            return _ok(req_id, _tool_result(f"speaker failed: {exc}", is_error=True))
        return _ok(req_id, _tool_result(result.text, is_error=not result.ok))

    def _call_history(self, req_id: Any, args: dict) -> dict:
        action = args.get("action", "list")
        if not isinstance(action, str) or action.strip().lower() not in HISTORY_ACTIONS:
            return _ok(
                req_id,
                _tool_result(
                    f"invalid action: {action!r} (expected {', '.join(HISTORY_ACTIONS)})",
                    is_error=True,
                ),
            )
        count = args.get("count")
        if count is not None and (isinstance(count, bool) or not isinstance(count, int)):
            return _ok(req_id, _tool_result("count must be a whole number", is_error=True))
        gb = args.get("gb")
        if gb is not None and (isinstance(gb, bool) or not isinstance(gb, (int, float))):
            return _ok(req_id, _tool_result("gb must be a number", is_error=True))
        as_json = args.get("as_json", False)
        if not isinstance(as_json, bool):
            return _ok(req_id, _tool_result("as_json must be true or false", is_error=True))
        store = self._history_store
        try:
            result = run_history_action(
                action,
                count=count,
                gb=gb,
                as_json=as_json,
                store=store,
                history=self._speech_history,
                player_note=speech_control.note,
            )
        except Exception as exc:  # noqa: BLE001
            return _ok(req_id, _tool_result(f"history failed: {exc}", is_error=True))
        return _ok(req_id, _tool_result(result.text, is_error=not result.ok))

    def _call_doctor(self, req_id: Any, args: dict) -> dict:
        as_json = bool(args.get("as_json") or args.get("json_format", False))
        doc = self._doctor or Doctor(home=self._home)
        try:
            report = doc.run()
        except Exception as exc:
            return _ok(req_id, _tool_result(f"doctor check crashed: {exc}", is_error=True))

        content = report.to_json() if as_json else report.to_text()
        is_error = not report.healthy
        return _ok(req_id, _tool_result(content, is_error=is_error))


def _id_of(msg):
    return msg.get("id") if isinstance(msg, dict) else None


def _ok(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def serve(stdin=sys.stdin, stdout=sys.stdout, server: McpServer | None = None) -> int:
    server = server or McpServer()
    for line in stdin:
        if not line.strip():
            continue
        response = server.handle_line(line)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(serve())
