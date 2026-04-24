# Phase 11 Micro-Design — Source-hash Replay Cache + `/replay`

Implements [ADR-008](../decisions/ADR-008-source-hash-replay-cache.md).

## Scope
1. Persistent cache of successful runs keyed on the triple
   (source_text, voice_id, speed).
2. `speak.py` consults the cache before running the pipeline; on hit,
   plays the cached `full.wav` immediately.
3. `speak.py` promotes successful pipeline output to the cache on miss.
4. New `/replay [n]` slash command plays the most recent (or N-th
   most recent) cached run without touching the transcript.

## M1 — Classes at this level

- **`CacheEntry`** (value, frozen dataclass) — one cache entry's metadata.
- **`CacheStore`** (L3 service) — lookup, promote, and list entries.
- **`replay.py`** (L5 entry) — CLI for `/replay`, analogous to `speak.py`.

Edits to existing files:
- `speak.py` — accept `--source-hash`, consult cache, short-circuit on hit.
- `pipeline.py` — promote to cache on successful miss-run.
- `plugin/commands/speak.md` — compute hash in bash; pass flag.
- `plugin/scripts/shell/run_replay.sh` — venv-activating wrapper.
- `plugin/commands/replay.md` — new slash command.

## M2 — Semantics

### `CacheEntry`
```
source_hash: str          # full 64-hex-char SHA-256
voice_id: str
speed: float
char_count: int
duration_seconds: float
created_at: str           # ISO-8601 UTC, seconds precision, trailing "Z"
chars_per_second_at_creation: float
```
Constructed from `meta.json` (class methods `to_dict` / `from_dict`).
Frozen; equality by value.

### `CacheStore`
Single responsibility: own the layout at `config/cache/<hash_prefix>/`.

- `__init__(root: Path)` — root defaults to `<project>/config/cache`.
- `lookup(source_hash: str) -> tuple[Path, CacheEntry] | None` — returns
  `(full_wav_path, entry)` iff the cache dir exists AND contains a
  non-empty `full.wav` AND a parseable `meta.json`. Any deviation →
  `None` (caller treats as miss; orphan is not auto-healed in v0.1).
- `promote(source_hash: str, source_full_wav: Path, entry: CacheEntry)
    -> Path` — moves the WAV into the cache dir (rename within FS) and
  writes `meta.json` alongside (atomic `tmp → replace`). Returns the
  final path. Raises `CachePromotionError` on failure.
- `list_by_recency() -> list[tuple[Path, CacheEntry]]` — scans cache
  dirs, returns pairs sorted by `entry.created_at` descending.
  Orphan dirs (no/bad `meta.json`) are skipped and logged.
- `path_for(source_hash: str) -> Path` — returns `root/<hash_prefix>/`.
- **Prefix length:** first 16 hex chars of the SHA-256.

### `replay.py` (entry)
CLI: `replay.py [--ordinal N] [--keep-artifacts]`. Semantics:
1. Load `CacheStore`.
2. List by recency. If fewer than `N` entries → exit 2
   ("no-such-cache-entry"; same exit code family as no-such-turn).
3. Pick the `N`-th most recent. Play its `full.wav` via the existing
   `AfplayLauncher` (Phase 12 will swap for mpv).
4. Exit 0 on clean play, 6 on playback failure, 130 on SIGINT.
5. `--keep-artifacts` is accepted for symmetry but has no effect
   (there is no tmpdir to keep — the cached file is already the
   durable artifact).

### `speak.py` edits
New flag `--source-hash <hex>`. If provided:
- Before constructing `PipelineOrchestrator`, look up the cache via
  `CacheStore`.
- On hit: print a one-line status, play `full.wav` through
  `AfplayLauncher` with a `stop_event`. Exit 0 on success.
- On miss: run the pipeline normally, and pass `source_hash` into
  the orchestrator for promotion on success.

If `--source-hash` is absent: behavior is exactly today's — run,
no cache consult, no promote. Keeps `echo … | speak.py` usable for
one-off tests.

### `pipeline.py` edits
`PipelineOrchestrator.__init__` gains `source_hash: str | None = None`
and the accompanying voice/speed/char_count needed to build a
`CacheEntry`. On successful completion (exit 0), after concat:
- If `source_hash` is `None`, proceed with current cleanup behavior.
- Else: build a `CacheEntry` from the run's known values and call
  `CacheStore.promote(...)`. The promotion moves `full.wav` out of
  the tmpdir before the `rmtree` runs, so `rmtree` cleans up only
  the chunk remnants and any other scratch.

### Slash command edits — `plugin/commands/speak.md`
Between step 2 (extract) and step 3 (rewrite), a new sub-step:

```
HASH=$(printf '%s' "$SOURCE_TEXT" | shasum -a 256 | cut -d' ' -f1)
# plus reading voice_id, speed from config/voice_calibration.json
# → produce the key input (source_text \x00 voice_id:speed_str) and hash that
```

In practice, the slash command shells out to a tiny helper
`plugin/scripts/shell/compute_hash.sh` that does the concatenation
and hashing in a single call (cleaner heredoc handling). That helper
reads the config JSON for voice_id/speed. Slash command then passes
`--source-hash <hex>` to `run_speak.sh`.

