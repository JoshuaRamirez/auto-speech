"""Build a 3-chunk plan and run the producer in isolation (no consumer)."""
from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from audio_transcript import AudioTranscript  # noqa: E402
from chunk_planner import ChunkPlanner  # noqa: E402
from playback_queue import PlaybackQueue, SENTINEL  # noqa: E402
from segment_producer import SegmentProducer  # noqa: E402
from tts_engine import TTSEngine  # noqa: E402
from voice_profile_store import VoiceProfileStore  # noqa: E402


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    store = VoiceProfileStore(root / "config" / "voice_calibration.json")
    profile = store.load()
    if profile is None:
        print("no voice profile; run calibrator first", file=sys.stderr)
        return 1

    text = (
        "This is the first chunk of a short test. "
        "Here is the second chunk, which is also quite short. "
        "And this is the third and final chunk of the test."
    )
    transcript = AudioTranscript(text=text)
    plan = ChunkPlanner().plan(transcript, profile, base_duration_seconds=2.0)
    print(f"[test] plan has {len(plan)} chunks")
    assert len(plan) >= 3, "expected at least 3 chunks"

    tmpdir = Path(tempfile.mkdtemp(prefix="auto-speech-test-prod-"))
    queue = PlaybackQueue(capacity=3)
    stop = threading.Event()
    producer = SegmentProducer(TTSEngine(), profile, tmpdir, queue, stop)

    t = threading.Thread(target=producer.run, args=(plan,))
    t.start()

    collected = []
    while True:
        item = queue.get()
        if item is SENTINEL:
            break
        collected.append(item)
        print(
            f"[test] got segment idx={item.descriptor.index} "
            f"dur={item.actual_duration_seconds:.2f}s "
            f"path={item.wav_path.name}"
        )
    t.join()

    if producer.error:
        print(f"[test] producer error: {producer.error}", file=sys.stderr)
        return 1

    assert len(collected) == len(plan), (
        f"expected {len(plan)} segments, got {len(collected)}"
    )
    for seg in collected:
        assert seg.wav_path.exists() and seg.wav_path.stat().st_size > 0
    print(f"[test] ok: {len(collected)} segments, tmpdir={tmpdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
