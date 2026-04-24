# Interface Contracts

The five stable interfaces. Every other internal surface is free to evolve.

---

## 1. `TTSEngine.synthesize`

```python
def synthesize(
    self,
    text: str,
    voice_profile: VoiceProfile,
    out_path: Path,
) -> None:
    """
    Write a WAV containing the spoken form of `text` to `out_path`.

    Pre:
      - text is non-empty.
      - voice_profile.voice_id is a voice installed with mlx-audio.
      - parent of out_path exists and is writable.

    Post (success):
      - out_path is a valid WAV (RIFF header + mono or stereo PCM).
      - out_path is atomically present — no partial writes visible.

    Raises:
      - TTSGenerationError on any synthesis failure (nonzero exit, import
        failure, model load failure). Callers must not try/except-swallow;
        pipeline must halt.
    """
```

**Atomicity rule:** write to `out_path.with_suffix('.partial.wav')`, flush,
close, then `os.replace` to `out_path`. `PlaybackQueue` consumers must only
ever see complete files.

---

## 2. `Calibrator.measure`

```python
def measure(
    self,
    voice_id: str,
    speed: float,
    reference_text_path: Path | None = None,
) -> VoiceProfile:
    """
    Calibrate chars_per_second for the given voice and speed.

    Pre:
      - TTSEngine is operational.

    Post:
      - Returns a VoiceProfile with chars_per_second > 0.
      - Persists it to config/voice_calibration.json (via VoiceProfileStore).
      - Appends a CalibrationRun to the calibration history.

    Raises:
      - CalibrationError if synthesis or WAV inspection fails.
    """
```

---

## 3. `ChunkPlanner.plan`

```python
def plan(
    self,
    transcript: AudioTranscript,
    voice_profile: VoiceProfile,
) -> ChunkPlan:
    """
    Produce a ChunkPlan for the given transcript.

    Pre:
      - transcript.text is non-empty.
      - voice_profile.chars_per_second > 0.

    Post (invariants):
      - Every descriptor in the returned plan has non-empty text.
      - Concatenating descriptor texts in order reconstructs transcript.text
        losslessly (character-identical except for whitespace trimmed at
        boundaries).
      - Except for the final descriptor, each descriptor's actual_char_count
        is within BOUNDARY_TOLERANCE of its target_char_count, OR the only
        available boundary within the tolerance window was accepted.
      - Descriptors are indexed 1..n in order.

    Note: This function is pure and deterministic. Given identical inputs it
    returns identical plans — tested with property-based checks.
    """
```

---

## 4. `PlaybackQueue`

```python
SENTINEL = object()  # module-level singleton

class PlaybackQueue:
    def put(self, segment: AudioSegment) -> None: ...
    def get(self, timeout: float | None = None) -> AudioSegment | object: ...
    def close(self) -> None:
        """Enqueue SENTINEL. Idempotent — safe to call multiple times."""
```

Shape is the stdlib `queue.Queue` shape plus the `close()` helper. Capacity
is `QUEUE_CAPACITY` from `config_constants.py`.

---

## 5. `PipelineOrchestrator.run`

```python
def run(self, turn_ordinal: int = 1) -> int:
    """
    Execute one /speak invocation end-to-end.

    Pre:
      - turn_ordinal >= 1.

    Post (exit codes):
      - 0 on clean completion (all audio played).
      - 2 on no-such-turn (UC-06).
      - 3 on transcript unreadable (UC-07).
      - 4 on rewrite failure.
      - 5 on TTS failure.
      - 6 on playback failure.
      - 130 on user interrupt (Ctrl+C).
    """
```

---

## Runtime-filled contract: the audio-friendly rewrite

This is not a Python API — it's a **prompt contract** that lives in the slash
command. The contract is:

> Given the text of an AssistantMessage, produce an AudioTranscript that a
> competent human could read aloud at a professional meeting with zero loss
> of information. Specifically:
> - Expand code blocks into spoken form: describe the language, describe what
>   the code does, and inline-quote any identifiers or short strings the reader
>   needs to hear verbatim. Do not read character-by-character.
> - Replace symbols with words where ambiguous (`->` → "arrow", `*` → "asterisk"
>   when it's punctuation, "times" when it's multiplication).
> - Spell out file paths and URLs.
> - Render tables as a sequence of clauses ("Row one: X is A, Y is B.").
> - Render lists naturally ("First, …. Second, …. Finally, ….").
> - Keep every example, caveat, number, and proper noun from the source.
> - Target natural spoken English rhythm. Prefer shorter sentences.
> - Output plain text only. No markdown, no bullets, no code fences.

The rewrite prompt appears verbatim in `plugin/commands/speak.md`.
