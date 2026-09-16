#!/usr/bin/env bash
# auto-speech — register the `speak` MCP server with Claude Code (user scope).
# Idempotent: re-running leaves exactly one `auto-speech` server entry.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_CMD="$PROJECT_ROOT/plugin/scripts/shell/run_mcp.sh"
NAME="auto-speech"

if ! command -v claude >/dev/null 2>&1; then
    echo "error: the claude CLI is required to register the MCP server." >&2
    echo "       Other MCP clients: run \`bash $SERVER_CMD\` as a stdio server." >&2
    exit 1
fi

if [[ ! -x "$SERVER_CMD" ]]; then
    echo "error: server script missing or not executable: $SERVER_CMD" >&2
    exit 1
fi

if claude mcp get "$NAME" >/dev/null 2>&1; then
    # Re-register so a moved clone self-heals the recorded path.
    claude mcp remove --scope user "$NAME" >/dev/null 2>&1 || true
fi

claude mcp add --scope user "$NAME" -- bash "$SERVER_CMD"
echo "[install-mcp] registered MCP server '$NAME' → $SERVER_CMD"
