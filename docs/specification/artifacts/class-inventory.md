# Class Inventory (CRC-style)

Each entry: **Responsibility** (what it does) and **Collaborators** (who it talks
to). One class per file per the user's standing rule. File names in
`plugin/scripts/python/` unless noted.

---

## L1 — I/O adapters

### `TranscriptReader` (`transcript_reader.py`)
**Responsibility:** Stream a JSONL file and yield parsed turn dicts.
**Collaborators:** Standard `json` module. No domain knowledge — returns raw dicts.

### `TTSEngine` (`tts_engine.py`)
**Responsibility:** Synthesize a WAV for a given text using Kokoro via mlx-audio.
Manages model load lifecycle (lazy-load on first call, reuse thereafter).
**Collaborators:** `mlx_audio` Python API (or its CLI); `VoiceProfile` for voice/speed.
**Contract:** `synthesize(text: str, voice_profile: VoiceProfile, out_path: Path) → None`.
Writes completed WAV via temp-file-then-rename.

### `AfplayLauncher` (`afplay_launcher.py`)
**Responsibility:** Spawn `afplay` for a single WAV and block until it exits.
Kill-on-cancel support via `stop_event`.
**Collaborators:** `subprocess` stdlib.

### `WavInspector` (`wav_inspector.py`)
**Responsibility:** Read a WAV header + samples to return exact duration in seconds.
Used by `Calibrator` and for observability.
**Collaborators:** `wave` stdlib.

---

## L2 — Domain primitives

### `FibonacciSeq` (`fibonacci.py`)
**Responsibility:** Emit `F(k)` for `k = 1, 2, …` on demand. Stateless generator
class; tiny and pure.
**Collaborators:** None.

### `BoundarySnapper` (`boundary_snapper.py`)
**Responsibility:** Given a text and a target character offset, return the
snapped offset at the strongest boundary within `[start, start + target × (1 + tol)]`.
Uses a priority list: paragraph → sentence → clause → word.
**Collaborators:** None (pure string ops).

### `DurationEstimator` (`duration_estimator.py`)
**Responsibility:** Convert character count → estimated seconds using
`chars_per_second` from the active `VoiceProfile`.
**Collaborators:** `VoiceProfile` (read-only).

---

## L3 — Domain services

### `TranscriptLocator` (`transcript_locator.py`)
**Responsibility:** Resolve the JSONL path of the current Claude Code session.
Strategy: inspect the environment for `CLAUDE_PROJECT_DIR` / session id if
available; otherwise find the newest JSONL under `~/.claude/projects/<slug>/`
where `<slug>` is derived from the current working directory.
**Collaborators:** `os.environ`, filesystem.
**Output:** `Path`.

### `MessageSelector` (`message_selector.py`)
**Responsibility:** Given a stream of parsed turns and an ordinal `n ≥ 1`, pick
the nth-most-recent turn that qualifies as an `AssistantMessage` (role == assistant,
contains non-empty text block).
**Collaborators:** `TranscriptReader`; `AssistantMessage` value.
**Output:** `AssistantMessage`.

### `Calibrator` (`calibrator.py`)
**Responsibility:** Load the reference prose, run TTS, measure WAV duration,
compute `chars_per_second`, persist `VoiceProfile`, append a `CalibrationRun`.
**Collaborators:** `TTSEngine`, `WavInspector`, `VoiceProfileStore`.

### `ChunkPlanner` (`chunk_planner.py`)
**Responsibility:** Decompose an `AudioTranscript` into a `ChunkPlan` using
`FibonacciSeq` for target sizes and `BoundarySnapper` for cut points.
**Collaborators:** `FibonacciSeq`, `BoundarySnapper`, `DurationEstimator`,
`VoiceProfile`, `ChunkDescriptor`.
**Output:** `ChunkPlan`.

### `SegmentProducer` (`segment_producer.py`)
**Responsibility:** Consume a `ChunkPlan`, drive `TTSEngine` per descriptor,
enqueue `AudioSegment`s on the `PlaybackQueue`. Honors `stop_event`.
**Collaborators:** `TTSEngine`, `PlaybackQueue`, `AudioSegment`, `WavInspector`.

### `PlaybackConsumer` (`playback_consumer.py`)
**Responsibility:** Dequeue `AudioSegment`s until `SENTINEL`, play each via
`AfplayLauncher` in order, honor `stop_event`.
**Collaborators:** `PlaybackQueue`, `AfplayLauncher`.

---

## L4 — Orchestration

### `PipelineOrchestrator` (`pipeline.py`)
**Responsibility:** Wire all services for one `/speak` invocation. Decides
short-vs-long path via `ShortPathStrategy`. Owns the `stop_event`, `tmpdir`,
thread lifecycles, and final cleanup.
**Collaborators:** everything in L3 + L5 entry.

### `ShortPathStrategy` (`short_path.py`)
**Responsibility:** Decide short vs long; when short, do in-line gen + play.
**Collaborators:** `DurationEstimator`, `TTSEngine`, `AfplayLauncher`.

---

## L5 — Entry

### `speak.py` (`speak.py`)
**Responsibility:** CLI entry. Parses `--n`, instantiates `PipelineOrchestrator`,
returns exit code.
**Collaborators:** `PipelineOrchestrator`.

### Slash command (`plugin/commands/speak.md`)
**Responsibility:** Parse user arg, produce the audio-friendly rewrite inline,
then invoke `speak.py` with the rewrite text and target ordinal.

---

## Data classes (one file each, all under `plugin/scripts/python/`)

| Class | File |
|---|---|
| `AssistantMessage` | `assistant_message.py` |
| `AudioTranscript` | `audio_transcript.py` |
| `ChunkDescriptor` | `chunk_descriptor.py` |
| `ChunkPlan` | `chunk_plan.py` |
| `AudioSegment` | `audio_segment.py` |
| `VoiceProfile` | `voice_profile.py` |
| `VoiceProfileStore` | `voice_profile_store.py` |
| `CalibrationRun` | `calibration_run.py` |
| `PlaybackQueue` (thin wrapper over `queue.Queue`) | `playback_queue.py` |

---

## Constants (shared)

### `config_constants.py`
```python
BASE_DURATION_SECONDS = 4.0
SHORT_THRESHOLD_SECONDS = 15.0
BOUNDARY_TOLERANCE = 0.25        # ±25% around Fibonacci target
DEFAULT_VOICE_ID = "af_heart"
DEFAULT_SPEED = 1.0
FALLBACK_CHARS_PER_SEC = 15.0
QUEUE_CAPACITY = 3
```
