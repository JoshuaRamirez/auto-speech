# Phase 0 Micro-Design — Environment

## Scope
Install mlx-audio + Kokoro weights into a project-owned venv and verify.

## M1 — Classes/objects at this level
None (this phase is entirely scripts and external dependencies).

## M2 — Semantics
- `install.sh` — one-shot installer: creates venv with uv, installs deps,
  warms Kokoro weights, writes a `.installed` sentinel.
- `verify.sh` — runs a minimal Python one-liner against the venv that synthesizes
  a known string and writes a WAV. Plays the WAV with `afplay` so the human
  confirms audibility.

## M3 — Relationships
- `install.sh` depends on system `uv`. uv will fetch Python 3.12 if not present
  (Python 3.14 works but mlx-audio dep pins — especially `numba`/`librosa` —
  lag behind; 3.12 is safer).
- `verify.sh` depends on `install.sh` having run; it looks for
  `setup/.installed`.

## M4 — Interface & implementation

### `setup/install.sh`
```
- Use uv to create a venv at `.venv/` pinned to Python 3.12
- Install: mlx-audio, misaki (for American English phonemes)
- Touch `.installed` sentinel
- Emit next-step hint: run verify.sh
```

### `setup/verify.sh`
```
- Activate venv
- Run an inline Python that:
    load_model("mlx-community/Kokoro-82M-bf16")
    iterate generate(text="Verification successful.", voice="af_heart", speed=1.0, lang_code="a")
    concatenate .audio arrays
    write WAV at 24000 Hz mono
- afplay the WAV
```

## Decisions
- **Python 3.12 pinned** (not 3.14) — mlx-audio has better wheel availability.
- **Venv lives at project root `.venv/`** — keeps the plugin self-contained.
- **Sample rate 24000 Hz** — Kokoro's native rate.

## Check gate
`setup/verify.sh` produces an audible "Verification successful." WAV. The file
exists at a known path and afplay exits 0.

## Notes for adjacent code stabilization
- Once Phase 0 is stable, add docstrings to any phase-0 helpers. Currently
  only shell scripts, so nothing to docstring.
- The venv Python path (`.venv/bin/python`) becomes the canonical interpreter
  for every subsequent phase's scripts. Phase 8 plugin integration will use
  this path.
