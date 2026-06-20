"""Feed 3 hand-built WAVs to the consumer; listen."""
from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from audio_transcript import AudioTranscript  # noqa: E402
from chunk_planner import ChunkPlanner  # noqa: E402
from playback_consumer import PlaybackConsumer  # noqa: E402
from playback_queue import PlaybackQueue  # noqa: E402
from segment_producer import SegmentProducer  # noqa: E402
from tts_engine import TTSEngine  # noqa: E402
from voice_profile_store import VoiceProfileStore  # noqa: E402


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    profile = VoiceProfileStore(root / "config" / "voice_calibration.json").load()
    if profile is None:
        print("run calibrator first", file=sys.stderr)
        return 1

    tmpdir = Path(tempfile.mkdtemp(prefix="auto-speech-test-play-"))

    # Generate 3 WAVs ahead of time so we isolate consumer behavior.
    engine = TTSEngine()
    plan = ChunkPlanner().plan(
        AudioTranscript(text="First. Second. Third."),
        profile,
        base_duration_seconds=1.0,
    )
    print(f"[test] plan len={len(plan)}")

    queue = PlaybackQueue(capacity=3)
    stop = threading.Event()
    producer = SegmentProducer(engine, profile, tmpdir, queue, stop)
    consumer = PlaybackConsumer(queue, stop)

    t_prod = threading.Thread(target=producer.run, args=(plan,))
    t_cons = threading.Thread(target=consumer.run)
    t_prod.start()
    t_cons.start()
    t_prod.join()
    t_cons.join()

    if producer.error or consumer.error:
        print(f"prod={producer.error} cons={consumer.error}", file=sys.stderr)
        return 1
    print(f"[test] ok: played {consumer.played_count} segments from {tmpdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
