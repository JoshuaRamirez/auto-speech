#!/usr/bin/env bash
# auto-speech — unregister the `speak` MCP server from Claude Code.
# Idempotent: a missing registration is not an error.

set -euo pipefail

NAME="auto-speech"

if ! command -v claude >/dev/null 2>&1; then
    echo "error: the claude CLI is required to unregister the MCP server." >&2
    exit 1
fi

if claude mcp remove --scope user "$NAME" >/dev/null 2>&1; then
    echo "[uninstall-mcp] removed MCP server '$NAME'"
else
    echo "[uninstall-mcp] MCP server '$NAME' was not registered (user scope)"
fi
