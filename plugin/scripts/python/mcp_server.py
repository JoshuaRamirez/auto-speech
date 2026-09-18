"""auto-speech MCP server: a `speak` tool that says the words it is given.

Stdio transport, newline-delimited JSON-RPC 2.0, standard library only.
Exposes one tool:

  speak(text)  say `text` aloud, verbatim, with the local Kokoro voice.

The call returns as soon as the words are queued; a detached SayWorker
waits its turn in the cross-session playback FIFO (never cutting off
another playback) and speaks them. The global mute
(~/.claude/auto-speech.disabled) silences this tool too.

stdout carries protocol messages only; diagnostics go to stderr.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from auto_speech_log import rotate_if_oversize
from autoplay_gate import AutoplayGate

SERVER_NAME = "auto-speech"
SERVER_VERSION = "0.2.0"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_CHARS = 20000  # matches web_server's /api/synthesize cap

_PYTHON_DIR = Path(__file__).resolve().parent
SAY_WORKER = _PYTHON_DIR / "say_worker.py"
SAY_LOG = Path("/tmp/auto-speech-say.log")

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

SPEAK_TOOL = {
    "name": "speak",
    "title": "Speak text aloud (local text-to-speech)",
    "description": (
        "Speak text aloud: say, read out loud, voice, narrate, or vocalize "
        "the given words as audio on this Mac's speakers. Local text-to-"
        "speech (TTS) with the Kokoro voice — no cloud service, no API key. "
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
            }
        },
        "required": ["text"],
        "additionalProperties": False,
    },
}


def spawn_say_worker(text: str) -> None:
    """Write `text` to a private temp file and start a detached SayWorker."""
    fd, path = tempfile.mkstemp(prefix="auto-speech-say-", suffix=".txt")  # mode 0600
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    rotate_if_oversize(SAY_LOG)
    try:
        log = open(SAY_LOG, "ab")
    except OSError:
        log = subprocess.DEVNULL
    try:
        subprocess.Popen(
            [sys.executable, str(SAY_WORKER), path],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        os.unlink(path)
        raise
    finally:
        if log is not subprocess.DEVNULL:
            log.close()


def _tool_result(message: str, *, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": is_error}


class McpServer:
    """Handles one JSON-RPC message at a time. Collaborators injectable."""

    def __init__(self, *, spawner=spawn_say_worker, gate: AutoplayGate | None = None) -> None:
        self._spawn = spawner
        self._gate = gate or AutoplayGate()

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
            return _ok(req_id, {"tools": [SPEAK_TOOL]})
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
        if name != "speak":
            return _error(req_id, INVALID_PARAMS, f"unknown tool: {name}")
        args = params.get("arguments")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return _error(req_id, INVALID_PARAMS, "arguments must be an object")
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
        try:
            self._spawn(text)
        except OSError as exc:
            return _ok(req_id, _tool_result(f"could not start speech: {exc}", is_error=True))
        return _ok(req_id, _tool_result(f"Queued {len(text)} characters to speak."))


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
