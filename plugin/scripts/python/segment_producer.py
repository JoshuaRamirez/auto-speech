"""SegmentProducer: walks a ChunkPlan, synthesizes each chunk, enqueues segments."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from audio_segment import AudioSegment
from chunk_plan import ChunkPlan
from playback_queue import PlaybackQueue
from resilient_synthesizer import ResilientSynthesizer
from tts_engine import TTSEngine
from voice_profile import VoiceProfile
from wav_inspector import WavInspector


class SegmentProducer:
    """Produces AudioSegments from a ChunkPlan and enqueues them in order."""

    def __init__(
        self,
        tts_engine: TTSEngine,
        voice_profile: VoiceProfile,
        tmpdir: Path,
        queue: PlaybackQueue,
        stop_event: threading.Event,
    ) -> None:
        self._tts = tts_engine
        self._voice = voice_profile
        self._tmpdir = tmpdir
        self._queue = queue
        self._stop_event = stop_event
        self.error: Exception | None = None
        # A content-dependent Kokoro generate fault used to abort the whole
        # run (exit 5, no audio at all) because one chunk raised. Recover
        # per chunk instead: the offending phrase is dropped, the rest of
        # the message is still spoken.
        self._synth = ResilientSynthesizer(tts_engine)
        self.skipped_indices: list[int] = []

    def run(self, plan: ChunkPlan) -> None:
        """Iterate the plan, generate each segment, enqueue; close at end."""
        self._tmpdir.mkdir(parents=True, exist_ok=True)
        try:
            for descriptor in plan:
                if self._stop_event.is_set():
                    print("[producer] stop_event set; aborting")
                    break
                wav_path = self._tmpdir / f"chunk-{descriptor.index:03d}.wav"
                print(
                    f"[producer] synth #{descriptor.index} "
                    f"chars={descriptor.actual_char_count} -> {wav_path.name}"
                )
                t0 = time.monotonic()
                produced = self._synth.synthesize_one(
                    descriptor.text, self._voice, wav_path
                )
                elapsed = time.monotonic() - t0
                if not produced:
                    # Nothing speakable survived this chunk. Skip it rather
                    # than fail the run; the concat step only joins the
                    # chunks that exist.
                    self.skipped_indices.append(descriptor.index)
                    print(
                        f"[producer] skip #{descriptor.index} "
                        "(no speakable audio recovered)"
                    )
                    continue
                duration = WavInspector.duration_seconds(wav_path)
                segment = AudioSegment(
                    descriptor=descriptor,
                    wav_path=wav_path,
                    actual_duration_seconds=duration,
                    generation_elapsed_seconds=elapsed,
                )
                print(
                    f"[producer] enqueue #{descriptor.index} "
                    f"gen={elapsed:.2f}s dur={duration:.2f}s"
                )
                self._queue.put(segment)
        except Exception as exc:
            self.error = exc
            print(f"[producer] ERROR: {exc}")
            raise
        finally:
            self._queue.close()
            print("[producer] closed queue")
