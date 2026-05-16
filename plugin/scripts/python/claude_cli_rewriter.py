"""ClaudeCliRewriter: run `claude -p` to rewrite text per the 12-rule prompt.

L1 adapter — wraps a subprocess. Stateless. Same Claude that powers
`/speak`, invoked headless. Uses the user's existing Claude Code login
(no API key). Prompt template is loaded once at construction from the
shared file under plugin/prompts/.

See docs/decisions/ADR-011-claude-cli-rewriter.md.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class ClaudeCliUnavailable(RuntimeError):
    """Raised when the `claude` binary is not on PATH."""


class ClaudeCliRewriteError(RuntimeError):
    """Raised on timeout, non-zero exit, or empty output."""


class ClaudeCliRewriter:
    """Rewrite source text into audio-friendly form via the Claude CLI."""

    def __init__(
        self,
        prompt_template: str,
        claude_bin: str = "claude",
    ) -> None:
        if "{SOURCE}" not in prompt_template:
            raise ValueError("prompt_template must contain a {SOURCE} placeholder")
        self._template = prompt_template
        self._bin = claude_bin

    def is_available(self) -> bool:
        return shutil.which(self._bin) is not None

    def rewrite(
        self,
        source_text: str,
        *,
        timeout_seconds: float = 60.0,
    ) -> str:
        text = source_text.strip()
        if not text:
            raise ClaudeCliRewriteError("source_text is empty")
        if not self.is_available():
            raise ClaudeCliUnavailable(
                f"{self._bin!r} not found on PATH. Install Claude Code "
                f"(https://claude.com/claude-code) to enable rewrite."
            )

        prompt = self._template.replace("{SOURCE}", text)
        cmd = [
            self._bin,
            "-p",
            "--output-format",
            "text",
            "--allowed-tools",
            "",
        ]
        # The `claude -p` subprocess starts its own Claude Code session,
        # which fires its OWN Stop / PreToolUse / PostToolUse hooks against
        # this plugin's autoplay_hook.sh and narrator_hook.sh. Without
        # this guard, every rewrite would trigger a NEW autoplay worker
        # that reads from the rewrite's session jsonl (newest-mtime in
        # the project slug dir) and recursively re-rewrites its own
        # output. Set an env var that both hooks check up front; nested
        # invocations bail with exit 0.
        env = {**os.environ, "AUTO_SPEECH_SUPPRESS_HOOKS": "1"}
        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=True,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClaudeCliRewriteError(
                f"claude rewrite timed out after {timeout_seconds:.0f}s"
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            raise ClaudeCliRewriteError(
                f"claude exited {exc.returncode}: {stderr[:500]}"
            ) from exc

        out = (result.stdout or "").strip()
        if not out:
            raise ClaudeCliRewriteError("claude returned empty rewrite")
        return out


def load_default_template() -> str:
    """Load the shared 12-rule prompt template from disk."""
    project_root = Path(__file__).resolve().parents[3]
    path = project_root / "plugin" / "prompts" / "audio_rewrite_prompt.txt"
    return path.read_text(encoding="utf-8")
