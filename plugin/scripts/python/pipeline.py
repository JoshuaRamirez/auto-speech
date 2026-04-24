"""PipelineOrchestrator: wire everything for one /speak invocation."""
from __future__ import annotations

import shutil
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from afplay_launcher import AfplayLauncher
from audio_transcript import AudioTranscript
from cache_entry import CacheEntry
from cache_store import CachePromotionError, CacheStore
from chunk_planner import ChunkPlanner
from config_constants import (
    BASE_DURATION_SECONDS,
    BOUNDARY_TOLERANCE,
    DEFAULT_SPEED,
    DEFAULT_VOICE_ID,
    FALLBACK_CHARS_PER_SEC,
    QUEUE_CAPACITY,
    SHORT_THRESHOLD_SECONDS,
)
from playback_consumer import PlaybackConsumer
from playback_queue import PlaybackQueue
from segment_producer import SegmentProducer
from short_path import ShortPathStrategy
from tts_engine import TTSEngine, TTSGenerationError
from voice_profile import VoiceProfile
from voice_profile_store import VoiceProfileStore
from wav_concatenator import WavConcatError, WavConcatenator
from wav_inspector import WavInspector


EXIT_OK = 0
EXIT_REWRITE_FAIL = 4
EXIT_TTS_FAIL = 5
EXIT_PLAYBACK_FAIL = 6
EXIT_INTERRUPTED = 130


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_config_path() -> Path:
    return _project_root() / "config" / "voice_calibration.json"


def _default_cache_root() -> Path:
    return _project_root() / "config" / "cache"


def _load_profile_or_fallback() -> VoiceProfile:
    store = VoiceProfileStore(_default_config_path())
    loaded = store.load()
    if loaded is not None:
        return loaded
    print(
        "[pipeline] WARNING: no voice profile found at "
        f"{store.path}; using fallback chars_per_second={FALLBACK_CHARS_PER_SEC}. "
        "Run setup/install.sh or `python -m plugin.scripts.python.calibrator` to calibrate."
    )
    return VoiceProfile(
        voice_id=DEFAULT_VOICE_ID,
        speed=DEFAULT_SPEED,
        chars_per_second=FALLBACK_CHARS_PER_SEC,
        calibrated_at="fallback",
        calibration_source_chars=0,
    )


