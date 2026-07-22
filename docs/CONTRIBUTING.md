# Contributing

auto-speech is a Claude Code plugin written in Python 3.12 + Bash, targeting
macOS / Apple Silicon. This is the developer's how-to: tests, lint, CI, and
the dependency-lock workflow.

## Setup

```
bash setup/install.sh        # uv sync from the committed lock + spaCy model
```

This creates `.venv` from `pyproject.toml` + `uv.lock` (reproducible). It is
non-destructive: if `mlx-lm` is already installed it keeps the `narrate`
extra so a base sync won't prune your narration capability.

## Tests

No pytest — tests are plain scripts with a `main()` that exits non-zero on
failure. The runner:

```
bash tests/run_all.sh            # full suite (python + shell + web + audio/TTS)
bash tests/run_all.sh --hermetic # only dependency-free tests (the CI subset)
bash tests/run_all.sh --web      # only web-server tests (Flask + numpy, no MLX)
```

- **Hermetic** tests import only the standard library + plugin modules and
  run on a bare interpreter (no MLX, mpv, numpy, or audio device). New tests
  are included automatically; a test that needs runtime deps goes in the
  `NEEDS_DEPS` denylist in `run_all.sh`.
- **Web** tests exercise the Flask server with MLX stubbed; they need only
  `flask` + `numpy`, so CI runs them in a light venv (`WEB` list in
  `run_all.sh`). A web test also goes in `NEEDS_DEPS` so hermetic skips it.
- Override the interpreter with `AUTO_SPEECH_TEST_PYTHON` (CI points it at a
  bare `uv venv` so the darwin-only lock never has to `uv sync` on Linux):

```
uv venv --python 3.12 /tmp/bare
AUTO_SPEECH_TEST_PYTHON=/tmp/bare/bin/python bash tests/run_all.sh --hermetic
```

Test conventions: one `test_*.py` per module, a `main()` listing the cases,
each printing `  ok  <name>`; collaborators/system probes are injected so the
test touches no real audio, LLM, daemon, or `/tmp` state.

## Lint

```
uvx ruff check plugin/scripts/python tests
shellcheck -S warning plugin/scripts/shell/*.sh setup/*.sh tests/*.sh
```

Both must be clean. Ruff config is in `pyproject.toml` (`[tool.ruff]`).

## CI

`.github/workflows/ci.yml` runs on a **macOS** runner (matching the target
platform) and gates every push/PR on five checks, without installing
the multi-GB darwin-only runtime deps:

1. `ruff check`
2. `shellcheck -S warning`
3. `uv lock --check` (lockfile in sync with `pyproject.toml`)
4. the **hermetic** test subset on a bare Python 3.12
5. the **web** test lane on a light venv (`flask` + `numpy` only)

Reproduce CI locally by running those five commands.

## Dependencies & the lockfile

Dependencies are declared in `pyproject.toml` and pinned in `uv.lock`
(darwin-scoped — a universal lock fails on the macOS-only MLX wheels).

- Add/upgrade a dependency: edit `pyproject.toml`, run `uv lock`, commit
  both. `tests/test_deps_locked.sh` (and CI) fail if they drift.
- `mlx-lm` lives in the `narrate` optional extra so narration is locked too
  without forcing it into the base install.

## Conventions

- One class per file; prefer many small classes over large ones.
- Name the actual constraint (e.g. "no environment I/O", "stateless"), not
  web shorthand.
- Commit with the repo's Conventional Commits + emoji style; stage by named
  files; keep commits small.
- Merge, never rewrite history; never force-push.

## Known deferred work

Two items from the hardening pass were intentionally left undone:

- **End-to-end integration test** (full Stop-hook → autoplay worker
  → cli_rewrite → speak.py → mpv → audible WAV). Doing this properly
  requires a fake `claude` binary on PATH AND a mock mpv that records
  start invocations without actually playing audio. The current
  suite covers each link in the chain individually, so the
  marginal value of a true end-to-end run is low relative to the
  fixture cost. Re-evaluate if a future regression spans multiple
  components.
- **`REASON` phase category** is declared in
  `narrator_phase_classifier.Category` and the MockSummarizer's verb
  tables, but never assigned. The intent was to narrate assistant
  text-only chunks (no tool call) as a "thinking" phase. Claude Code
  doesn't emit such chunks through any of the hook events we use
  (PreToolUse / PostToolUse / Stop / UserPromptSubmit), so wiring
  the category needs a different signal source — e.g., tailing the
  transcript JSONL — which we don't currently have a clean place
  for. The category is left in the enum so a future implementation
  doesn't need a migration.
