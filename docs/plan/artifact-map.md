# Artifact Map

Master inventory of every artifact this project produces. Organized by origin phase
of the OOAD macro process, then by implementation phase for the micro process.

---

## OOAD Macro Process Artifacts (Specification)

### Conceptualization
| Artifact | Path | Produced by | State |
|---|---|---|---|
| Vision & problem statement | `docs/specification/01-conceptualization.md` | macro | planned |
| Context diagram | `docs/specification/diagrams/context-diagram.mmd` | macro | planned |

### Analysis
| Artifact | Path | Produced by | State |
|---|---|---|---|
| Analysis narrative | `docs/specification/02-analysis.md` | macro | planned |
| Use cases | `docs/specification/artifacts/use-cases.md` | macro | planned |
| Domain model narrative | `docs/specification/artifacts/domain-model.md` | macro | planned |
| Domain model diagram | `docs/specification/diagrams/domain-model.mmd` | macro | planned |
| Glossary (ubiquitous language) | `docs/specification/glossary.md` | macro | planned |

### Design
| Artifact | Path | Produced by | State |
|---|---|---|---|
| Design narrative | `docs/specification/03-design.md` | macro | planned |
| Component architecture diagram | `docs/specification/diagrams/component-architecture.mmd` | macro | planned |
| Pipeline sequence diagram | `docs/specification/diagrams/sequence-pipeline.mmd` | macro | planned |
| Fibonacci-scheduling activity diagram | `docs/specification/diagrams/activity-fibonacci.mmd` | macro | planned |
| Playback queue state diagram | `docs/specification/diagrams/state-playback.mmd` | macro | planned |
| Class inventory (CRC-style) | `docs/specification/artifacts/class-inventory.md` | macro | planned |
| Interface contracts | `docs/specification/artifacts/interface-contracts.md` | macro | planned |

### Architecture Decision Records
| ADR | Path |
|---|---|
| 001 — TTS engine selection (Kokoro via mlx-audio) | `docs/decisions/ADR-001-tts-engine-selection.md` |
| 002 — Fibonacci-scaled buffered playback | `docs/decisions/ADR-002-fibonacci-buffered-playback.md` |
| 003 — Transcript source & extraction strategy | `docs/decisions/ADR-003-transcript-source.md` |
| 004 — Boundary-snapping heuristic for chunk planner | `docs/decisions/ADR-004-boundary-snapping.md` |
| 005 — Audio-friendly rewrite via Claude (not rule-based) | `docs/decisions/ADR-005-audio-rewrite-strategy.md` |
| 006 — Producer/consumer concurrency via file-queue | `docs/decisions/ADR-006-file-queue-concurrency.md` |

---

## Plan Artifacts

| Artifact | Path |
|---|---|
| Implementation plan (phased) | `docs/plan/implementation-plan.md` |
| Phase breakdown | `docs/plan/phase-breakdown.md` |
| Artifact map (this file) | `docs/plan/artifact-map.md` |
| Risk register | `docs/plan/risk-register.md` |

---

## OOAD Micro Process Artifacts (per-phase, produced during execution)

Each phase invokes the micro process at the relevant abstraction level:
1. Identify classes/objects at this level
2. Identify their semantics (responsibilities)
3. Identify relationships
4. Specify interfaces & implementation

Per-phase micro-design documents live in `docs/micro-design/`.

| Phase | Micro-design doc | Implementation artifacts |
|---|---|---|
| 0 — Environment setup | `docs/micro-design/phase-0-environment.md` | `setup/install.sh`, `setup/verify.sh` |
| 1 — Voice calibration | `docs/micro-design/phase-1-calibration.md` | `plugin/scripts/python/calibrator.py`, `plugin/scripts/python/voice_profile.py`, `tests/reference/calibration_prose.txt`, `config/voice_calibration.json` |
| 2 — Transcript extraction | `docs/micro-design/phase-2-extraction.md` | `plugin/scripts/python/transcript_locator.py`, `plugin/scripts/python/transcript_reader.py`, `plugin/scripts/python/assistant_message.py`, `plugin/scripts/python/message_selector.py` |
| 3 — Audio-friendly rewriter | `docs/micro-design/phase-3-rewriter.md` | prompt embedded in `plugin/commands/speak.md`; optional `plugin/scripts/python/rewrite_spec.md` (prompt contract) |
| 4 — Fibonacci chunk planner | `docs/micro-design/phase-4-chunk-planner.md` | `plugin/scripts/python/fibonacci.py`, `plugin/scripts/python/boundary_snapper.py`, `plugin/scripts/python/chunk_descriptor.py`, `plugin/scripts/python/chunk_planner.py` |
| 5 — TTS engine wrapper | `docs/micro-design/phase-5-tts-engine.md` | `plugin/scripts/python/tts_engine.py`, `plugin/scripts/python/audio_segment.py`, `plugin/scripts/python/segment_producer.py` |
| 6 — Playback consumer | `docs/micro-design/phase-6-playback.md` | `plugin/scripts/python/playback_queue.py`, `plugin/scripts/python/playback_consumer.py`, `plugin/scripts/shell/play_segment.sh` |
| 7 — Pipeline orchestrator | `docs/micro-design/phase-7-orchestrator.md` | `plugin/scripts/python/pipeline.py`, `plugin/scripts/python/short_path.py`, `plugin/scripts/python/speak.py` (entry) |
| 8 — Plugin integration | `docs/micro-design/phase-8-plugin.md` | `plugin/.claude-plugin/plugin.json`, `plugin/commands/speak.md` |
| 9 — End-to-end validation | `docs/micro-design/phase-9-e2e.md` | `tests/manual_test_plan.md`, `tests/outputs/*` (captured runs) |

---

## Naming & organization conventions
- One class per file (user preference). File name = snake_case of class name.
- Micro-design docs live beside the phase they belong to.
- All shell scripts go under `plugin/scripts/shell/`, all Python under `plugin/scripts/python/`.
- Configuration is never embedded in code; it lives under `config/`.
- Generated audio during live runs goes to a tmpdir, not checked in. Reference outputs
  captured during validation go under `tests/outputs/` with a timestamped subfolder.
