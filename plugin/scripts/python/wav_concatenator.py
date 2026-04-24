"""WavConcatenator: concatenate a list of format-identical PCM WAVs into one.

Stdlib-only. Atomic: writes to `<dest>.partial` and renames on success.
Enforces format parity across sources — if any source disagrees on channels,
sample width, or sample rate, the whole concat fails loud.
"""
from __future__ import annotations

import os
import wave
from pathlib import Path


class WavConcatError(RuntimeError):
    """Raised on format mismatch or any I/O failure during concatenation."""


class WavConcatenator:
    """Read WAVs in order, emit one WAV with the sum of their frames."""

    _READ_FRAMES_BATCH = 65536  # frames per read batch

    @staticmethod
    def concat(sources: list[Path], dest: Path) -> None:
        if not sources:
            raise WavConcatError("no sources to concatenate")
        for p in sources:
            if not p.exists():
                raise WavConcatError(f"source WAV missing: {p}")

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".partial")

        # Open first source to fix the output format.
        with wave.open(str(sources[0]), "rb") as first:
            nchannels = first.getnchannels()
            sampwidth = first.getsampwidth()
            framerate = first.getframerate()

        total_frames = 0
        try:
            with wave.open(str(tmp), "wb") as out:
                out.setnchannels(nchannels)
                out.setsampwidth(sampwidth)
                out.setframerate(framerate)
                for p in sources:
                    with wave.open(str(p), "rb") as src:
                        if (
                            src.getnchannels() != nchannels
                            or src.getsampwidth() != sampwidth
                            or src.getframerate() != framerate
                        ):
                            raise WavConcatError(
                                f"format mismatch at {p}: "
                                f"got ({src.getnchannels()}, {src.getsampwidth()}, "
                                f"{src.getframerate()}), expected "
                                f"({nchannels}, {sampwidth}, {framerate})"
                            )
                        remaining = src.getnframes()
                        total_frames += remaining
                        while remaining > 0:
                            batch = min(remaining, WavConcatenator._READ_FRAMES_BATCH)
                            out.writeframes(src.readframes(batch))
                            remaining -= batch
        except WavConcatError:
            tmp.unlink(missing_ok=True)
            raise
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            raise WavConcatError(f"concat failed: {exc}") from exc

        os.replace(tmp, dest)
        print(
            f"[concat] wrote {dest}  frames={total_frames}  "
            f"duration={total_frames / framerate:.2f}s"
        )
