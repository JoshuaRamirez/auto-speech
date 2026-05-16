"""Summarizer interface and factory.

Providers are pluggable. The first impl is MLX (Apple Silicon native);
future impls will be Ollama, OpenAI, Anthropic. Providers are imported
lazily so missing optional deps don't break the import.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from narrator_phase_classifier import Phase


class Summarizer(ABC):
    @abstractmethod
    def summarize(self, phase: Phase) -> str: ...


class MockSummarizer(Summarizer):
    """Templated fallback. Used when no provider is configured or the
    configured provider's deps are missing."""

    def summarize(self, phase: Phase) -> str:
        verb = {
            "explore": "examined",
            "edit": "modified",
            "run": "executed",
            "delegate": "delegated to",
            "reason": "reasoned through",
            "other": "performed",
        }.get(phase.category.value, "performed")
        return (
            f"The assistant {verb} {len(phase.events)} {phase.category.value} "
            f"operations over {phase.duration_s:.0f} seconds."
        )


def load_summarizer(config: dict) -> Summarizer:
    """Return a Summarizer per config. Falls back to MockSummarizer on
    any failure (e.g., mlx-lm not installed)."""
    provider = (config.get("provider") or "mock").lower()

    if provider == "mock":
        return MockSummarizer()

    if provider == "mlx":
        try:
            from narrator_mlx_summarizer import MlxSummarizer

            return MlxSummarizer(
                model=config["model"],
                prompt_template_path=config["prompt_template_path"],
                max_tokens=int(config.get("max_tokens", 60)),
            )
        except Exception as exc:
            import sys

            print(
                f"[narrator] mlx provider unavailable ({exc}); "
                f"falling back to MockSummarizer",
                file=sys.stderr,
            )
            return MockSummarizer()

    raise ValueError(f"unknown narrator provider: {provider!r}")
