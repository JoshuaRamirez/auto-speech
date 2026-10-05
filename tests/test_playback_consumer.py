"""Feed 3 hand-built WAVs to the consumer; listen."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))

from audio_transcript import AudioTranscript
from chunk_planner import ChunkPlanner
from playback_consumer import AfplayLauncher, PlaybackConsumer
from playback_queue import PlaybackQueue
from segment_producer import SegmentProducer
from tts_executor import get_default_tts_engine
from voice_profile_store import VoiceProfileStore


class SilentLauncher:
    """Silent launcher that isolates consumer behavior without audible speaker output."""

    @staticmethod
    def play(wav_path: Path, stop_event: threading.Event) -> int:
        return 0


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    profile = VoiceProfileStore(root / "config" / "voice_calibration.json").load()
    if profile is None:
        print("run calibrator first", file=sys.stderr)
        return 1

    tmpdir = Path(tempfile.mkdtemp(prefix="auto-speech-test-play-"))

    listen = "--listen" in sys.argv or os.environ.get("AUTO_SPEECH_TEST_LISTEN") == "1"
    launcher = AfplayLauncher() if listen else SilentLauncher()

    # Generate WAVs ahead of time so we isolate consumer behavior.
    engine = get_default_tts_engine()
    plan = ChunkPlanner().plan(
        AudioTranscript(text="First. Second. Third."),
        profile,
        base_duration_seconds=1.0,
    )
    print(f"[test] plan len={len(plan)}")

    queue = PlaybackQueue(capacity=3)
    stop = threading.Event()
    producer = SegmentProducer(engine, profile, tmpdir, queue, stop)
    consumer = PlaybackConsumer(queue, stop, launcher=launcher)

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
