#!/usr/bin/env bash
# auto-speech — one-shot environment installer.
# Creates a Python 3.12 venv at project root and installs mlx-audio + misaki.
# Idempotent: safe to re-run.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$PROJECT_ROOT/.venv"
SENTINEL="$PROJECT_ROOT/setup/.installed"

echo "[auto-speech install] project root: $PROJECT_ROOT"

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

if [[ ! -d "$VENV" ]]; then
    echo "[auto-speech install] creating venv at $VENV with Python 3.12"
    uv venv --python 3.12 "$VENV"
else
    echo "[auto-speech install] venv already exists at $VENV"
fi

echo "[auto-speech install] installing mlx-audio and misaki..."
# shellcheck disable=SC1091
source "$VENV/bin/activate"
uv pip install --upgrade pip
uv pip install mlx-audio "misaki[en]" num2words "flask>=3.0"
python -m spacy download en_core_web_sm

date -u +"%Y-%m-%dT%H:%M:%SZ" > "$SENTINEL"
echo "[auto-speech install] done. Sentinel: $SENTINEL"
echo "[auto-speech install] next step: run setup/verify.sh"
