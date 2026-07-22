---
description: Install MLX deps + pull the default narrator model, then write a starter ~/.config/auto-speech/narrator.toml that selects the MLX provider.
argument-hint: "[model-id]"
allowed-tools: Bash
---

You are executing `/auto-speech-narrate-install` for the auto-speech plugin.

## Argument

Parse `$ARGUMENTS`:
- If empty or whitespace-only → `MODEL=mlx-community/Qwen2.5-3B-Instruct-4bit`
- Otherwise → use the argument literally as `MODEL`.

## Step 1 — Inform the user

Before running anything, tell the user this will:
1. Install the `narrate` extra (mlx-lm, from the lock) into the project venv via `uv sync --extra narrate`
2. Download `<MODEL>` from HuggingFace (typically 1-3 GB)
3. Write `~/.config/auto-speech/narrator.toml` with `provider = "mlx"` and `model = "<MODEL>"`

This may take several minutes on first run. The model is cached under
`~/.cache/huggingface/` so subsequent runs are instant.

Ask the user to confirm with a one-word "yes" or "no" reply (use AskUserQuestion).

If they decline, stop and respond `install cancelled`.

## Step 2 — Install

Only after explicit confirmation, run this Bash command. Substitute the
chosen MODEL.

```
set -euo pipefail
PROJECT_ROOT="$(cat "$HOME/.config/auto-speech/root" 2>/dev/null || true)"
[ -d "$PROJECT_ROOT" ] || { echo "auto-speech: project root not configured — run setup/install-plugin.sh from your clone" >&2; exit 1; }
VENV="$PROJECT_ROOT/.venv"
MODEL='<MODEL>'

# Install mlx-lm from the lock (reproducible) and keep the base deps.
( cd "$PROJECT_ROOT" && uv sync --extra narrate )

# Pre-pull the model so first narration isn't blocked by a download.
"$VENV/bin/python" -c "
from mlx_lm import load
print('downloading and loading model:', '$MODEL')
load('$MODEL')
print('model ready')
"

# Write the user-level config selecting MLX.
CONFIG_DIR="$HOME/.config/auto-speech"
mkdir -p "$CONFIG_DIR"
CONFIG_FILE="$CONFIG_DIR/narrator.toml"
if [[ ! -f "$CONFIG_FILE" ]]; then
    cat > "$CONFIG_FILE" <<TOML
[narrator]
provider = "mlx"
model = "$MODEL"
prompt_template = "$PROJECT_ROOT/config/narrator_prompt_newscaster.txt"
max_tokens = 60
silence_seconds = 8.0
idle_shutdown_seconds = 600.0
TOML
    echo "wrote $CONFIG_FILE"
else
    echo "kept existing $CONFIG_FILE — edit it manually to switch provider to mlx"
fi
```

## Step 3 — Report

Respond with one line: `narrator install complete — provider=mlx model=<MODEL>, config=<CONFIG_FILE>`.

If anything failed, surface the failing command's stderr verbatim and the
user-readable error in plain prose. Do not promise that narration "will work"
— suggest the user run `/auto-speech-narrate-on` then trigger a tool-heavy
turn to verify.
