#!/usr/bin/env bash
# auto-speech — one-shot environment installer.
# Creates the project venv at .venv and installs the LOCKED dependency set
# via `uv sync` (pyproject.toml + uv.lock) for reproducible installs.
# Idempotent: safe to re-run.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$PROJECT_ROOT/.venv"
SENTINEL="$PROJECT_ROOT/setup/.installed"

echo "[auto-speech install] project root: $PROJECT_ROOT"
cd "$PROJECT_ROOT"

if ! command -v uv >/dev/null 2>&1; then
    echo "error: uv is not installed. Install via: brew install uv" >&2
    exit 1
fi

if ! command -v mpv >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then
        echo "[auto-speech install] installing mpv via brew"
        brew install mpv
    else
        echo "error: mpv is not installed and Homebrew is unavailable." >&2
        echo "       Install mpv manually and re-run this script." >&2
        exit 1
    fi
fi

if ! command -v jq >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then
        echo "[auto-speech install] installing jq via brew (used by setup/install-hook.sh)"
        brew install jq
    else
        echo "warning: jq is not installed; setup/install-hook.sh will fail until you install it." >&2
    fi
fi

# Preserve an existing narration install: if mlx-lm is already present in
# the venv, include the `narrate` extra so `uv sync` does not prune it.
# (uv sync is exact by default; a base sync would otherwise uninstall a
# user's narration capability.)
SYNC_ARGS=()
if [[ -x "$VENV/bin/python" ]] && "$VENV/bin/python" -c "import mlx_lm" >/dev/null 2>&1; then
    echo "[auto-speech install] detected narration deps — keeping the 'narrate' extra"
    SYNC_ARGS+=(--extra narrate)
fi

echo "[auto-speech install] syncing locked dependencies (uv sync)..."
uv sync "${SYNC_ARGS[@]}"

# spaCy English model for misaki G2P. NOT a pip dependency — a separate
# one-time download, cached under the venv. Skip when already importable
# so re-running the installer doesn't re-download it.
if uv run --no-sync python -c "import en_core_web_sm" >/dev/null 2>&1; then
    echo "[auto-speech install] spaCy model en_core_web_sm already present"
else
    echo "[auto-speech install] downloading spaCy model en_core_web_sm"
    uv run --no-sync python -m spacy download en_core_web_sm
fi

date -u +"%Y-%m-%dT%H:%M:%SZ" > "$SENTINEL"
echo "[auto-speech install] done. Sentinel: $SENTINEL"
echo "[auto-speech install] next step: run setup/verify.sh"
