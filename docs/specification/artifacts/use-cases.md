# Use Cases

## UC-01: Speak the last assistant message

**Actor:** User.
**Trigger:** User types `/speak` in Claude Code.
**Preconditions:**
- A VoiceProfile exists (calibrated at least once), OR a default fallback is available.
- At least one assistant message with non-empty text content exists in the current session transcript.

**Main flow:**
1. The slash command resolves to the orchestrator.
2. Orchestrator locates the current session's JSONL transcript.
3. Orchestrator selects the most recent qualifying `AssistantMessage`.
4. Orchestrator obtains an `AudioTranscript` via the rewrite pass (delegated to Claude with the rewrite-prompt contract).
5. If estimated duration ≤ `SHORT_THRESHOLD`, take UC-03 short path; else continue.
6. Orchestrator asks `ChunkPlanner` for a `ChunkPlan`.
7. Orchestrator launches `SegmentProducer` and `PlaybackConsumer` concurrently.
8. Producer generates segments in Fibonacci order.
9. Consumer plays segments in order as they appear.
10. When the final segment finishes playing, orchestrator terminates.

**Postconditions:**
- All generated WAV files under the run-specific tmpdir are cleaned up.
- No orphan processes remain.

**Exceptions:** UC-06, UC-07.

---

## UC-02: Speak the Nth-most-recent assistant message

**Actor:** User.
**Trigger:** User types `/speak n` where `n` is an integer ≥ 1.
**Differences from UC-01:**
- Step 3 selects the `n`th-most-recent qualifying `AssistantMessage` instead of the 1st.
- UC-06 fires if the transcript has fewer than `n` qualifying messages.

---

## UC-03: Short-response fast path

**Trigger:** Internal — invoked from UC-01 step 5 when estimated audio duration is short.
**Rationale:** For a ≤ 15 s response, the buffered scheme's overhead (subprocesses, file I/O, seams) exceeds the wait it saves.

**Flow:**
1. Orchestrator generates a single `AudioSegment` for the full `AudioTranscript`.
2. Plays it via `afplay`.
3. Cleans up.

---

## UC-04: Voice calibration

**Actor:** User (via `setup/install.sh`) or triggered on-demand.
**Trigger:** Fresh install, voice changed, or explicit `--recalibrate`.
**Preconditions:** mlx-audio + Kokoro installed.

**Flow:**
1. `Calibrator` loads the reference text (`tests/reference/calibration_prose.txt`).
2. Runs the TTS engine to produce a WAV.
3. Measures WAV duration.
4. Computes `chars_per_second = len(reference_text) / duration_seconds`.
5. Writes/updates `config/voice_calibration.json` with voice ID, speed, value, timestamp.

**Postconditions:** A valid `VoiceProfile` is loadable by subsequent runs.

---

## UC-05 (exception): No calibration present

**Handling:** Use a conservative built-in default (`15 chars/s` at speed 1.0 for
the default voice) and emit a visible warning suggesting recalibration. The fallback
is deliberately conservative (underestimating speed) so that chunks err slightly
long rather than short.

---

## UC-06 (exception): Requested index exceeds available assistant turns

**Handling:** Fail loud. Exit with a clear message of the form
`"only K qualifying assistant messages available; you asked for the Nth"`.
Do not play anything.

---

## UC-07 (exception): Transcript malformed or unreadable

**Handling:** Fail loud. Exit with the underlying I/O or parse error.
Do not attempt partial recovery; do not play anything.