class PipelineOrchestrator:
    """Wire all services for a single /speak invocation."""

    def __init__(
        self,
        keep_artifacts: bool = False,
        source_hash: str | None = None,
        cache_root: Path | None = None,
    ) -> None:
        self._keep_artifacts = keep_artifacts
        self._source_hash = source_hash
        self._cache = CacheStore(cache_root or _default_cache_root())

    def _maybe_cache_hit(
        self, profile: VoiceProfile, stop_event: threading.Event
    ) -> int | None:
        """If --source-hash was provided and the cache has a matching entry,
        play it and return an exit code. Otherwise return None.
        """
        if self._source_hash is None:
            return None
        hit = self._cache.lookup(self._source_hash)
        if hit is None:
            print(
                f"[pipeline] cache miss  hash={self._source_hash[:16]}...",
                file=sys.stderr,
            )
            return None
        wav_path, entry = hit
        print(
            f"[pipeline] cache HIT   hash={self._source_hash[:16]}... "
            f"voice={entry.voice_id} dur={entry.duration_seconds:.1f}s "
            f"path={wav_path}",
            file=sys.stderr,
        )
        rc = AfplayLauncher.play(wav_path, stop_event)
        return EXIT_OK if rc == 0 else EXIT_PLAYBACK_FAIL

    def run(self, transcript_text: str, turn_ordinal: int = 1) -> int:
        profile = _load_profile_or_fallback()
        stop_event = threading.Event()

        # Cache short-circuit comes FIRST so that a cache hit needs no
        # transcript_text at all — the cached full.wav is self-sufficient.
        cache_result = self._maybe_cache_hit(profile, stop_event)
        if cache_result is not None:
            return cache_result

        transcript_text = transcript_text.strip()
        if not transcript_text:
            print("[pipeline] empty transcript text", file=sys.stderr)
            return EXIT_REWRITE_FAIL

        transcript = AudioTranscript(text=transcript_text)
        tmpdir = Path(
            tempfile.mkdtemp(
                prefix=f"auto-speech-"
                f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-"
            )
        )
        print(
            f"[pipeline] ordinal={turn_ordinal} chars={transcript.char_count} "
            f"tmpdir={tmpdir}"
        )

        tts_engine = TTSEngine()
        short_path = ShortPathStrategy(SHORT_THRESHOLD_SECONDS)

        exit_code = EXIT_OK
        try:
            if short_path.should_use(transcript, profile):
                print("[pipeline] short path")
                try:
                    exit_code = short_path.execute(
                        transcript, profile, tts_engine, tmpdir, stop_event
                    )
                except TTSGenerationError as exc:
                    print(f"[pipeline] TTS failure: {exc}", file=sys.stderr)
                    exit_code = EXIT_TTS_FAIL
            else:
                print("[pipeline] long path (Fibonacci)")
                exit_code = self._long_path(
                    transcript, profile, tts_engine, tmpdir, stop_event
                )

            if exit_code == EXIT_OK:
                promote_rc = self._maybe_promote(tmpdir, profile, transcript)
                if promote_rc != EXIT_OK:
                    exit_code = promote_rc
        except KeyboardInterrupt:
            print("[pipeline] keyboard interrupt")
            stop_event.set()
            exit_code = EXIT_INTERRUPTED
        finally:
            if exit_code == EXIT_OK and not self._keep_artifacts:
                shutil.rmtree(tmpdir, ignore_errors=True)
                print(f"[pipeline] cleaned up {tmpdir}")
            else:
                print(f"[pipeline] artifacts preserved at {tmpdir}")
        return exit_code

    def _maybe_promote(
        self,
        tmpdir: Path,
        profile: VoiceProfile,
        transcript: AudioTranscript,
    ) -> int:
        """Promote tmpdir/full.wav into the cache if --source-hash was set.

        No-op (returns EXIT_OK) when source_hash is None.
        """
        if self._source_hash is None:
            return EXIT_OK
        full_wav = tmpdir / "full.wav"
        if not full_wav.is_file() or full_wav.stat().st_size == 0:
            print(
                f"[pipeline] promote skipped: {full_wav} missing or empty",
                file=sys.stderr,
            )
            return EXIT_TTS_FAIL
        try:
            duration = WavInspector.duration_seconds(full_wav)
        except Exception as exc:
            print(f"[pipeline] promote inspect failed: {exc}", file=sys.stderr)
            return EXIT_TTS_FAIL
        entry = CacheEntry(
            source_hash=self._source_hash,
            voice_id=profile.voice_id,
            speed=profile.speed,
            char_count=transcript.char_count,
            duration_seconds=duration,
            created_at=datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            chars_per_second_at_creation=profile.chars_per_second,
        )
        try:
            self._cache.promote(self._source_hash, full_wav, entry)
        except CachePromotionError as exc:
            print(f"[pipeline] cache promote failed: {exc}", file=sys.stderr)
            return EXIT_TTS_FAIL
        return EXIT_OK

    def _long_path(
        self,
        transcript: AudioTranscript,
        profile: VoiceProfile,
        tts_engine: TTSEngine,
        tmpdir: Path,
        stop_event: threading.Event,
    ) -> int:
        plan = ChunkPlanner().plan(
            transcript,
            profile,
            base_duration_seconds=BASE_DURATION_SECONDS,
            tolerance=BOUNDARY_TOLERANCE,
        )
        print(
            f"[pipeline] plan chunks={len(plan)} est_total={plan.total_estimated_duration_seconds:.1f}s"
        )

        queue = PlaybackQueue(capacity=QUEUE_CAPACITY)
        producer = SegmentProducer(tts_engine, profile, tmpdir, queue, stop_event)
        consumer = PlaybackConsumer(queue, stop_event)

        t_prod = threading.Thread(target=producer.run, args=(plan,), name="producer")
        t_cons = threading.Thread(target=consumer.run, name="consumer")
        t_prod.start()
        t_cons.start()

        t_prod.join()
        t_cons.join()

        if producer.error is not None:
            # Producer errors most commonly map to TTS failure.
            if isinstance(producer.error, TTSGenerationError):
                return EXIT_TTS_FAIL
            return EXIT_TTS_FAIL
        if consumer.error is not None:
            return EXIT_PLAYBACK_FAIL

        # Invariant I-10.1: concat-iff-success. Both threads finished cleanly.
        chunk_paths = [tmpdir / f"chunk-{d.index:03d}.wav" for d in plan]
        full_path = tmpdir / "full.wav"
        try:
            WavConcatenator.concat(chunk_paths, full_path)
        except WavConcatError as exc:
            print(f"[pipeline] concat failed: {exc}", file=sys.stderr)
            return EXIT_TTS_FAIL

        # Invariant I-10.2: chunks-absent-on-success-unless-kept.
        if not self._keep_artifacts:
            for p in chunk_paths:
                p.unlink(missing_ok=True)
            print(f"[pipeline] removed {len(chunk_paths)} chunk WAVs")

        return EXIT_OK
