"""PipelineOrchestrator: wire everything for one /speak invocation."""
from __future__ import annotations

import shutil
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

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
    SHORT_THRESHOLD_SECONDS,
)
from mpv_controller import MpvController, MpvNotInstalledError, MpvStartupError
from playback_queue import SENTINEL, PlaybackQueue
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


def _start_mpv_or_exit_code(wav_path: Path) -> int:
    """Hand a WAV off to the mpv controller. Returns an exit code.

    Starting mpv is non-blocking from the orchestrator's perspective:
    the controller returns once the mpv socket is responsive, which is
    typically 200–400 ms after spawn. Playback continues after this
    process exits — that's the point of Phase 12.
    """
    try:
        MpvController().start(wav_path)
    except MpvNotInstalledError as exc:
        print(f"[pipeline] {exc}", file=sys.stderr)
        return EXIT_PLAYBACK_FAIL
    except MpvStartupError as exc:
        print(f"[pipeline] mpv startup failed: {exc}", file=sys.stderr)
        return EXIT_PLAYBACK_FAIL
    except FileNotFoundError as exc:
        print(f"[pipeline] mpv could not find WAV: {exc}", file=sys.stderr)
        return EXIT_PLAYBACK_FAIL
    return EXIT_OK


class PipelineOrchestrator:
    """Wire all services for a single /speak invocation."""

    def __init__(
        self,
        keep_artifacts: bool = False,
        source_hash: str | None = None,
        cache_root: Path | None = None,
        tts_engine: TTSEngine | None = None,
    ) -> None:
        self._keep_artifacts = keep_artifacts
        self._source_hash = source_hash
        self._cache = CacheStore(cache_root or _default_cache_root())
        # When a long-lived caller (e.g., the web server) holds a hot
        # TTSEngine, injecting it here skips the per-run model load.
        self._injected_tts = tts_engine

    def _maybe_cache_hit(self, profile: VoiceProfile) -> int | None:
        """If --source-hash was provided and the cache has a matching entry,
        start mpv on it and return an exit code. Otherwise return None.
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
        return _start_mpv_or_exit_code(wav_path)

    def run(self, transcript_text: str, turn_ordinal: int = 1) -> int:
        profile = _load_profile_or_fallback()
        stop_event = threading.Event()

        # Cache short-circuit comes FIRST so that a cache hit needs no
        # transcript_text at all — the cached full.wav is self-sufficient.
        cache_result = self._maybe_cache_hit(profile)
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

        tts_engine = self._injected_tts or TTSEngine()
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

        # Phases 12 + 13 note: playback is no longer per-chunk. The producer
        # generates all chunks; we concat; mpv plays the single WAV. Run
        # the producer SYNCHRONOUSLY on the calling thread so single-thread
        # TTS engines (MLX in particular: per-thread compute streams) keep
        # their state on the same thread that loaded the model.
        queue = PlaybackQueue(capacity=max(len(plan) + 2, 4))
        producer = SegmentProducer(tts_engine, profile, tmpdir, queue, stop_event)
        try:
            producer.run(plan)
        except Exception:
            # SegmentProducer.run records the error before re-raising.
            return EXIT_TTS_FAIL

        if producer.error is not None:
            return EXIT_TTS_FAIL

        # Drain the queue so its SENTINEL contract is satisfied. The
        # producer has already finished, so this is a non-blocking sweep.
        while True:
            item = queue.get(timeout=0.5)
            if item is SENTINEL:
                break

        # Invariant I-10.1: concat-iff-success.
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

        # Hand off to mpv. Controller returns once the socket is ready.
        return _start_mpv_or_exit_code(full_path)
