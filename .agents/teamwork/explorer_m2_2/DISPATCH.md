## 2026-10-06T22:05:43Z
From: 4db9af0e-498d-49a5-831a-0009fd31326e (parent)
Priority: MESSAGE_PRIORITY_HIGH

You are teamwork_preview_explorer (explorer_m2_2).
Your dedicated working directory is: /Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_2

MANDATORY FIRST STEP: Read the user request verbatim in:
/Users/joshua/Developer/auto-speech/.agents/teamwork/ORIGINAL_REQUEST.md (under header `## 2026-10-06T20:47:50Z`).
Also read:
/Users/joshua/Developer/auto-speech/PROJECT.md
and reference the architectural survey in:
/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_survey_2/analysis.md.

Note: Code changes for Milestone M2 will be applied inside the isolated git worktree at `/Users/joshua/Developer/auto-speech-worktree`.

Objective:
Formulate the concrete, detailed implementation strategy for the `playback_control`, `session_configure`, and `doctor` MCP tools in `plugin/scripts/python/mcp_server.py` for Milestone M2.

Investigate and specify:
1. `playback_control` tool schema & handler:
   - Actions: `pause`, `resume`, `stop`, `restart`, `seek`, `replay`.
   - Arguments: `action` (required string enum), `target` (string, required for seek), `ordinal` (int, default 1 for replay).
   - Logic:
     * `stop`: `DaemonClient.interrupt()` and clearing state.
     * `pause`, `resume`, `restart`: `MpvIpc` over `/tmp/auto-speech-mpv.sock`. Return error if socket not alive.
     * `seek`: target validation (+N, -N, N, end) and `MpvIpc.send()`.
     * `replay`: check global mute, query `CacheStore.list_by_recency()`, invoke `DaemonClient.play_cache()`.
2. `session_configure` tool schema & handler:
   - Parameters: `session_id`, `autoplay` (on, off, status), `narrate` (on, off, status), `mode` (summary, verbatim, raw), `size` (small, medium, large), `scope` (all, solo, status).
   - Logic:
     * `SessionEnrollment` for autoplay marker creation/deletion (`~/.claude/auto-speech-autoplay-enabled/<session_id>`).
     * Narration markers (`~/.claude/auto-speech-narrate-sessions/<session_id>`).
     * `SoloScope` for solo scope marker (`~/.claude/auto-speech-autoplay-solo`).
     * Autoplay TOML config updates.
3. `doctor` tool schema & handler:
   - Parameters: `as_json` (boolean, default false).
   - Logic:
     * Invoke `Doctor().run()`.
     * Return formatted text or JSON string. Set `isError: True` if `not report.healthy`.
4. Injectable collaborators pattern for testing in `McpServer.__init__`.

Do NOT implement code directly.
Write your analysis to:
/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_2/analysis.md
and handoff report to:
/Users/joshua/Developer/auto-speech/.agents/teamwork/explorer_m2_2/handoff.md
Send a completion message back to caller when done.
