"""Unit tests for MlxSummarizer with a stubbed mlx_lm module.

The real mlx_lm.load downloads multi-GB models; we don't want that
in tests. Inject a fake mlx_lm module into sys.modules BEFORE importing
narrator_mlx_summarizer so the lazy `from mlx_lm import ...` inside
MlxSummarizer.__init__ resolves to the stub.

Verifies:
  - constructor reads the template file
  - prompt is built with chat-template if tokenizer supports it
  - {category}, {count}, {duration}, {events} are all substituted
  - _first_sentence strips leading bullets/quotes and takes first line
"""
from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))


_GENERATE_CALLS: list[dict] = []


class _FakeTokenizer:
    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        # Return the user prompt with a wrapper so we can detect the path.
        return f"<<CHAT>>{messages[0]['content']}<<END>>"


def _fake_load(model_id: str):
    return ("fake-model", _FakeTokenizer())


def _fake_generate(model, tokenizer, prompt=None, max_tokens=60, verbose=False):
    _GENERATE_CALLS.append({
        "model": model,
        "prompt": prompt,
        "max_tokens": max_tokens,
        "verbose": verbose,
    })
    return "  Stubbed summary line one.\n  bogus second line"


def _install_fake_mlx_lm():
    """Idempotent: ensure sys.modules['mlx_lm'] is our fake."""
    fake = types.ModuleType("mlx_lm")
    fake.load = _fake_load  # type: ignore[attr-defined]
    fake.generate = _fake_generate  # type: ignore[attr-defined]
    sys.modules["mlx_lm"] = fake


def _make_prompt_file(body: str) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write(body)
        return Path(f.name)


def _phase(events: list[str], category_value: str = "edit"):
    """Build a Phase mock without importing narrator_phase_classifier
    explicitly (we do, below — just keeping this helper compact)."""
    from narrator_phase_classifier import Category, Phase, ToolEvent

    cat = Category(category_value)
    p = Phase(category=cat)
    for i, summary in enumerate(events):
        p.add(ToolEvent(ts=float(i), tool_name="Edit", category=cat, summary=summary))
    return p


def test_init_loads_template_and_model() -> None:
    _install_fake_mlx_lm()
    # Defer the import until after the fake module is installed.
    if "narrator_mlx_summarizer" in sys.modules:
        del sys.modules["narrator_mlx_summarizer"]
    from narrator_mlx_summarizer import MlxSummarizer

    prompt_path = _make_prompt_file("prompt body with {category}/{count}/{duration}/{events}")
    try:
        summ = MlxSummarizer(
            model="fake-model-id",
            prompt_template_path=prompt_path,
            max_tokens=42,
        )
        assert summ._max_tokens == 42
        assert "{category}" in summ._template, "template must be loaded"
    finally:
        prompt_path.unlink()


def test_summarize_substitutes_all_template_fields() -> None:
    _install_fake_mlx_lm()
    if "narrator_mlx_summarizer" in sys.modules:
        del sys.modules["narrator_mlx_summarizer"]
    from narrator_mlx_summarizer import MlxSummarizer

    prompt_path = _make_prompt_file(
        "category={category} count={count} duration={duration}\n{events}"
    )
    try:
        summ = MlxSummarizer(
            model="m",
            prompt_template_path=prompt_path,
            max_tokens=10,
        )
        _GENERATE_CALLS.clear()
        out = summ.summarize(_phase(["Edit: foo.py", "Edit: bar.py"], "edit"))
    finally:
        prompt_path.unlink()

    assert len(_GENERATE_CALLS) == 1
    rendered = _GENERATE_CALLS[0]["prompt"]
    # chat-template wraps the user prompt.
    assert "<<CHAT>>" in rendered, "should use chat template path"
    assert "category=edit" in rendered
    assert "count=2" in rendered
    assert "- Edit: foo.py" in rendered
    assert "- Edit: bar.py" in rendered
    # _first_sentence trims to one line.
    assert out == "Stubbed summary line one."


def test_summarize_strips_leading_quote_or_bullet() -> None:
    _install_fake_mlx_lm()
    if "narrator_mlx_summarizer" in sys.modules:
        del sys.modules["narrator_mlx_summarizer"]
    from narrator_mlx_summarizer import _first_sentence

    assert _first_sentence('"quoted line"\nrest') == "quoted line\""
    assert _first_sentence("- bulleted line\nrest") == "bulleted line"
    assert _first_sentence("1. numbered line\nrest") == "numbered line"
    assert _first_sentence("plain\n  next") == "plain"
    assert _first_sentence("   \n  actual line  \n more") == "actual line"


def test_summarize_handles_tokenizer_without_chat_template() -> None:
    # If the tokenizer lacks apply_chat_template, the raw user prompt
    # is passed through.
    sys.modules.pop("narrator_mlx_summarizer", None)
    fake = types.ModuleType("mlx_lm")

    class _BareTokenizer:
        pass

    def _bare_load(model_id: str):
        return ("m", _BareTokenizer())

    def _bare_generate(model, tokenizer, prompt=None, max_tokens=60, verbose=False):
        # Verify NO chat-template wrapping happened.
        assert "<<CHAT>>" not in (prompt or "")
        return "ok"

    fake.load = _bare_load  # type: ignore[attr-defined]
    fake.generate = _bare_generate  # type: ignore[attr-defined]
    sys.modules["mlx_lm"] = fake

    from narrator_mlx_summarizer import MlxSummarizer

    prompt_path = _make_prompt_file("body {events}")
    try:
        summ = MlxSummarizer(model="m", prompt_template_path=prompt_path, max_tokens=5)
        out = summ.summarize(_phase(["Edit: x"], "edit"))
        assert out == "ok"
    finally:
        prompt_path.unlink()


def main() -> int:
    tests = [
        test_init_loads_template_and_model,
        test_summarize_substitutes_all_template_fields,
        test_summarize_strips_leading_quote_or_bullet,
        test_summarize_handles_tokenizer_without_chat_template,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"MlxSummarizer (mocked mlx_lm): {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
