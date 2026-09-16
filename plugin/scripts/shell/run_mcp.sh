#!/usr/bin/env bash
# auto-speech — start the stdio MCP server (the `speak` tool).
# Launched by the MCP client; stdout is the protocol channel.

set -euo pipefail

PLUGIN_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_SCRIPTS_DIR/../.." && pwd)"
VENV="$PROJECT_ROOT/.venv"

if [[ ! -d "$VENV" ]]; then
    echo "error: venv missing at $VENV. Run $PROJECT_ROOT/setup/install.sh" >&2
    exit 1
fi

exec "$VENV/bin/python" "$PLUGIN_SCRIPTS_DIR/python/mcp_server.py"
