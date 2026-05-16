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
    configured provider's deps are missing. Less rich than an LLM
    summary but at least it's a complete sentence."""

    _PRESENT_PROGRESSIVE = {
        "explore": "exploring",
        "edit": "editing",
        "run": "running",
        "delegate": "delegating to",
        "reason": "reasoning through",
        "other": "performing",
    }
    _PAST = {
        "explore": "explored",
        "edit": "edited",
        "run": "ran",
        "delegate": "delegated to",
        "reason": "reasoned through",
        "other": "performed",
    }

    @staticmethod
    def _strip_tool_prefix(summary: str) -> str:
        # Event summaries are formatted as "ToolName: arg" by the
        # classifier. For mock narration we voice the action verb
        # ourselves, so trim the redundant tool prefix.
        if ": " in summary:
            return summary.split(": ", 1)[1]
        return summary

    def summarize(self, phase: Phase) -> str:
        # Single-event phase: gerund + the actual artifact, no narrator subject.
        if len(phase.events) == 1:
            verb = self._PRESENT_PROGRESSIVE.get(phase.category.value, "performing")
            arg = self._strip_tool_prefix(phase.events[0].summary)
            return f"{verb.capitalize()} {arg}"
        # Multi-event phase: past tense, count-led, no "the assistant".
        past = self._PAST.get(phase.category.value, "performed")
        first = self._strip_tool_prefix(phase.events[0].summary)
        return f"{past.capitalize()} {len(phase.events)} items, starting with {first}"


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
