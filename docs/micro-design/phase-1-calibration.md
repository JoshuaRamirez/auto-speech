# Phase 1 Micro-Design — Voice Calibration

## Scope
Measure `chars_per_second` for the default voice at speed 1.0 and persist as a
`VoiceProfile` in `config/voice_calibration.json`.

## M1 — Classes at this level
- `VoiceProfile` (dataclass)
- `CalibrationRun` (dataclass)
- `VoiceProfileStore` (JSON read/write)
- `WavInspector` (duration probe)
- `TTSEngine` (thin subset used here — full version in Phase 5)
- `Calibrator` (service)

## M2 — Semantics
- `VoiceProfile`: immutable record of voice + speed + measured chars/sec.
- `CalibrationRun`: one historical measurement (kept in a sibling history file
  for audit; not used at runtime by other components).
- `VoiceProfileStore`: load/save the active `VoiceProfile` JSON.
- `WavInspector`: report the exact duration of a WAV in seconds using the
  stdlib `wave` module.
- `TTSEngine`: synthesize text to a WAV. For Phase 1 the full atomic-write
  interface is written (so Phase 5 reuses it unchanged).
- `Calibrator`: load reference prose → synthesize → inspect duration → compute
  `chars_per_sec` → persist.

## M3 — Relationships
```
Calibrator ──► TTSEngine ──► Kokoro
   │              │
   │              └─► WAV on disk
   ├──► WavInspector ──► duration seconds
   └──► VoiceProfileStore ──► config JSON
```

## M4 — Interface & implementation sketches

### VoiceProfile (dataclass, frozen)
```
voice_id: str
speed: float
chars_per_second: float
calibrated_at: str  # ISO-8601 UTC
calibration_source_chars: int
```

### CalibrationRun (dataclass, frozen)
Same shape as VoiceProfile plus `measured_duration_seconds`.

### VoiceProfileStore
- `load() -> VoiceProfile | None`
- `save(profile: VoiceProfile) -> None` (atomic: temp file + rename)
- Path: `<project_root>/config/voice_calibration.json`.

### WavInspector
- `duration_seconds(path: Path) -> float`

### TTSEngine (interface lock-in; full impl Phase 5)
- `__init__(model_id="mlx-community/Kokoro-82M-bf16")`
- `synthesize(text, voice_profile, out_path) -> None` writes WAV atomically.
- Internal: lazy-load model on first call; reuse thereafter.
- Atomicity: write to `out_path.with_suffix('.partial.wav')`, then `os.replace`.

### Calibrator
- `measure(voice_id, speed=1.0, reference_text_path=None) -> VoiceProfile`
- Reference default: `tests/reference/calibration_prose.txt`.

## Sanity bounds
After measurement, assert `10.0 <= chars_per_second <= 30.0` as a reasonableness
check. Outside the band → raise `CalibrationError` with the measurement so the
user can investigate.

## Reference prose
About 500 characters of neutral-register English with mixed sentence lengths,
no numbers requiring expansion (since we haven't tested Kokoro's number
handling yet — a deliberate bias toward measurement stability).

## Check gate
`python -m plugin.scripts.python.calibrator` runs successfully and prints the
computed chars/sec in the sanity band; the JSON config file is present and
parseable.

## Notes
- Constants live in `config_constants.py` (Phase 7). For Phase 1 we inline
  the defaults (`DEFAULT_VOICE_ID = "af_heart"`, `DEFAULT_SPEED = 1.0`); Phase
  7 extraction is pure refactor.
- The `TTSEngine` written in this phase is the final Phase 5 version; writing
  it here means Phase 5 becomes "add `SegmentProducer` around it" rather than
  "write TTSEngine from scratch."
