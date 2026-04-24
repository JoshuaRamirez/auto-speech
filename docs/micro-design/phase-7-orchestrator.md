# Phase 7 Micro-Design — Pipeline Orchestrator

## Scope
Wire every service for one `/speak` invocation, including:
- Short-path decision
- Tmpdir lifecycle
- Thread lifecycle
- Cleanup
- Exit-code mapping

## M1 — Classes
- `config_constants` (module): hold tunables
- `ShortPathStrategy`: decide + execute short path
- `PipelineOrchestrator`: full L4 wiring
- `speak.py`: CLI entry

## M2 — Semantics

### ShortPathStrategy
```
def should_use(transcript, voice_profile, short_threshold_seconds) -> bool
def execute(transcript, voice_profile, tts_engine, tmpdir, stop_event) -> int
```
Decision: `estimate_seconds(transcript.char_count, voice_profile) <= threshold`.
Execution: one synth + one afplay; return 0 on success, nonzero on error.

### PipelineOrchestrator
Takes:
- `transcript_text: str` (the AUDIO-FRIENDLY text — rewrite already done)
- `turn_ordinal: int` (for logging only in this variant; actual selection
  happens in the slash command body)
- Keep-artifacts flag

Run sequence:
1. Load VoiceProfile (fallback to conservative default if missing).
2. Build AudioTranscript.
3. Short-path decide.
4. Create tmpdir.
5. If short: ShortPathStrategy.execute.
6. Else: plan → spawn threads → join → cleanup.
7. Map exceptions to exit codes per interface-contracts.md.

### speak.py
Arg parse:
- `--ordinal N` (default 1) — logging only here
- `--keep-artifacts` — don't delete tmpdir on success
- Reads the rewritten text from **stdin**. Always stdin — that's the
  contract with the slash command.

## Exit codes (from interface-contracts.md)
- 0 — success
- 4 — rewrite/text failure (empty stdin)
- 5 — TTS failure
- 6 — playback failure
- 130 — KeyboardInterrupt

(2/3 belong to the slash-command prelude, not speak.py.)

## M3 — Relationships
Central hub. Calls everything.

## M4 — Key decisions

- **Profile fallback:** if `config/voice_calibration.json` is absent, use
  hardcoded `VoiceProfile(voice_id="af_heart", speed=1.0, chars_per_second=15.0, ...)`
  with a prominent warning. This covers the "first-run before calibration" case.
- **Tmpdir cleanup:** on success AND no `--keep-artifacts`, remove tmpdir.
  On error, print the path and leave it. The user can rerun with
  `--keep-artifacts` to always preserve for debugging.
- **Thread join order:** join producer first, then consumer. If producer
  errored, `stop_event` is set → consumer drains quickly.
- **Stdin empty:** exit 4 immediately.

## Check gate
- `echo "Hello world." | .venv/bin/python plugin/scripts/python/speak.py`
  plays "Hello world." and returns 0.
- A longer stdin (≥ 300 chars) triggers the Fibonacci path; multiple
  chunks audible in order.
