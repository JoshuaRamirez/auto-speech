#!/usr/bin/env bash
# auto-speech — extract the Nth-most-recent assistant message to stdout.
# args: --ordinal N   (default 1)
#       --cwd PATH    (default: current working directory)

set -euo pipefail

PLUGIN_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(cd "$PLUGIN_SCRIPTS_DIR/../.." && pwd)"
VENV="$PROJECT_ROOT/.venv"

if [[ ! -d "$VENV" ]]; then
    echo "error: venv missing at $VENV. Run $PROJECT_ROOT/setup/install.sh" >&2
    exit 1
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"
exec python "$PLUGIN_SCRIPTS_DIR/python/extract_message.py" "$@"