## M3 — Relationships

```
/speak command ── run_extract.sh ──► source text
             ──── compute_hash.sh ──► hash
             ──── rewrite (Claude) ──► AUDIO_TEXT
             ──── run_speak.sh --source-hash HEX <<< AUDIO_TEXT

speak.py ── CacheStore.lookup(hash) ──► hit? play; else run pipeline
pipeline.py ── on success + hash ──► CacheStore.promote(...)

/replay command ── run_replay.sh ──► replay.py
replay.py ── CacheStore.list_by_recency() ──► pick N-th → afplay
```

No new L1/L2 adapters. `CacheStore` uses stdlib `json`, `shutil`,
`pathlib`, plus the existing `AfplayLauncher` for playback.

## M4 — Key interface contracts

### `CacheStore.lookup`
```python
def lookup(self, source_hash: str) -> tuple[Path, CacheEntry] | None:
    """Return (full_wav_path, entry) on complete cache hit; else None.

    Pre: source_hash is a 64-hex-char SHA-256 digest.
    Post-hit: full_wav_path points to an existing non-empty WAV AND
              entry.source_hash == source_hash.
    Post-miss: None returned; caller must not promote until a run succeeds.
    Never raises on plain cache-miss; raises only on malformed hash input.
    """
```

### `CacheStore.promote`
```python
def promote(
    self,
    source_hash: str,
    source_full_wav: Path,
    entry: CacheEntry,
) -> Path:
    """Move source_full_wav into the cache dir for source_hash; write meta.json.

    Atomicity: writes meta.json.partial + rename; uses os.rename for the WAV
    within the same filesystem. If cross-filesystem (rare: /tmp vs project
    disk), falls back to copy+unlink with a full-file temp name.

    Post-success: cache dir contains full.wav and meta.json; source_full_wav
                  no longer exists at the original path.
    Raises CachePromotionError on any IO failure; cache dir is left clean
           (partial artifacts removed).
    """
```

### `compute_hash.sh`
```sh
# args: none
# stdin: source text
# stdout: 64-hex-char SHA-256 hash
# reads voice_id, speed from config/voice_calibration.json
# constructs: source_bytes + 0x00 + voice_id + ":" + speed_str
# emits: shasum -a 256 of that, first field only
```

## Failure modes

| Mode | Handling |
|---|---|
| Malformed `--source-hash` | Reject at argparse; exit 2 (usage). |
| Cache dir exists but `meta.json` is missing or invalid | Treated as miss; logged to stderr as an orphan. |
| Cache dir exists but `full.wav` is zero-bytes | Treated as miss; same stderr note. |
| Promotion fails (disk full, filesystem boundary without fallback, etc.) | Exit 5 (family: system cannot save — "TTS-ish failure surface"). Run's audio still played on its way through; user hears it, but next run won't hit cache. Stderr logs the failure. |
| `/replay` with no cache entries present | Exit 2 with a clear message: "no cache entries found; run /speak at least once first." |
| `/replay` ordinal exceeds cache count | Same exit 2, message names the available count. |

## Invariants introduced (beyond ADR-008's)

- **I-11.5 Promote-before-rmtree.** In the cache-promotion code path,
  `os.rename` of `full.wav` precedes the tmpdir `rmtree`. If the rename
  fails the rmtree is skipped and the tmpdir is preserved for inspection.
- **I-11.6 No partial cache entries.** A cache dir either contains both
  `full.wav` and `meta.json` atomically (after promotion), or is absent.
  Partial states are transient and cleaned up in the same promote call.
- **I-11.7 Replay idempotency.** Two consecutive `/replay` invocations
  yield identical audio output (the cached `full.wav` is immutable).

## Check gate

1. **Miss → promote.** Run `/speak` on a given message. Inspect
   `config/cache/` — a new dir named with the expected 16-hex prefix
   contains `full.wav` and `meta.json`. `meta.json` parses cleanly and
   matches the known `char_count` and `voice_id`.
2. **Hit.** Run `/speak` on the same message again. Stderr logs
   "cache hit" and no Kokoro model load occurs; the audio plays.
3. **/replay most recent.** `/replay` plays the most recently cached
   entry (the one created in step 2 — same as step 1's entry, since
   step 2 hit rather than created).
4. **/replay N.** After creating ≥ 3 cache entries from three different
   messages, `/replay 2` plays the second-most-recent.
5. **Ad-hoc still works.** `echo "..." | run_speak.sh` (no
   `--source-hash`) runs the pipeline without consulting or promoting
   to cache — backwards compatible.
6. **Voice change invalidates.** Recalibrate with a synthetic voice
   override, rerun `/speak` on the same source — get a new cache dir,
   not a hit on the prior entry. (Manual; no test harness required
   since this just reads config.)

## Out of scope for this phase
- Cache eviction / LRU.
- `auto-speech cache list` / `cache clean` management commands.
- mpv-based seekable playback — Phase 12 (ADR-009). The cached WAV is
  the uniform artifact both `/speak` and `/replay` hand to whatever
  the playback engine is at the time.
