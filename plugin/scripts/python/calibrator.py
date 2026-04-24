"""Calibrator: measure chars-per-second for a voice and persist a VoiceProfile.

Runs once at install, and on-demand any time the voice or speed changes.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from calibration_run import CalibrationRun
from tts_engine import TTSEngine
from voice_profile import VoiceProfile
from voice_profile_store import VoiceProfileStore
from wav_inspector import WavInspector


DEFAULT_VOICE_ID = "af_heart"
DEFAULT_SPEED = 1.0
SANITY_MIN_CPS = 10.0
SANITY_MAX_CPS = 30.0


class CalibrationError(RuntimeError):
    """Raised when calibration fails or yields an implausible result."""


def _project_root() -> Path:
    # this file: <root>/plugin/scripts/python/calibrator.py
    return Path(__file__).resolve().parents[3]


def _default_reference_path() -> Path:
    return _project_root() / "tests" / "reference" / "calibration_prose.txt"


def _default_config_path() -> Path:
    return _project_root() / "config" / "voice_calibration.json"


def _history_path() -> Path:
    return _project_root() / "config" / "calibration_history.jsonl"


def _append_history(run: CalibrationRun) -> None:
    path = _history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(run.to_dict()) + "\n")


class Calibrator:
    """Produce a VoiceProfile by timing synthesis of a reference passage."""

    def __init__(
        self,
        tts_engine: TTSEngine,
        store: VoiceProfileStore,
    ) -> None:
        self._tts = tts_engine
        self._store = store

    def measure(
        self,
        voice_id: str = DEFAULT_VOICE_ID,
        speed: float = DEFAULT_SPEED,
        reference_text_path: Path | None = None,
        out_wav_path: Path | None = None,
    ) -> VoiceProfile:
        ref_path = reference_text_path or _default_reference_path()
        if not ref_path.exists():
            raise CalibrationError(f"reference text missing at {ref_path}")
        text = ref_path.read_text(encoding="utf-8").strip()
        if not text:
            raise CalibrationError(f"reference text at {ref_path} is empty")

        char_count = len(text)
        wav_path = out_wav_path or (_project_root() / "tests" / "outputs" / "calibration.wav")
        wav_path.parent.mkdir(parents=True, exist_ok=True)

        # Bootstrap a temporary profile with a placeholder cps for the TTS call —
        # synthesize doesn't use cps, only voice_id + speed.
        bootstrap = VoiceProfile(
            voice_id=voice_id,
            speed=speed,
            chars_per_second=1.0,
            calibrated_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            calibration_source_chars=char_count,
        )

        print(f"[calibrator] synthesizing reference chars={char_count}")
        self._tts.synthesize(text, bootstrap, wav_path)

        duration = WavInspector.duration_seconds(wav_path)
        if duration <= 0:
            raise CalibrationError(f"zero duration WAV at {wav_path}")
        cps = char_count / duration

        print(f"[calibrator] duration={duration:.3f}s  chars_per_second={cps:.3f}")
        if not (SANITY_MIN_CPS <= cps <= SANITY_MAX_CPS):
            raise CalibrationError(
                f"chars_per_second {cps:.3f} outside sanity band "
                f"[{SANITY_MIN_CPS}, {SANITY_MAX_CPS}]"
            )

        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        profile = VoiceProfile(
            voice_id=voice_id,
            speed=speed,
            chars_per_second=cps,
            calibrated_at=now,
            calibration_source_chars=char_count,
        )
        self._store.save(profile)

        _append_history(
            CalibrationRun(
                timestamp=now,
                voice_id=voice_id,
                speed=speed,
                reference_text_chars=char_count,
                measured_duration_seconds=duration,
                computed_chars_per_second=cps,
            )
        )
        print(f"[calibrator] saved profile to {self._store.path}")
        return profile


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Calibrate chars-per-second for a Kokoro voice.")
    p.add_argument("--voice", default=DEFAULT_VOICE_ID)
    p.add_argument("--speed", type=float, default=DEFAULT_SPEED)
    p.add_argument("--reference", type=Path, default=None)
    p.add_argument("--out-wav", type=Path, default=None)
    args = p.parse_args(argv)

    store = VoiceProfileStore(_default_config_path())
    engine = TTSEngine()
    calibrator = Calibrator(engine, store)
    try:
        profile = calibrator.measure(
            voice_id=args.voice,
            speed=args.speed,
            reference_text_path=args.reference,
            out_wav_path=args.out_wav,
        )
    except CalibrationError as exc:
        print(f"calibration failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(profile.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
