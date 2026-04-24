# Retrospective — auto-speech v0.1

Closing note after Phase 9 completed successfully.

## What landed

- **Specification**: conceptualization, analysis, design, glossary, CRC
  class inventory, interface contracts, six ADRs, five Mermaid diagrams.
- **Implementation plan**: ten phases (0–9) each with an inline OOAD
  micro-process stub (identify → semantics → relationships → interface).
- **Code**: 27 Python files (one class per file), 3 shell scripts,
  1 slash command, 1 plugin manifest. All phases' check gates passed.
- **Measured calibration**: `af_heart` at speed 1.0 = **15.84 chars/sec**
  on this machine. Fibonacci base duration left at `N = 4 s`.
- **Live runs**: short path and Fibonacci long path both produced
  audible end-to-end output with artifacts retained under
  `tests/outputs/`.

## What took longer than expected

1. **mlx-audio soft-dependency chain.** `mlx-audio` declared only a thin
   dep list; `misaki` needed `num2words` and `spacy` as runtime
   requirements that the install didn't pull transitively. Installer
   now explicitly pulls `misaki[en]` + `num2words` + the `en_core_web_sm`
   spacy model.
2. **Stdout contract discovery.** The extractor's stdout was polluted
   by `TranscriptLocator`'s info lines until I moved them to stderr.
   Lesson: any CLI whose stdout is pipeline data must have every log
   path go to stderr from day one.

## Surprises (in a good way)

1. **Generation speed.** Kokoro on M-series Apple Silicon generates
   ~20–30× realtime for short texts and ~5–7× realtime for longer
   ones. The Fibonacci buffer is massively over-provisioned, which is
   exactly the comfort margin we want.
2. **Short-path threshold.** 15 s turned out to be the sweet spot for
   "single-shot" cutoff: anything shorter than that, the Fibonacci
   scheme's bookkeeping dominates the wait.
3. **Boundary-snapper behavior.** On 30 random-prose trials, the
   snapper never once landed mid-word or dropped a character.

## Open items (not blocking v0.1)

1. **Latency measurement.** The instrumentation in Phase 9 used
   `subprocess.Popen` + `for line in proc.stdout` which, due to
   output buffering, misreported first-audio latency. Replace with
   `bufsize=1, text=True` and explicit `proc.stdout.readline()` when a
   `--measure-latency` mode is added.
2. **Pre-warm daemon.** First invocation after login always pays the
   Kokoro cold-load cost (~2.5 s). A small always-on process that
   keeps the model hot would drop first-audio latency closer to 1 s.
   Out of scope for v0.1.
3. **Voice catalog.** Only `af_heart` is tested. Phase-1 calibration
   is voice-specific; a "switch-voice" command would need to
   recalibrate before the first speak invocation with the new voice.
4. **Marketplace packaging.** The current install uses a user-level
   command symlink. Proper plugin marketplace packaging is future work.
5. **Slash command end-to-end in a live session.** The `/speak`
   command was installed via `setup/install-plugin.sh` and the
   extract+rewrite+speak flow was exercised manually. A live
   `/speak` test in a fresh Claude Code session is the next smoke
   test; it cannot be run from inside the session that installed it.

## Lessons (captured for future projects)

1. **Always split stdout from stderr on CLIs that pipe.** Non-negotiable
   from day one.
2. **`write-to-temp-then-rename` is worth the 3 lines of code.** The
   `PlaybackQueue` never saw a partial WAV because of this pattern.
3. **Fibonacci is overkill for fast-generation TTS, and that's the
   point.** A system designed to tolerate slow generation costs
   nothing when generation is fast — you just get an even bigger
   safety margin.
4. **Measure before you tune.** The calibration doc existed before a
   single WAV was generated; once measured, the planner's math was
   exact on the first try.
