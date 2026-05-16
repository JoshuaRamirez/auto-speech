"""Ollama-based summarizer. Uses the local Ollama HTTP API at
http://127.0.0.1:11434/api/generate. No persistent model state in our
process — Ollama runs as its own service and keeps the model warm.

Pre-requisites:
  - Ollama installed (`brew install ollama` then `ollama serve`).
  - The configured model pulled (`ollama pull qwen2.5:3b` or similar).

Config (in ~/.config/auto-speech/narrator.toml):

    [narrator]
    provider = "ollama"
    model = "qwen2.5:3b"
    prompt_template = "narrator_prompt_newscaster.txt"
    max_tokens = 60
    # Optional override (defaults to env or http://127.0.0.1:11434):
    # ollama_host = "http://127.0.0.1:11434"
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from narrator_phase_classifier import Phase
from narrator_summarizer import Summarizer


class OllamaSummarizer(Summarizer):
    _DEFAULT_HOST = "http://127.0.0.1:11434"

    def __init__(
        self,
        model: str,
        prompt_template_path: str | Path,
        max_tokens: int = 60,
        host: str | None = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._host = (
            host
            or os.environ.get("AUTO_SPEECH_OLLAMA_HOST")
            or self._DEFAULT_HOST
        ).rstrip("/")
        self._template = Path(prompt_template_path).read_text(encoding="utf-8")

    def summarize(self, phase: Phase) -> str:
        events_str = "\n".join(f"- {e.summary}" for e in phase.events)
        user_prompt = self._template.format(
            category=phase.category.value,
            count=len(phase.events),
            duration=f"{phase.duration_s:.0f}",
            events=events_str,
        )
        body = json.dumps({
            "model": self._model,
            "prompt": user_prompt,
            "stream": False,
            "options": {"num_predict": self._max_tokens},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self._host}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Ollama request to {self._host} failed: {exc}. "
                f"Is `ollama serve` running and the {self._model!r} model pulled?"
            ) from exc
        out = (payload.get("response") or "").strip()
        return _first_line(out)


def _first_line(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    for prefix in ('"', "'", "* ", "- ", "1. "):
        if line.startswith(prefix):
            line = line[len(prefix):].lstrip()
    return line
