"""Unit tests for the auto-speech MCP server (expanded 6-tool suite).

Drives McpServer with raw JSON-RPC lines and mocked collaborators, so no
audio hardware, real worker processes, or host system markers are touched.

Covers:
  - Protocol initialization, protocol version negotiation, ping, and notifications
  - tools/list advertises all seven tools with complete schemas
  - speak tool (100% backward compatibility, parameter validation, global mute, fallbacks)
  - autoplay_turn tool (extraction, min-len check, caching hash, rewriting, fallbacks, mute)
  - narrate_event tool (parameter validation, enrollment gating, daemon check, JSONL logging)
  - playback_control tool (stop, pause, resume, restart, seek targets, replay cache, mute)
  - session_configure tool (autoplay markers, narrate markers, solo scope, config TOML)
  - doctor tool (human text report, structured JSON, health failure isError signaling)
  - stdio line framing and protocol errors
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Hermetic home: McpServer reads the global mute flag (~/.claude/auto-speech.disabled),
# the enrollment and the scope under HOME, so a server built without home= must never
# see this machine's (the suite failed at 10:47 on 2026-10-20 when his global mute was on).
os.environ["HOME"] = tempfile.mkdtemp(prefix="auto-speech-mcp-home-")

# The caller's Claude Code session must not leak into these tests: the server
# falls back to CLAUDE_CODE_SESSION_ID / CLAUDE_SESSION_ID when a call names
# no session, which would turn every "without session" case into a
# per-session one when the file runs from a Claude Code shell.
for _var in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"):
    os.environ.pop(_var, None)

import mcp_server  # noqa: E402
from assistant_message import AssistantMessage  # noqa: E402
from autoplay_gate import AutoplayGate  # noqa: E402
from autoplay_scope import SoloScope  # noqa: E402
from cache_entry import CacheEntry  # noqa: E402
from claude_cli_rewriter import ClaudeCliRewriteError  # noqa: E402
from daemon_client import DaemonResponse, Priority  # noqa: E402
from health_report import Check, HealthReport, Status  # noqa: E402
from mcp_server import McpServer, serve  # noqa: E402
from message_selector import NoSuchAssistantTurn  # noqa: E402
from transcript_locator import TranscriptLocator  # noqa: E402


def _server(muted: bool = False, spawn_error: Exception | None = None, **kwargs):
    home = Path(tempfile.mkdtemp(prefix="auto-speech-mcp-test-"))
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    if muted:
        (claude_dir / "auto-speech.disabled").touch()
    spoken: list[str] = []

    def spawner(text: str) -> None:
        if spawn_error is not None:
            raise spawn_error
        spoken.append(text)

    srv = McpServer(
        spawner=spawner,
        gate=AutoplayGate(home=home),
        home=home,
        **kwargs,
    )
    return srv, spoken


def _req(method: str, params=None, req_id=1) -> str:
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def _call_speak(server: McpServer, arguments) -> dict:
    return server.handle_line(_req("tools/call", {"name": "speak", "arguments": arguments}))


def _call_tool(server: McpServer, name: str, arguments=None, req_id=1) -> dict:
    return server.handle_line(
        _req("tools/call", {"name": name, "arguments": arguments if arguments is not None else {}}, req_id=req_id)
    )


# =========================================================================
# Group 1: Protocol & Advertisement
# =========================================================================

def test_initialize_echoes_supported_protocol() -> None:
    s, _ = _server()
    r = s.handle_line(_req("initialize", {"protocolVersion": "2025-03-26"}))
    assert r["id"] == 1
    assert r["result"]["protocolVersion"] == "2025-03-26"
    assert r["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert r["result"]["serverInfo"]["name"] == "auto-speech"


def test_initialize_unknown_protocol_falls_back_to_latest() -> None:
    s, _ = _server()
    r = s.handle_line(_req("initialize", {"protocolVersion": "1999-01-01"}))
    assert r["result"]["protocolVersion"] == mcp_server.SUPPORTED_PROTOCOLS[0]


def test_notification_gets_no_response() -> None:
    s, _ = _server()
    line = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert s.handle_line(line) is None


def test_ping() -> None:
    s, _ = _server()
    assert s.handle_line(_req("ping"))["result"] == {}


def test_tools_list_advertises_all_ten_tools() -> None:
    s, _ = _server()
    tools = s.handle_line(_req("tools/list"))["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    expected_names = [
        "speak",
        "autoplay_turn",
        "narrate_event",
        "playback_control",
        "session_configure",
        "sound_yield",
        "missed",
        "speaker",
        "history",
        "doctor",
    ]
    assert tool_names == expected_names

    tool_dict = {t["name"]: t for t in tools}
    assert tool_dict["speak"]["inputSchema"]["required"] == ["text"]
    assert tool_dict["narrate_event"]["inputSchema"]["required"] == ["event"]
    assert tool_dict["playback_control"]["inputSchema"]["required"] == ["action"]

    for name in expected_names:
        schema = tool_dict[name]["inputSchema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False


# =========================================================================
# Group 2: Backward Compatibility of speak
# =========================================================================

def test_speak_queues_text_verbatim() -> None:
    s, spoken = _server()
    text = "Hello there.\n  Two lines, *verbatim*."
    r = _call_speak(s, {"text": text})
    assert r["result"]["isError"] is False
    assert spoken == [text]
    assert str(len(text)) in r["result"]["content"][0]["text"]


def test_speak_rejects_bad_text() -> None:
    for args in ({}, {"text": ""}, {"text": "   \n"}, {"text": 42}, {"text": None}):
        s, spoken = _server()
        r = _call_speak(s, args)
        assert r["result"]["isError"] is True, args
        assert spoken == [], args


def test_speak_rejects_oversize_text() -> None:
    s, spoken = _server()
    r = _call_speak(s, {"text": "a" * (mcp_server.MAX_CHARS + 1)})
    assert r["result"]["isError"] is True
    assert spoken == []
    r = _call_speak(s, {"text": "a" * mcp_server.MAX_CHARS})
    assert r["result"]["isError"] is False


def test_speak_honors_global_mute() -> None:
    s, spoken = _server(muted=True)
    r = _call_speak(s, {"text": "should stay silent"})
    assert r["result"]["isError"] is True
    assert "muted" in r["result"]["content"][0]["text"]
    assert spoken == []


def test_speak_spawn_failure_is_tool_error() -> None:
    s, _ = _server(spawn_error=OSError("no python"))
    r = _call_speak(s, {"text": "hello world"})
    assert r["result"]["isError"] is True
    assert "no python" in r["result"]["content"][0]["text"]


def test_protocol_errors() -> None:
    s, _ = _server()
    r = s.handle_line(_req("tools/call", {"name": "shout", "arguments": {"text": "x"}}))
    assert r["error"]["code"] == mcp_server.INVALID_PARAMS
    r = s.handle_line(_req("tools/call", {"name": "speak", "arguments": ["x"]}))
    assert r["error"]["code"] == mcp_server.INVALID_PARAMS
    r = s.handle_line(_req("tools/call", ["speak"]))
    assert r["error"]["code"] == mcp_server.INVALID_PARAMS
    r = s.handle_line(_req("resources/list", req_id=7))
    assert r["error"]["code"] == mcp_server.METHOD_NOT_FOUND and r["id"] == 7
    r = s.handle_line("{not json")
    assert r["error"]["code"] == mcp_server.PARSE_ERROR and r["id"] is None
    r = s.handle_line(json.dumps([_req("ping")]))
    assert r["error"]["code"] == mcp_server.INVALID_REQUEST
    r = s.handle_line(json.dumps({"id": 3, "method": "ping"}))  # missing jsonrpc
    assert r["error"]["code"] == mcp_server.INVALID_REQUEST and r["id"] == 3


def test_response_messages_are_ignored() -> None:
    s, _ = _server()
    assert s.handle_line(json.dumps({"jsonrpc": "2.0", "id": 9, "result": {}})) is None


def test_serve_writes_one_line_per_request() -> None:
    s, spoken = _server()
    lines = "\n".join(
        [
            _req("initialize", {"protocolVersion": "2025-06-18"}, req_id=1),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            "",
            _req("tools/call", {"name": "speak", "arguments": {"text": "hi — ünïcode"}}, 2),
        ]
    )
    out = io.StringIO()
    assert serve(io.StringIO(lines + "\n"), out, s) == 0
    responses = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["id"] for r in responses] == [1, 2]
    assert spoken == ["hi — ünïcode"]


def test_plugin_mcp_manifest_points_at_the_launcher() -> None:
    """plugin/.mcp.json is what a managed-plugin install auto-discovers."""
    plugin_root = Path(__file__).resolve().parents[1] / "plugin"
    manifest = json.loads((plugin_root / ".mcp.json").read_text(encoding="utf-8"))
    entry = manifest["mcpServers"]["auto-speech"]
    assert entry["command"] == "bash"
    (arg,) = entry["args"]
    assert arg.startswith("${CLAUDE_PLUGIN_ROOT}/")
    launcher = plugin_root / arg.removeprefix("${CLAUDE_PLUGIN_ROOT}/")
    assert launcher.is_file() and os.access(launcher, os.X_OK), launcher


def test_speak_invokes_daemon_client_directly() -> None:
    mock_client = MagicMock()
    mock_client.speak.return_value = DaemonResponse(status="ok", action="speak")

    server = McpServer(client=mock_client)
    res = server.handle_line(
        _req("tools/call", {"name": "speak", "arguments": {"text": "hello sublimated"}})
    )
    assert res["result"]["isError"] is False
    assert "Queued 16 characters" in res["result"]["content"][0]["text"]
    mock_client.speak.assert_called_once_with(
        "hello sublimated", priority=Priority.EXPLICIT_MCP, session_id=None
    )


def test_speak_carries_the_session_from_the_call_or_the_environment() -> None:
    """Every path carries its session so the daemon can name the desk only
    when the speaker changes (his 19:06)."""
    mock_client = MagicMock()
    mock_client.speak.return_value = DaemonResponse(status="ok", action="speak")
    server = McpServer(client=mock_client)
    server.handle_line(_req("tools/call", {"name": "speak", "arguments": {"text": "hi", "session_id": "sess-arg"}}))
    assert mock_client.speak.call_args.kwargs["session_id"] == "sess-arg"
    with patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "sess-env"}):
        server.handle_line(_req("tools/call", {"name": "speak", "arguments": {"text": "hi again"}}))
    assert mock_client.speak.call_args.kwargs["session_id"] == "sess-env"
    res = server.handle_line(_req("tools/call", {"name": "speak", "arguments": {"text": "hi", "session_id": " "}}))
    assert res["result"]["isError"] is True


def test_speak_falls_back_to_spawn_say_worker_on_connection_error() -> None:
    mock_client = MagicMock()
    mock_client.speak.side_effect = ConnectionError("daemon offline")

    with patch("mcp_server.spawn_say_worker") as mock_spawn:
        server = McpServer(client=mock_client)
        res = server.handle_line(
            _req("tools/call", {"name": "speak", "arguments": {"text": "fallback text"}})
        )
        assert res["result"]["isError"] is False
        assert "Queued 13 characters" in res["result"]["content"][0]["text"]
        mock_spawn.assert_called_once_with("fallback text")


def test_speak_falls_back_to_spawn_say_worker_on_daemon_error_status() -> None:
    mock_client = MagicMock()
    mock_client.speak.return_value = DaemonResponse(
        status="error", action="speak", message="queue full"
    )

    with patch("mcp_server.spawn_say_worker") as mock_spawn:
        server = McpServer(client=mock_client)
        res = server.handle_line(
            _req("tools/call", {"name": "speak", "arguments": {"text": "status error fallback"}})
        )
        assert res["result"]["isError"] is False
        assert "Queued 21 characters" in res["result"]["content"][0]["text"]
        mock_spawn.assert_called_once_with("status error fallback")


# =========================================================================
# Group 3: autoplay_turn
# =========================================================================

def test_autoplay_turn_happy_path() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-ap-"))
    transcript = tmp / "session.jsonl"
    transcript.write_text("dummy\n", encoding="utf-8")

    turn_text = "Here is the comprehensive report for the build system."
    mock_selector = MagicMock()
    mock_selector.select.return_value = AssistantMessage(
        turn_index=1,
        ordinal_from_end=1,
        timestamp="2026-10-06T12:00:00Z",
        text=turn_text,
    )

    mock_client = MagicMock()
    mock_client.speak.return_value = DaemonResponse(status="ok", action="speak")

    server = McpServer(
        client=mock_client,
        selector=mock_selector,
        home=tmp,
    )

    res = _call_tool(
        server,
        "autoplay_turn",
        {"transcript_path": str(transcript), "mode": "raw"},
    )
    assert res["result"]["isError"] is False
    assert "Queued autoplay turn" in res["result"]["content"][0]["text"]

    mock_client.speak.assert_called_once()
    args, kwargs = mock_client.speak.call_args
    assert args[0] == turn_text
    assert kwargs.get("priority") == Priority.AUTOPLAY
    assert len(kwargs.get("source_hash")) == 64


def test_autoplay_turn_honors_global_mute() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-ap-mute-"))
    claude_dir = tmp / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "auto-speech.disabled").touch()

    mock_client = MagicMock()
    server = McpServer(
        client=mock_client,
        gate=AutoplayGate(home=tmp),
        home=tmp,
    )

    res = _call_tool(server, "autoplay_turn", {})
    assert res["result"]["isError"] is True
    assert "globally muted" in res["result"]["content"][0]["text"]
    assert mock_client.speak.call_count == 0


def test_autoplay_turn_missing_transcript_returns_tool_error() -> None:
    server, _ = _server()
    res = _call_tool(
        server,
        "autoplay_turn",
        {"transcript_path": "/tmp/nonexistent_transcript_12345.jsonl"},
    )
    assert res["result"]["isError"] is True
    assert "transcript not found" in res["result"]["content"][0]["text"]


def test_autoplay_turn_no_qualifying_turn_returns_tool_error() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-ap-noturn-"))
    transcript = tmp / "session.jsonl"
    transcript.write_text("dummy\n", encoding="utf-8")

    mock_selector = MagicMock()
    mock_selector.select.side_effect = NoSuchAssistantTurn("no turn")

    server = McpServer(selector=mock_selector, home=tmp)
    res = _call_tool(server, "autoplay_turn", {"transcript_path": str(transcript)})
    assert res["result"]["isError"] is True
    assert "no qualifying assistant turn found" in res["result"]["content"][0]["text"]


def test_autoplay_turn_text_below_min_len_returns_tool_error() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-ap-short-"))
    transcript = tmp / "session.jsonl"
    transcript.write_text("dummy\n", encoding="utf-8")

    mock_selector = MagicMock()
    mock_selector.select.return_value = AssistantMessage(
        turn_index=1,
        ordinal_from_end=1,
        timestamp="2026-10-06T12:00:00Z",
        text="Short.",
    )
    mock_client = MagicMock()

    server = McpServer(client=mock_client, selector=mock_selector, home=tmp)
    res = _call_tool(server, "autoplay_turn", {"transcript_path": str(transcript)})
    assert res["result"]["isError"] is True
    assert "too short" in res["result"]["content"][0]["text"]
    assert mock_client.speak.call_count == 0


def test_autoplay_turn_mode_raw_bypasses_rewriter() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-ap-raw-"))
    transcript = tmp / "session.jsonl"
    transcript.write_text("dummy\n", encoding="utf-8")

    turn_text = "This is a sufficiently long message exceeding twenty characters."
    mock_selector = MagicMock()
    mock_selector.select.return_value = AssistantMessage(
        turn_index=1,
        ordinal_from_end=1,
        timestamp="2026-10-06T12:00:00Z",
        text=turn_text,
    )

    mock_rewriter = MagicMock()
    mock_rewriter.rewrite.side_effect = AssertionError("Rewriter should not be called in raw mode")

    mock_client = MagicMock()
    mock_client.speak.return_value = DaemonResponse(status="ok", action="speak")

    server = McpServer(
        client=mock_client,
        selector=mock_selector,
        rewriter=mock_rewriter,
        home=tmp,
    )

    res = _call_tool(
        server,
        "autoplay_turn",
        {"transcript_path": str(transcript), "mode": "raw"},
    )
    assert res["result"]["isError"] is False
    mock_client.speak.assert_called_once()
    assert mock_client.speak.call_args[0][0] == turn_text


def test_autoplay_turn_bypass_llm_flag_bypasses_rewriter() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-ap-bypass-"))
    transcript = tmp / "session.jsonl"
    transcript.write_text("dummy\n", encoding="utf-8")

    turn_text = "This is a sufficiently long message exceeding twenty characters."
    mock_selector = MagicMock()
    mock_selector.select.return_value = AssistantMessage(
        turn_index=1,
        ordinal_from_end=1,
        timestamp="2026-10-06T12:00:00Z",
        text=turn_text,
    )

    mock_rewriter = MagicMock()
    mock_rewriter.rewrite.side_effect = AssertionError("Rewriter should not be called when bypass_llm=True")

    mock_client = MagicMock()
    mock_client.speak.return_value = DaemonResponse(status="ok", action="speak")

    server = McpServer(
        client=mock_client,
        selector=mock_selector,
        rewriter=mock_rewriter,
        home=tmp,
    )

    res = _call_tool(
        server,
        "autoplay_turn",
        {"transcript_path": str(transcript), "bypass_llm": True},
    )
    assert res["result"]["isError"] is False
    assert mock_client.speak.call_args[0][0] == turn_text


def test_autoplay_turn_rewriter_fallback_on_failure() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-ap-rwfail-"))
    transcript = tmp / "session.jsonl"
    transcript.write_text("dummy\n", encoding="utf-8")

    turn_text = "This is a sufficiently long message exceeding twenty characters."
    mock_selector = MagicMock()
    mock_selector.select.return_value = AssistantMessage(
        turn_index=1,
        ordinal_from_end=1,
        timestamp="2026-10-06T12:00:00Z",
        text=turn_text,
    )

    mock_rewriter = MagicMock()
    mock_rewriter.rewrite.side_effect = ClaudeCliRewriteError("timeout")

    mock_client = MagicMock()
    mock_client.speak.return_value = DaemonResponse(status="ok", action="speak")

    server = McpServer(
        client=mock_client,
        selector=mock_selector,
        rewriter=mock_rewriter,
        home=tmp,
    )

    res = _call_tool(
        server,
        "autoplay_turn",
        {"transcript_path": str(transcript), "mode": "summary"},
    )
    assert res["result"]["isError"] is False
    assert mock_client.speak.call_args[0][0] == turn_text


def test_autoplay_turn_connection_error_falls_back_cleanly() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-ap-connerr-"))
    transcript = tmp / "session.jsonl"
    transcript.write_text("dummy\n", encoding="utf-8")

    turn_text = "This is a sufficiently long message exceeding twenty characters."
    mock_selector = MagicMock()
    mock_selector.select.return_value = AssistantMessage(
        turn_index=1,
        ordinal_from_end=1,
        timestamp="2026-10-06T12:00:00Z",
        text=turn_text,
    )

    mock_client = MagicMock()
    mock_client.speak.side_effect = ConnectionError("daemon offline")

    server = McpServer(
        client=mock_client,
        selector=mock_selector,
        home=tmp,
    )

    with patch("mcp_server.spawn_say_worker") as mock_spawn:
        res = _call_tool(
            server,
            "autoplay_turn",
            {"transcript_path": str(transcript), "mode": "raw"},
        )
        assert res["result"]["isError"] is False
        mock_spawn.assert_called_once_with(turn_text)


def test_autoplay_turn_nonexistent_session_id_returns_tool_error() -> None:
    mock_locator = MagicMock()
    mock_locator.locate.return_value = Path("/tmp/unrelated-newest-session.jsonl")
    mock_client = MagicMock()

    server = McpServer(client=mock_client, locator=mock_locator)
    res = _call_tool(
        server,
        "autoplay_turn",
        {"session_id": "requested-session-xyz"},
    )
    assert res["result"]["isError"] is True
    assert (
        "session transcript not found for session_id: requested-session-xyz"
        in res["result"]["content"][0]["text"]
    )
    assert mock_client.speak.call_count == 0


def test_autoplay_turn_matching_session_id_succeeds() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-ap-match-"))
    transcript = tmp / "target-sess.jsonl"
    transcript.write_text("dummy\n", encoding="utf-8")

    turn_text = "This is a sufficiently long message exceeding twenty characters."
    mock_selector = MagicMock()
    mock_selector.select.return_value = AssistantMessage(
        turn_index=1,
        ordinal_from_end=1,
        timestamp="2026-10-06T12:00:00Z",
        text=turn_text,
    )
    mock_client = MagicMock()
    mock_client.speak.return_value = DaemonResponse(status="ok", action="speak")
    mock_locator = MagicMock()
    mock_locator.locate.return_value = transcript

    server = McpServer(
        client=mock_client,
        selector=mock_selector,
        locator=mock_locator,
        home=tmp,
    )
    res = _call_tool(
        server,
        "autoplay_turn",
        {"session_id": "target-sess", "mode": "raw"},
    )
    assert res["result"]["isError"] is False
    assert "Queued autoplay turn" in res["result"]["content"][0]["text"]
    mock_client.speak.assert_called_once()


def test_autoplay_turn_invalid_session_id_validation() -> None:
    server, _ = _server()
    res = _call_tool(server, "autoplay_turn", {"session_id": 12345})
    assert res["result"]["isError"] is True
    assert "session_id must be a string" in res["result"]["content"][0]["text"]

    res = _call_tool(server, "autoplay_turn", {"session_id": ""})
    assert res["result"]["isError"] is True
    assert "session_id must be a non-empty string" in res["result"]["content"][0]["text"]

    res = _call_tool(server, "autoplay_turn", {"session_id": "   "})
    assert res["result"]["isError"] is True
    assert "session_id must be a non-empty string" in res["result"]["content"][0]["text"]


def test_autoplay_turn_nonexistent_session_id_with_real_locator_returns_tool_error() -> None:
    tmp_home = Path(tempfile.mkdtemp(prefix="auto-speech-test-ap-realloc-"))
    projects_dir = tmp_home / ".claude" / "projects"
    cwd = Path("/Users/me/test-proj")
    slug = str(cwd).replace("/", "-")
    slug_dir = projects_dir / slug
    slug_dir.mkdir(parents=True, exist_ok=True)
    real_transcript = slug_dir / "real-session-abc.jsonl"
    real_transcript.write_text("dummy\n", encoding="utf-8")

    real_locator = TranscriptLocator()
    real_locator.PROJECTS_ROOT = projects_dir

    mock_client = MagicMock()
    server = McpServer(client=mock_client, locator=real_locator, home=tmp_home)
    with patch("pathlib.Path.cwd", return_value=cwd):
        res = _call_tool(
            server,
            "autoplay_turn",
            {"session_id": "nonexistent-xyz"},
        )
        assert res["result"]["isError"] is True
        assert (
            "session transcript not found for session_id: nonexistent-xyz"
            in res["result"]["content"][0]["text"]
        )
        assert mock_client.speak.call_count == 0


# =========================================================================
# Group 4: narrate_event
# =========================================================================

def test_narrate_event_missing_event_returns_parameter_error() -> None:
    server, _ = _server()
    for bad_args in ({}, {"event": ""}, {"event": "   "}):
        res = _call_tool(server, "narrate_event", bad_args)
        assert res["result"]["isError"] is True


def test_narrate_event_honors_global_mute() -> None:
    server, _ = _server(muted=True)
    res = _call_tool(server, "narrate_event", {"event": "PreToolUse"})
    assert res["result"]["isError"] is True
    assert "globally muted" in res["result"]["content"][0]["text"]


def test_narrate_event_unenrolled_session_returns_clean_notice() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-narr-unenroll-"))
    events_log = tmp / "events.jsonl"
    server, _ = _server(events_log=events_log)

    res = _call_tool(
        server,
        "narrate_event",
        {"event": "PreToolUse", "session_id": "unenrolled-session-999"},
    )
    assert res["result"]["isError"] is False
    assert "not enrolled" in res["result"]["content"][0]["text"]
    assert not events_log.exists()


def test_narrate_event_enrolled_session_appends_jsonl_and_ensures_daemon() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-narr-enroll-"))
    claude_dir = tmp / ".claude"
    sessions_dir = claude_dir / "auto-speech-narrate-sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "sess-123").touch()

    events_log = tmp / "events.jsonl"
    mock_client = MagicMock()

    server = McpServer(
        client=mock_client,
        home=tmp,
        events_log=events_log,
        narrator_sessions_dir=sessions_dir,
    )

    res = _call_tool(
        server,
        "narrate_event",
        {
            "event": "PreToolUse",
            "session_id": "sess-123",
            "payload": {"tool_name": "Bash"},
        },
    )
    assert res["result"]["isError"] is False
    assert "ingested for session 'sess-123'" in res["result"]["content"][0]["text"]
    assert mock_client.ensure_daemon_running.called

    assert events_log.is_file()
    lines = events_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "PreToolUse"
    assert record["session_id"] == "sess-123"
    assert record["payload"] == {"tool_name": "Bash"}
    assert "ts" in record
    assert "pid" in record


def test_narrate_event_antigravity_conversation_id_resolution() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-narr-ag-"))
    sessions_dir = tmp / ".claude" / "auto-speech-narrate-sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "ag-conv-99").touch()

    events_log = tmp / "events.jsonl"
    server = McpServer(
        home=tmp,
        events_log=events_log,
        narrator_sessions_dir=sessions_dir,
    )

    res = _call_tool(
        server,
        "narrate_event",
        {
            "event": "Stop",
            "payload": {"conversationId": "ag-conv-99"},
        },
    )
    assert res["result"]["isError"] is False
    assert "ingested for session 'ag-conv-99'" in res["result"]["content"][0]["text"]
    record = json.loads(events_log.read_text(encoding="utf-8").strip())
    assert record["session_id"] == "ag-conv-99"


def test_narrate_event_default_cwd_fallback() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-narr-cwd-"))
    sessions_dir = tmp / ".claude" / "auto-speech-narrate-sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "sess-cwd").touch()

    events_log = tmp / "events.jsonl"
    server = McpServer(
        home=tmp,
        events_log=events_log,
        narrator_sessions_dir=sessions_dir,
    )

    res = _call_tool(
        server,
        "narrate_event",
        {"event": "UserPromptSubmit", "session_id": "sess-cwd"},
    )
    assert res["result"]["isError"] is False
    record = json.loads(events_log.read_text(encoding="utf-8").strip())
    assert record["cwd"] != ""


# =========================================================================
# Group 5: playback_control
# =========================================================================

def test_playback_control_invalid_action_returns_error() -> None:
    server, _ = _server()
    res = _call_tool(server, "playback_control", {"action": "jump"})
    assert res["result"]["isError"] is True
    assert "unknown action" in res["result"]["content"][0]["text"]


def test_playback_control_stop_invokes_interrupt() -> None:
    mock_client = MagicMock()
    server, _ = _server(client=mock_client)

    res = _call_tool(server, "playback_control", {"action": "stop"})
    assert res["result"]["isError"] is False
    assert "Playback stopped and queue cleared." in res["result"]["content"][0]["text"]
    assert mock_client.interrupt.called


def test_playback_control_pause_resume_restart_seek_when_socket_missing() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-ctrl-nosock-"))
    server = McpServer(home=tmp, is_mpv_alive=lambda: False)

    for act in ("pause", "resume", "restart"):
        res = _call_tool(server, "playback_control", {"action": act})
        assert res["result"]["isError"] is True
        assert "No active playback session." in res["result"]["content"][0]["text"]

    res_seek = _call_tool(server, "playback_control", {"action": "seek", "target": "+10"})
    assert res_seek["result"]["isError"] is True
    assert "No active playback session." in res_seek["result"]["content"][0]["text"]


def test_playback_control_pause_resume_restart_when_socket_alive() -> None:
    sent_cmds: list[list] = []

    def mock_sender(cmd, _sock_path):
        sent_cmds.append(cmd)
        return {"error": "success"}

    server = McpServer(
        mpv_ipc_sender=mock_sender,
        is_mpv_alive=lambda: True,
    )

    res = _call_tool(server, "playback_control", {"action": "pause"})
    assert res["result"]["isError"] is False
    assert "Playback paused." in res["result"]["content"][0]["text"]
    assert sent_cmds[-1] == ["set_property", "pause", True]

    res = _call_tool(server, "playback_control", {"action": "resume"})
    assert res["result"]["isError"] is False
    assert "Playback resumed." in res["result"]["content"][0]["text"]
    assert sent_cmds[-1] == ["set_property", "pause", False]

    res = _call_tool(server, "playback_control", {"action": "restart"})
    assert res["result"]["isError"] is False
    assert "Playback restarted." in res["result"]["content"][0]["text"]
    assert sent_cmds[-1] == ["seek", 0, "absolute"]


def test_playback_control_resume_honors_global_mute() -> None:
    server, _ = _server(muted=True, is_mpv_alive=lambda: True)
    res = _call_tool(server, "playback_control", {"action": "resume"})
    assert res["result"]["isError"] is True
    assert "globally muted" in res["result"]["content"][0]["text"]


def test_playback_control_seek_targets() -> None:
    sent_cmds: list[list] = []

    def mock_sender(cmd, _sock_path):
        sent_cmds.append(cmd)
        return {"error": "success"}

    server = McpServer(
        mpv_ipc_sender=mock_sender,
        is_mpv_alive=lambda: True,
    )

    test_cases = [
        ("+10", ["seek", 10.0, "relative"]),
        ("-5", ["seek", -5.0, "relative"]),
        ("60", ["seek", 60.0, "absolute"]),
        ("end", ["seek", 999999, "absolute"]),
    ]

    for target_str, expected_cmd in test_cases:
        res = _call_tool(server, "playback_control", {"action": "seek", "target": target_str})
        assert res["result"]["isError"] is False
        assert sent_cmds[-1] == expected_cmd


def test_playback_control_seek_invalid_target_returns_error() -> None:
    server = McpServer(is_mpv_alive=lambda: True)
    for bad_target in ("", "   ", "abc", "+inf", "-inf"):
        res = _call_tool(server, "playback_control", {"action": "seek", "target": bad_target})
        assert res["result"]["isError"] is True
        assert "seek requires a target" in res["result"]["content"][0]["text"]


def test_playback_control_replay_cached_audio() -> None:
    mock_store = MagicMock()
    entry = CacheEntry(
        source_hash="abcd" * 16,
        voice_id="system",
        speed=1.0,
        char_count=40,
        duration_seconds=2.5,
        created_at="now",
        chars_per_second_at_creation=16.0,
    )
    mock_store.list_by_recency.return_value = [(Path("/tmp/audio.wav"), entry)]

    mock_client = MagicMock()
    mock_client.play_cache.return_value = DaemonResponse(status="ok", action="play_cache")

    server = McpServer(client=mock_client, cache_store=mock_store)

    res = _call_tool(server, "playback_control", {"action": "replay", "ordinal": 1})
    assert res["result"]["isError"] is False
    assert "Replaying cached audio" in res["result"]["content"][0]["text"]
    mock_client.play_cache.assert_called_once_with("abcd" * 16, session_id=None)


def test_playback_control_replay_empty_cache_and_bounds() -> None:
    mock_store = MagicMock()
    mock_store.list_by_recency.return_value = []

    server = McpServer(cache_store=mock_store)
    res = _call_tool(server, "playback_control", {"action": "replay", "ordinal": 1})
    assert res["result"]["isError"] is True
    assert "No cached audio available" in res["result"]["content"][0]["text"]

    entry = CacheEntry(
        source_hash="abcd" * 16,
        voice_id="system",
        speed=1.0,
        char_count=40,
        duration_seconds=2.5,
        created_at="now",
        chars_per_second_at_creation=16.0,
    )
    mock_store.list_by_recency.return_value = [(Path("/tmp/audio.wav"), entry)]
    res = _call_tool(server, "playback_control", {"action": "replay", "ordinal": 5})
    assert res["result"]["isError"] is True
    assert "No cached audio available" in res["result"]["content"][0]["text"]


def test_playback_control_replay_honors_global_mute() -> None:
    server, _ = _server(muted=True)
    res = _call_tool(server, "playback_control", {"action": "replay"})
    assert res["result"]["isError"] is True
    assert "globally muted" in res["result"]["content"][0]["text"]


# =========================================================================
# Group 6: session_configure
# =========================================================================

def test_session_configure_autoplay_on_and_off() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-cfg-ap-"))
    server = McpServer(home=tmp)

    res = _call_tool(
        server,
        "session_configure",
        {"autoplay": "on", "session_id": "sess-1"},
    )
    assert res["result"]["isError"] is False
    assert (tmp / ".claude" / "auto-speech-autoplay-enabled" / "sess-1").exists()

    res = _call_tool(
        server,
        "session_configure",
        {"autoplay": "off", "session_id": "sess-1"},
    )
    assert res["result"]["isError"] is False
    assert not (tmp / ".claude" / "auto-speech-autoplay-enabled" / "sess-1").exists()


def test_session_configure_autoplay_off_without_session_sets_global_mute() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-cfg-mute-"))
    server = McpServer(home=tmp)

    res = _call_tool(server, "session_configure", {"autoplay": "off"})
    assert res["result"]["isError"] is False
    assert (tmp / ".claude" / "auto-speech.disabled").exists()


def test_session_configure_narrate_on_and_off() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-cfg-narr-"))
    mock_client = MagicMock()
    server = McpServer(client=mock_client, home=tmp)

    res = _call_tool(
        server,
        "session_configure",
        {"narrate": "on", "session_id": "sess-2"},
    )
    assert res["result"]["isError"] is False
    assert (tmp / ".claude" / "auto-speech-narrate-sessions" / "sess-2").exists()
    assert mock_client.ensure_daemon_running.called

    res = _call_tool(
        server,
        "session_configure",
        {"narrate": "off", "session_id": "sess-2"},
    )
    assert res["result"]["isError"] is False
    assert not (tmp / ".claude" / "auto-speech-narrate-sessions" / "sess-2").exists()


def test_session_configure_scope_solo_and_all() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-cfg-scope-"))
    server = McpServer(home=tmp)

    res = _call_tool(
        server,
        "session_configure",
        {"scope": "solo", "session_id": "sess-3"},
    )
    assert res["result"]["isError"] is False
    assert SoloScope(home=tmp).current() == "sess-3"

    res = _call_tool(server, "session_configure", {"scope": "all"})
    assert res["result"]["isError"] is False
    assert SoloScope(home=tmp).current() is None


def test_session_configure_scope_solo_without_session_returns_error() -> None:
    server, _ = _server()
    res = _call_tool(server, "session_configure", {"scope": "solo"})
    assert res["result"]["isError"] is True


def test_session_configure_mode_and_size() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-cfg-mode-"))
    server = McpServer(home=tmp)

    res = _call_tool(
        server,
        "session_configure",
        {"mode": "summary", "size": "large"},
    )
    assert res["result"]["isError"] is False
    cfg_file = tmp / ".config" / "auto-speech" / "autoplay.toml"
    assert cfg_file.is_file()
    content = cfg_file.read_text(encoding="utf-8")
    assert 'mode = "summary"' in content
    assert 'summary_size = "large"' in content


def test_session_configure_status_reports() -> None:
    server, _ = _server()
    res = _call_tool(server, "session_configure", {})
    assert res["result"]["isError"] is False
    assert "autoplay" in res["result"]["content"][0]["text"].lower()


def test_session_configure_subsystem_and_action_validation() -> None:
    server, _ = _server()

    # Invalid subsystem
    res = _call_tool(server, "session_configure", {"subsystem": "bogus"})
    assert res["result"]["isError"] is True
    assert "invalid subsystem: 'bogus'" in res["result"]["content"][0]["text"]

    res = _call_tool(server, "session_configure", {"subsystem": "bogus", "action": "on"})
    assert res["result"]["isError"] is True
    assert "invalid subsystem: 'bogus'" in res["result"]["content"][0]["text"]

    # Unhandled action without subsystem
    res = _call_tool(server, "session_configure", {"action": "bogus"})
    assert res["result"]["isError"] is True
    assert "unhandled action 'bogus' without valid subsystem" in res["result"]["content"][0]["text"]

    res = _call_tool(server, "session_configure", {"action": "on", "session_id": "sess-test"})
    assert res["result"]["isError"] is True
    assert "unhandled action 'on' without valid subsystem" in res["result"]["content"][0]["text"]

    # Invalid action under subsystem
    res = _call_tool(server, "session_configure", {"subsystem": "autoplay", "action": "bogus"})
    assert res["result"]["isError"] is True
    assert "invalid autoplay action: 'bogus'" in res["result"]["content"][0]["text"]

    res = _call_tool(server, "session_configure", {"subsystem": "narrate", "action": "bogus"})
    assert res["result"]["isError"] is True
    assert "invalid narrate action: 'bogus'" in res["result"]["content"][0]["text"]

    res = _call_tool(server, "session_configure", {"subsystem": "narrate", "action": "mode"})
    assert res["result"]["isError"] is True
    assert "invalid narrate action: 'mode'" in res["result"]["content"][0]["text"]

    # Mode / scope missing value
    res = _call_tool(server, "session_configure", {"subsystem": "autoplay", "action": "mode"})
    assert res["result"]["isError"] is True
    assert "action 'mode' requires 'value'" in res["result"]["content"][0]["text"]

    res = _call_tool(server, "session_configure", {"subsystem": "autoplay", "action": "scope"})
    assert res["result"]["isError"] is True
    assert "action 'scope' requires 'value'" in res["result"]["content"][0]["text"]

    res = _call_tool(server, "session_configure", {"action": "mode"})
    assert res["result"]["isError"] is True
    assert "action 'mode' requires 'value'" in res["result"]["content"][0]["text"]

    res = _call_tool(server, "session_configure", {"action": "scope"})
    assert res["result"]["isError"] is True
    assert "action 'scope' requires 'value'" in res["result"]["content"][0]["text"]


def test_session_configure_alternative_dispatch_routing() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-test-cfg-alt-"))
    server = McpServer(home=tmp)

    # 1. Autoplay on via alternative dispatch
    res = _call_tool(
        server,
        "session_configure",
        {"subsystem": "autoplay", "action": "on", "session_id": "sess-alt-1"},
    )
    assert res["result"]["isError"] is False
    assert (tmp / ".claude" / "auto-speech-autoplay-enabled" / "sess-alt-1").exists()

    # 2. Autoplay mode via alternative dispatch (mode="raw")
    res = _call_tool(
        server,
        "session_configure",
        {"subsystem": "autoplay", "action": "mode", "value": "raw"},
    )
    assert res["result"]["isError"] is False
    cfg_file = tmp / ".config" / "auto-speech" / "autoplay.toml"
    assert cfg_file.is_file()
    assert 'mode = "raw"' in cfg_file.read_text(encoding="utf-8")

    # 3. Autoplay scope via alternative dispatch (scope="solo")
    res = _call_tool(
        server,
        "session_configure",
        {"subsystem": "autoplay", "action": "scope", "value": "solo", "session_id": "sess-alt-1"},
    )
    assert res["result"]["isError"] is False
    assert SoloScope(home=tmp).current() == "sess-alt-1"

    # 4. Autoplay scope via alternative dispatch (scope="all")
    res = _call_tool(
        server,
        "session_configure",
        {"subsystem": "autoplay", "action": "scope", "value": "all"},
    )
    assert res["result"]["isError"] is False
    assert SoloScope(home=tmp).current() is None

    # 5. Narrate on via alternative dispatch
    res = _call_tool(
        server,
        "session_configure",
        {"subsystem": "narrate", "action": "on", "session_id": "sess-alt-2"},
    )
    assert res["result"]["isError"] is False
    assert (tmp / ".claude" / "auto-speech-narrate-sessions" / "sess-alt-2").exists()

    # 6. Standalone action mode and scope
    res = _call_tool(
        server,
        "session_configure",
        {"action": "mode", "value": "verbatim"},
    )
    assert res["result"]["isError"] is False
    assert 'mode = "verbatim"' in cfg_file.read_text(encoding="utf-8")

    res = _call_tool(
        server,
        "session_configure",
        {"action": "scope", "value": "solo", "session_id": "sess-alt-3"},
    )
    assert res["result"]["isError"] is False
    assert SoloScope(home=tmp).current() == "sess-alt-3"


# =========================================================================
# Group 7: doctor
# =========================================================================

def test_doctor_text_report_healthy() -> None:
    mock_doctor = MagicMock()
    mock_doctor.run.return_value = HealthReport(
        checks=[Check("binaries", Status.OK, "mpv available")]
    )
    server = McpServer(doctor=mock_doctor)

    res = _call_tool(server, "doctor", {"as_json": False})
    assert res["result"]["isError"] is False
    assert "auto-speech doctor — HEALTHY" in res["result"]["content"][0]["text"]


def test_doctor_json_report_healthy() -> None:
    mock_doctor = MagicMock()
    mock_doctor.run.return_value = HealthReport(
        checks=[Check("binaries", Status.OK, "mpv available")]
    )
    server = McpServer(doctor=mock_doctor)

    res = _call_tool(server, "doctor", {"as_json": True})
    assert res["result"]["isError"] is False
    data = json.loads(res["result"]["content"][0]["text"])
    assert data["healthy"] is True
    assert isinstance(data["checks"], list)


def test_doctor_unhealthy_sets_is_error_true() -> None:
    mock_doctor = MagicMock()
    mock_doctor.run.return_value = HealthReport(
        checks=[Check("disk", Status.FAIL, "disk full")]
    )
    server = McpServer(doctor=mock_doctor)

    res_text = _call_tool(server, "doctor", {"as_json": False})
    assert res_text["result"]["isError"] is True
    assert "UNHEALTHY" in res_text["result"]["content"][0]["text"]

    res_json = _call_tool(server, "doctor", {"as_json": True})
    assert res_json["result"]["isError"] is True
    data = json.loads(res_json["result"]["content"][0]["text"])
    assert data["healthy"] is False


# Group 8: sound_yield


class _FakeYieldGate:
    def __init__(self, assessment):
        self._a = assessment

    def assess(self):
        return self._a


def _yield_server():
    from sound_yield import SoundYieldStore

    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-mcp-yield-"))
    store = SoundYieldStore(tmp / "sound-yield.toml")
    srv, _ = _server(sound_yield_store=store)
    return srv, store, tmp


def test_sound_yield_requires_a_known_action() -> None:
    srv, _, _ = _yield_server()
    res = _call_tool(srv, "sound_yield", {})
    assert res["result"]["isError"] is True
    assert "invalid action" in res["result"]["content"][0]["text"]
    res = _call_tool(srv, "sound_yield", {"action": "mute"})
    assert res["result"]["isError"] is True
    assert "status, on, off, add, remove, clear, check" in res["result"]["content"][0]["text"]
    res = _call_tool(srv, "sound_yield", {"action": "add", "apps": [1, 2]})
    assert res["result"]["isError"] is True
    assert "list of strings" in res["result"]["content"][0]["text"]


def test_sound_yield_status_defaults_without_a_config_file() -> None:
    srv, _, _ = _yield_server()
    res = _call_tool(srv, "sound_yield", {"action": "status"})
    text = res["result"]["content"][0]["text"]
    assert res["result"]["isError"] is False
    assert "sound yield: ON" in text
    assert "exceptions (apps whose sound does not hold speech): none" in text
    assert "longest hold: 180 s, then set aside as missed (auto-speech missed)" in text
    assert "defaults (no config file yet)" in text


def test_sound_yield_on_off_add_remove_clear_write_the_shared_config() -> None:
    srv, store, tmp = _yield_server()
    res = _call_tool(srv, "sound_yield", {"action": "off"})
    assert "sound yield: OFF" in res["result"]["content"][0]["text"]
    assert store.load().enabled is False

    res = _call_tool(srv, "sound_yield", {"action": "add", "apps": ["Music", "com.spotify.client", "music"]})
    text = res["result"]["content"][0]["text"]
    assert text.startswith("added: Music, com.spotify.client")
    assert store.load().exceptions == ("Music", "com.spotify.client")
    cfg = (tmp / "sound-yield.toml").read_text()
    assert 'exceptions = ["Music", "com.spotify.client"]' in cfg
    assert "enabled = false" in cfg

    res = _call_tool(srv, "sound_yield", {"action": "add"})
    assert res["result"]["isError"] is True
    assert "name at least one app" in res["result"]["content"][0]["text"]

    res = _call_tool(srv, "sound_yield", {"action": "remove", "apps": "MUSIC"})
    assert res["result"]["content"][0]["text"].startswith("removed: Music")
    assert store.load().exceptions == ("com.spotify.client",)

    res = _call_tool(srv, "sound_yield", {"action": "clear"})
    assert res["result"]["content"][0]["text"].startswith("exceptions cleared")
    assert store.load().exceptions == ()

    res = _call_tool(srv, "sound_yield", {"action": "on"})
    assert "sound yield: ON" in res["result"]["content"][0]["text"]
    assert store.load().enabled is True


def test_sound_yield_player_verbs_and_hold_for_write_the_shared_config() -> None:
    srv, store, _ = _yield_server()

    def text(res):
        return res["result"]["content"][0]["text"]

    res = _call_tool(srv, "sound_yield", {"action": "status"})
    assert "holds for: players only" in text(res)
    assert "players (apps whose sound holds speech): com.apple.Music" in text(res)
    res = _call_tool(srv, "sound_yield", {"action": "player-add", "apps": ["com.google.antigravity"]})
    assert text(res).startswith("players added: com.google.antigravity")
    assert "com.google.antigravity" in store.load().players
    res = _call_tool(srv, "sound_yield", {"action": "player-remove", "apps": "Chrome"})
    assert text(res).startswith("players removed: com.google.Chrome")
    assert "com.google.Chrome" not in store.load().players
    res = _call_tool(srv, "sound_yield", {"action": "player-reset"})
    assert text(res).startswith("players reset to the defaults")
    assert "com.google.Chrome" in store.load().players
    res = _call_tool(srv, "sound_yield", {"action": "hold-for", "apps": ["any"]})
    assert res["result"]["isError"] is False
    assert store.load().hold_for == "any"
    assert "holds for: any app" in text(res)
    res = _call_tool(srv, "sound_yield", {"action": "hold-for", "apps": ["sometimes"]})
    assert res["result"]["isError"] is True
    res = _call_tool(srv, "sound_yield", {"action": "player-add"})
    assert res["result"]["isError"] is True


def test_sound_yield_meeting_is_his_own_switch_with_the_cli_words() -> None:
    from sound_yield import MeetingFlag

    srv, store, tmp = _yield_server()
    flag_path = tmp / "meeting.json"
    old = os.environ.get("AUTO_SPEECH_MEETING_FILE")
    os.environ["AUTO_SPEECH_MEETING_FILE"] = str(flag_path)
    try:
        res = _call_tool(srv, "sound_yield", {"action": "meeting"})
        assert res["result"]["content"][0]["text"] == "meeting mode: off"
        res = _call_tool(srv, "sound_yield", {"action": "meeting", "apps": ["for", "45m"]})
        text = res["result"]["content"][0]["text"]
        assert res["result"]["isError"] is False
        assert "meeting mode: on until" in text and "45 min left" in text
        assert MeetingFlag(flag_path).read() is not None
        res = _call_tool(srv, "sound_yield", {"action": "meeting", "apps": ["for", "9h"]})
        assert res["result"]["isError"] is True
        res = _call_tool(srv, "sound_yield", {"action": "meeting", "apps": ["off"]})
        assert res["result"]["content"][0]["text"] == "meeting mode: off (was on)"
        assert not flag_path.exists()
    finally:
        if old is None:
            os.environ.pop("AUTO_SPEECH_MEETING_FILE", None)
        else:
            os.environ["AUTO_SPEECH_MEETING_FILE"] = old


def test_sound_yield_check_reports_the_live_assessment() -> None:
    from sound_yield import MODE_PROCESS_LIST, Assessment, AudioProcess, SoundYieldSettings, SoundYieldStore

    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-mcp-yield-"))
    store = SoundYieldStore(tmp / "sound-yield.toml")
    music = AudioProcess(pid=501, bundle_id="com.apple.Music", name="Music")
    gate = _FakeYieldGate(
        Assessment(hold=True, mode=MODE_PROCESS_LIST, settings=SoundYieldSettings(), blockers=[music])
    )
    srv, _ = _server(sound_yield_store=store, sound_yield_gate=gate)
    res = _call_tool(srv, "sound_yield", {"action": "check"})
    text = res["result"]["content"][0]["text"]
    assert res["result"]["isError"] is False
    assert "playing: Music (com.apple.Music) (pid 501) — holds speech" in text
    assert "speech would HOLD (up to 180 s, then set aside as missed)" in text


# Group 9: missed


def _missed_server(client=None):
    from missed_speech import MissedList

    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-mcp-missed-"))
    missed = MissedList(tmp / "missed.json")
    srv, _ = _server(missed_list=missed, client=client)
    return srv, missed


def _seed_missed(missed) -> None:
    from missed_speech import REASON_LONGEST_WAIT, MissedLine

    missed.miss(
        MissedLine(id="aa11", text="The build is green.", queued_at=1_700_000_000.0, voice_id="af_bella", speed=1.1),
        reason=REASON_LONGEST_WAIT,
        waited_seconds=180.0,
        held_for=("ChatGPT (pid 9)",),
    )
    missed.hold(MissedLine(id="bb22", text="Second line.", queued_at=1_700_000_100.0), ("ChatGPT (pid 9)",))


def test_missed_requires_a_known_action() -> None:
    srv, _ = _missed_server()
    res = _call_tool(srv, "missed", {"action": "drop"})
    assert res["result"]["isError"] is True
    assert "list, play, clear" in res["result"]["content"][0]["text"]


def test_missed_list_is_the_default_and_matches_the_cli_text() -> None:
    srv, missed = _missed_server()
    res = _call_tool(srv, "missed", {})
    assert res["result"]["isError"] is False
    assert res["result"]["content"][0]["text"] == "missed speech: nothing missed, nothing held"
    _seed_missed(missed)
    text = _call_tool(srv, "missed", {"action": "list"})["result"]["content"][0]["text"]
    assert "missed speech: 2 lines you have not heard (1 held, 1 missed), oldest first" in text
    assert text.index("The build is green.") < text.index("Second line.")
    assert "missed: longest wait (180 s) while ChatGPT (pid 9) played" in text
    assert "replay the missed lines, oldest first: auto-speech missed --play" in text


def test_missed_play_sends_each_missed_line_to_the_daemon_without_a_hold() -> None:
    client = MagicMock()
    srv, missed = _missed_server(client=client)
    _seed_missed(missed)
    res = _call_tool(srv, "missed", {"action": "play"})
    text = res["result"]["content"][0]["text"]
    assert res["result"]["isError"] is False
    assert "missed speech: replaying 1 line, oldest first, over any other sound" in text
    client.speak.assert_called_once()
    args, kwargs = client.speak.call_args
    assert args[0] == "The build is green."
    assert kwargs["missed_id"] == "aa11"
    assert kwargs["hold"] is False
    assert kwargs["priority"] == Priority.EXPLICIT_MCP
    assert [m.id for m in missed.load()] == ["aa11", "bb22"]  # the daemon clears it once played


def test_missed_play_reports_an_unreachable_daemon() -> None:
    client = MagicMock()
    client.speak.side_effect = ConnectionError("no socket")
    srv, missed = _missed_server(client=client)
    _seed_missed(missed)
    res = _call_tool(srv, "missed", {"action": "play"})
    assert res["result"]["isError"] is True
    assert "could not reach the auto-speech daemon" in res["result"]["content"][0]["text"]


def test_missed_clear_forgets_everything() -> None:
    srv, missed = _missed_server()
    _seed_missed(missed)
    res = _call_tool(srv, "missed", {"action": "clear"})
    assert res["result"]["content"][0]["text"] == "missed speech: cleared 2 lines"
    assert missed.load() == []


# Group 10: speaker


def _speaker_server(**kwargs):
    from speaker import DeskNames, SpeakerStore

    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-mcp-speaker-"))
    agents = tmp / "Agents"
    agents.mkdir()
    (agents / "Desk.md").write_text(
        '---\nname: "Master Desk"\nstate: working\nsession: sess-master\nsection: "[[Desk/AspenESS|AspenESS]]"\n---\n',
        encoding="utf-8",
    )
    store = SpeakerStore(tmp / "speaker.toml")
    srv, _ = _server(speaker_store=store, speaker_names=DeskNames(agents), **kwargs)
    return srv, store


def test_speaker_requires_a_known_action() -> None:
    srv, _ = _speaker_server()
    res = _call_tool(srv, "speaker", {"action": "shout"})
    assert res["result"]["isError"] is True
    assert "status, on, off, gap, name, forget, who" in res["result"]["content"][0]["text"]


def test_speaker_status_is_the_default_and_matches_the_cli_text() -> None:
    srv, _ = _speaker_server()
    text = _call_tool(srv, "speaker", {})["result"]["content"][0]["text"]
    assert text.startswith("speaker names: on\nquiet gap: 15 min")
    assert "desks known: 1" in text


def test_speaker_off_gap_name_forget_and_who() -> None:
    srv, store = _speaker_server()
    assert "speaker names: off" in _call_tool(srv, "speaker", {"action": "off"})["result"]["content"][0]["text"]
    assert store.load().enabled is False
    res = _call_tool(srv, "speaker", {"action": "gap", "seconds": 120})
    assert res["result"]["isError"] is False and "quiet gap: 2 min" in res["result"]["content"][0]["text"]
    assert store.load().quiet_gap_seconds == 120.0
    assert _call_tool(srv, "speaker", {"action": "gap"})["result"]["isError"] is True
    assert _call_tool(srv, "speaker", {"action": "gap", "seconds": "soon"})["result"]["isError"] is True
    res = _call_tool(srv, "speaker", {"action": "name", "session_id": "sess-x", "name": "Side Desk"})
    assert res["result"]["isError"] is False
    assert dict(store.load().names) == {"sess-x": "Side Desk"}
    who = _call_tool(srv, "speaker", {"action": "who", "session_id": "sess-x"})["result"]["content"][0]["text"]
    assert "Side Desk" in who
    who = _call_tool(srv, "speaker", {"action": "who", "session_id": "sess-master"})["result"]["content"][0]["text"]
    assert "Master Desk" in who
    res = _call_tool(srv, "speaker", {"action": "forget", "session_id": "sess-x"})
    assert res["result"]["isError"] is False and store.load().names == ()
    with patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "sess-master"}):
        who = _call_tool(srv, "speaker", {"action": "who"})["result"]["content"][0]["text"]
    assert "Master Desk" in who, "who defaults to the server's own session"


def test_speaker_tool_schema() -> None:
    s, _ = _server()
    tools = {t["name"]: t for t in s.handle_line(_req("tools/list"))["result"]["tools"]}
    schema = tools["speaker"]["inputSchema"]
    assert schema["properties"]["action"]["enum"] == ["status", "on", "off", "gap", "name", "forget", "who"]
    assert "required" not in schema
    assert set(schema["properties"]) == {"action", "seconds", "session_id", "name"}
    assert "session_id" in tools["speak"]["inputSchema"]["properties"]


def test_missed_tool_schema() -> None:
    s, _ = _server()
    tools = s.handle_line(_req("tools/list"))["result"]["tools"]
    tool = next(t for t in tools if t["name"] == "missed")
    assert tool["inputSchema"]["properties"]["action"]["enum"] == ["list", "play", "clear"]
    assert "required" not in tool["inputSchema"]
    assert tool["inputSchema"]["additionalProperties"] is False


def test_sound_yield_tool_schema() -> None:
    s, _ = _server()
    tools = {t["name"]: t for t in s.handle_line(_req("tools/list"))["result"]["tools"]}
    schema = tools["sound_yield"]["inputSchema"]
    assert schema["required"] == ["action"]
    assert schema["properties"]["action"]["enum"] == [
        "status", "on", "off", "add", "remove", "clear", "check",
        "hold-for", "player-add", "player-remove", "player-reset", "meeting",
    ]
    assert schema["properties"]["apps"]["items"] == {"type": "string"}
    assert schema["additionalProperties"] is False


def _history_server():
    from speech_history import HistoryStore, SpeechHistory

    tmp = Path(tempfile.mkdtemp(prefix="auto-speech-mcp-history-"))
    store = HistoryStore(tmp / "history.toml")
    history = SpeechHistory(tmp / "history", store=store)
    srv, _ = _server(history_store=store, speech_history=history)
    return srv, store, history, tmp


def _history_wav(tmp: Path) -> Path:
    import wave

    path = tmp / "src.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 12000)
    return path


def test_history_requires_a_known_action() -> None:
    srv, *_ = _history_server()
    res = _call_tool(srv, "history", {"action": "shout"})
    assert res["result"]["isError"] is True
    assert "list, status, on, off, keep, player-on, player-off" in res["result"]["content"][0]["text"]
    assert _call_tool(srv, "history", {"action": "keep", "count": "ten"})["result"]["isError"] is True
    assert _call_tool(srv, "history", {"as_json": "yes"})["result"]["isError"] is True


def test_history_list_is_the_default_and_matches_the_cli_text() -> None:
    srv, store, history, tmp = _history_server()
    assert _call_tool(srv, "history", {})["result"]["content"][0]["text"] == "speech history: nothing played yet"
    line = history.export(_history_wav(tmp), text="Hello from the desk.", sender="Master Desk")
    text = _call_tool(srv, "history", {})["result"]["content"][0]["text"]
    assert text.startswith("speech history: the last 1 of 1 line, newest first")
    assert "Hello from the desk." in text
    data = json.loads(_call_tool(srv, "history", {"action": "list", "as_json": True})["result"]["content"][0]["text"])
    assert data["lines"][0]["wav_path"] == str(line.wav)


def test_history_keep_and_player_write_the_shared_config() -> None:
    srv, store, history, tmp = _history_server()
    res = _call_tool(srv, "history", {"action": "keep", "count": 30})
    assert res["result"]["isError"] is False
    assert "keep: the last 30 lines" in res["result"]["content"][0]["text"]
    assert store.load().keep == 30
    assert _call_tool(srv, "history", {"action": "keep"})["result"]["isError"] is True
    _call_tool(srv, "history", {"action": "player-on"})
    assert store.load().player is True
    _call_tool(srv, "history", {"action": "off"})
    assert store.load().enabled is False


def test_history_tool_schema() -> None:
    srv, *_ = _history_server()
    tools = {t["name"]: t for t in srv.handle_line(_req("tools/list"))["result"]["tools"]}
    schema = tools["history"]["inputSchema"]
    assert schema["properties"]["action"]["enum"] == ["list", "status", "on", "off", "keep", "player-on", "player-off"]
    assert schema["properties"]["count"]["type"] == "integer"
    assert schema["additionalProperties"] is False


TESTS = [
    # Group 1: Protocol & Advertisement
    test_initialize_echoes_supported_protocol,
    test_initialize_unknown_protocol_falls_back_to_latest,
    test_notification_gets_no_response,
    test_ping,
    test_tools_list_advertises_all_ten_tools,
    # Group 2: Backward Compatibility of speak
    test_speak_queues_text_verbatim,
    test_speak_rejects_bad_text,
    test_speak_rejects_oversize_text,
    test_speak_honors_global_mute,
    test_speak_spawn_failure_is_tool_error,
    test_protocol_errors,
    test_response_messages_are_ignored,
    test_serve_writes_one_line_per_request,
    test_plugin_mcp_manifest_points_at_the_launcher,
    test_speak_invokes_daemon_client_directly,
    test_speak_falls_back_to_spawn_say_worker_on_connection_error,
    test_speak_falls_back_to_spawn_say_worker_on_daemon_error_status,
    # Group 3: autoplay_turn
    test_autoplay_turn_happy_path,
    test_autoplay_turn_honors_global_mute,
    test_autoplay_turn_missing_transcript_returns_tool_error,
    test_autoplay_turn_no_qualifying_turn_returns_tool_error,
    test_autoplay_turn_text_below_min_len_returns_tool_error,
    test_autoplay_turn_mode_raw_bypasses_rewriter,
    test_autoplay_turn_bypass_llm_flag_bypasses_rewriter,
    test_autoplay_turn_rewriter_fallback_on_failure,
    test_autoplay_turn_connection_error_falls_back_cleanly,
    test_autoplay_turn_nonexistent_session_id_returns_tool_error,
    test_autoplay_turn_matching_session_id_succeeds,
    test_autoplay_turn_invalid_session_id_validation,
    test_autoplay_turn_nonexistent_session_id_with_real_locator_returns_tool_error,
    # Group 4: narrate_event
    test_narrate_event_missing_event_returns_parameter_error,
    test_narrate_event_honors_global_mute,
    test_narrate_event_unenrolled_session_returns_clean_notice,
    test_narrate_event_enrolled_session_appends_jsonl_and_ensures_daemon,
    test_narrate_event_antigravity_conversation_id_resolution,
    test_narrate_event_default_cwd_fallback,
    # Group 5: playback_control
    test_playback_control_invalid_action_returns_error,
    test_playback_control_stop_invokes_interrupt,
    test_playback_control_pause_resume_restart_seek_when_socket_missing,
    test_playback_control_pause_resume_restart_when_socket_alive,
    test_playback_control_resume_honors_global_mute,
    test_playback_control_seek_targets,
    test_playback_control_seek_invalid_target_returns_error,
    test_playback_control_replay_cached_audio,
    test_playback_control_replay_empty_cache_and_bounds,
    test_playback_control_replay_honors_global_mute,
    # Group 6: session_configure
    test_session_configure_autoplay_on_and_off,
    test_session_configure_autoplay_off_without_session_sets_global_mute,
    test_session_configure_narrate_on_and_off,
    test_session_configure_scope_solo_and_all,
    test_session_configure_scope_solo_without_session_returns_error,
    test_session_configure_mode_and_size,
    test_session_configure_status_reports,
    test_session_configure_subsystem_and_action_validation,
    test_session_configure_alternative_dispatch_routing,
    # Group 7: doctor
    test_doctor_text_report_healthy,
    test_doctor_json_report_healthy,
    test_doctor_unhealthy_sets_is_error_true,
    # Group 8: sound_yield
    test_sound_yield_requires_a_known_action,
    test_sound_yield_status_defaults_without_a_config_file,
    test_sound_yield_on_off_add_remove_clear_write_the_shared_config,
    test_sound_yield_player_verbs_and_hold_for_write_the_shared_config,
        test_sound_yield_check_reports_the_live_assessment,
        test_sound_yield_meeting_is_his_own_switch_with_the_cli_words,
    test_sound_yield_tool_schema,
    test_missed_requires_a_known_action,
    test_missed_list_is_the_default_and_matches_the_cli_text,
    test_missed_play_sends_each_missed_line_to_the_daemon_without_a_hold,
    test_missed_play_reports_an_unreachable_daemon,
    test_missed_clear_forgets_everything,
    test_missed_tool_schema,
    test_speak_carries_the_session_from_the_call_or_the_environment,
    test_speaker_requires_a_known_action,
    test_speaker_status_is_the_default_and_matches_the_cli_text,
    test_speaker_off_gap_name_forget_and_who,
    test_speaker_tool_schema,
    test_history_requires_a_known_action,
    test_history_list_is_the_default_and_matches_the_cli_text,
    test_history_keep_and_player_write_the_shared_config,
    test_history_tool_schema,
]


def load_tests(
    loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None
) -> unittest.TestSuite:
    suite = unittest.TestSuite()
    for t in TESTS:
        suite.addTest(unittest.FunctionTestCase(t, description=t.__name__))
    return suite


def main() -> int:
    for t in TESTS:
        t()
        print(f"  ok  {t.__name__}")
    print(f"mcp_server: {len(TESTS)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
