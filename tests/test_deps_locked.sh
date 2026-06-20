#!/usr/bin/env bash
# Guards against pyproject.toml / uv.lock drift: the committed lock must
# stay in sync with the manifest so installs are reproducible.
# `uv lock --check` verifies this WITHOUT modifying the lock or any venv.
# Skips cleanly when uv is unavailable (keeps non-uv environments green).

set -uo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$TESTS_DIR/.." && pwd)"

if ! command -v uv >/dev/null 2>&1; then
    echo "  skip uv-not-installed"
    echo "deps-locked: skipped (uv missing)"
    exit 0
fi

cd "$PROJECT_ROOT" || exit 1
if uv lock --check >/dev/null 2>&1; then
    echo "  ok  uv.lock is in sync with pyproject.toml"
    echo "deps-locked: 1 ran, 0 failed"
    exit 0
fi

echo "  FAIL uv.lock is STALE — run 'uv lock' and commit the result"
echo "deps-locked: 1 ran, 1 failed"
exit 1
