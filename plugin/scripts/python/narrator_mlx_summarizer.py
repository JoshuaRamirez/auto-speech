"""MLX-based summarizer. Optional — requires `pip install mlx-lm` and
a downloaded model. Use auto-speech-narrate-install to set up."""
from __future__ import annotations

from pathlib import Path

from narrator_phase_classifier import Phase
from narrator_summarizer import Summarizer


class MlxSummarizer(Summarizer):
    """One model instance, reused across summarize() calls. Loading is
    several seconds; meant to live inside the narrator service."""

    def __init__(
        self,
        model: str,
        prompt_template_path: str | Path,
        max_tokens: int = 60,
    ) -> None:
        # Lazy import so importing this module never triggers MLX deps
        # unless someone actually constructs an instance.
        from mlx_lm import generate, load

        self._generate = generate
        self._model, self._tokenizer = load(model)
        self._max_tokens = max_tokens
        self._template = Path(prompt_template_path).read_text(encoding="utf-8")

    def summarize(self, phase: Phase) -> str:
        events_str = "\n".join(f"- {e.summary}" for e in phase.events)
        user_prompt = self._template.format(
            category=phase.category.value,
            count=len(phase.events),
            duration=f"{phase.duration_s:.0f}",
            events=events_str,
        )

        if hasattr(self._tokenizer, "apply_chat_template"):
            prompt = self._tokenizer.apply_chat_template(
                [{"role": "user", "content": user_prompt}],
                tokenize=False,
                add_generation_prompt=True,
            )
        else:
            prompt = user_prompt

        out = self._generate(
            self._model,
            self._tokenizer,
            prompt=prompt,
            max_tokens=self._max_tokens,
            verbose=False,
        )
        return _first_sentence(out.strip())


def _first_sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    # take first non-empty line
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    # strip leading quote chars or bullets the model might emit
    for prefix in ('"', "'", "* ", "- ", "1. "):
        if line.startswith(prefix):
            line = line[len(prefix):].lstrip()
    return line
