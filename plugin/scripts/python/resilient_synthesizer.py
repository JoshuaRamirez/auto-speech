"""ResilientSynthesizer: synthesis that survives a single bad span.

mlx-audio's Kokoro `generate` raises on certain inputs (a broadcast-shape
fault whose trigger is content-dependent, not length-dependent). A bare
TTSEngine.synthesize() call propagates that as TTSGenerationError, and
every caller upstream treats it as fatal — so one unlucky phrase silences
an entire message.

This collaborator wraps a TTSEngine and degrades instead: a span that
faults is split one level finer (SpanSplitter) and retried, down to
_MAX_SPLIT_DEPTH. Fragments that still fault at the floor are dropped
with a log line. The caller gets whatever was speakable, which is very
nearly always everything but the offending phrase.

Two entry points:
  synthesize_parts() — the raw recovery walk; returns 0..n WAV paths.
  synthesize_one()   — collapses those parts into exactly `out_path`,
                       for callers that promised a single file.

Both are decisions about audio only; neither touches playback or cache.
"""
from __future__ import annotations

import sys
from pathlib import Path

from span_splitter import SpanSplitter
from tts_engine import (
    TTSEngine,
    TTSGenerationError,
    TTSNoSpeakableContentError,
)
from voice_profile import VoiceProfile
from wav_concatenator import WavConcatError, WavConcatenator

# Depth cap for the retry split (sentence → clause → word-halves → floor).
MAX_SPLIT_DEPTH = 4


def _default_log(msg: str) -> None:
    print(msg, file=sys.stderr)


class ResilientSynthesizer:
    """Synthesize a span, splitting finer on generation failure."""

    def __init__(
        self,
        engine: TTSEngine,
        *,
        max_depth: int = MAX_SPLIT_DEPTH,
        splitter: SpanSplitter | None = None,
        log=None,
    ) -> None:
        self._engine = engine
        self._max_depth = max_depth
        self._splitter = splitter or SpanSplitter()
        self._log = log or _default_log

    def synthesize_parts(
        self,
        text: str,
        profile: VoiceProfile,
        out_path: Path,
        depth: int = 0,
    ) -> list[Path]:
        """Synthesize `text`, returning the WAV paths produced (0..n).

        An unspeakable span yields an empty list (skipped, not fatal). A
        span that trips a generation fault is split finer and retried; a
        fragment that still fails at the depth cap is dropped so one bad
        phrase cannot silence the whole message.
        """
        try:
            self._engine.synthesize(text, profile, out_path)
            return [out_path]
        except TTSNoSpeakableContentError:
            return []  # nothing to say in this span; skip it
        except TTSGenerationError as exc:
            parts = self._splitter.split(text)
            if depth >= self._max_depth or len(parts) <= 1:
                self._log(
                    f"[resilient] dropping unsynthesizable span "
                    f"(depth={depth}) {text[:40]!r}: {exc}"
                )
                return []
            self._log(
                f"[resilient] span tripped generate fault; splitting into "
                f"{len(parts)} (depth={depth})"
            )
            results: list[Path] = []
            for i, part in enumerate(parts):
                sub = out_path.with_name(f"{out_path.stem}-{depth}_{i}.wav")
                results.extend(
                    self.synthesize_parts(part, profile, sub, depth + 1)
                )
            return results

    def synthesize_one(
        self, text: str, profile: VoiceProfile, out_path: Path
    ) -> bool:
        """Produce exactly `out_path`, recovering from generation faults.

        Returns True when `out_path` holds audio, False when nothing in
        `text` was speakable (no file is written in that case). Recovered
        fragments are concatenated in order, so the caller's single-file
        contract holds whether or not a retry was needed.
        """
        parts = self.synthesize_parts(text, profile, out_path)
        if not parts:
            return False
        if len(parts) == 1:
            if parts[0] != out_path:
                parts[0].replace(out_path)
            return True
        try:
            WavConcatenator.concat(parts, out_path)
        except WavConcatError as exc:
            raise TTSGenerationError(
                f"could not join recovered fragments: {exc}"
            ) from exc
        for p in parts:
            if p != out_path:
                p.unlink(missing_ok=True)
        return True
