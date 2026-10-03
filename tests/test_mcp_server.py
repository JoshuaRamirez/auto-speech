"""Unit tests for the auto-speech MCP server (the `speak` tool).

Drives McpServer with raw JSON-RPC lines and a stub spawner, so no worker
process, audio, or real mute marker is touched. Covers:
  - initialize negotiates a supported protocol, falls back otherwise
  - notifications get no response
  - tools/list advertises exactly `speak`
  - speak queues the text verbatim and reports success
  - speak rejects empty / non-string / oversize text as tool errors
  - speak honors the global mute
  - unknown tool, unknown method, bad params, parse error, invalid request
  - serve() writes one JSON line per request and none per notification
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

import mcp_server  # noqa: E402
from autoplay_gate import AutoplayGate  # noqa: E402
from mcp_server import McpServer, serve  # noqa: E402


def _server(muted: bool = False, spawn_error: Exception | None = None):
    home = Path(tempfile.mkdtemp(prefix="auto-speech-mcp-test-"))
    if muted:
        (home / ".claude").mkdir()
        (home / ".claude" / "auto-speech.disabled").touch()
    spoken: list[str] = []

    def spawner(text: str) -> None:
        if spawn_error is not None:
            raise spawn_error
        spoken.append(text)

    return McpServer(spawner=spawner, gate=AutoplayGate(home=home)), spoken


def _req(method: str, params=None, req_id=1) -> str:
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def _call_speak(server: McpServer, arguments) -> dict:
    return server.handle_line(_req("tools/call", {"name": "speak", "arguments": arguments}))


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


def test_tools_list_has_only_speak() -> None:
    s, _ = _server()
    tools = s.handle_line(_req("tools/list"))["result"]["tools"]
    assert [t["name"] for t in tools] == ["speak"]
    assert tools[0]["inputSchema"]["required"] == ["text"]


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


def main() -> int:
    tests = [
        test_initialize_echoes_supported_protocol,
        test_initialize_unknown_protocol_falls_back_to_latest,
        test_notification_gets_no_response,
        test_ping,
        test_tools_list_has_only_speak,
        test_speak_queues_text_verbatim,
        test_speak_rejects_bad_text,
        test_speak_rejects_oversize_text,
        test_speak_honors_global_mute,
        test_speak_spawn_failure_is_tool_error,
        test_protocol_errors,
        test_response_messages_are_ignored,
        test_serve_writes_one_line_per_request,
        test_plugin_mcp_manifest_points_at_the_launcher,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"mcp_server: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
