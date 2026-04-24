# ADR-008 — Source-hash replay cache

**Status:** Accepted
**Date:** 2026-04-23
**Depends on:** [ADR-007](ADR-007-mandatory-concat-and-cache-centric-artifact.md).

## Context
A `/speak` run currently regenerates audio every time, even for a message
the user has already listened to in an earlier run. TTS generation is
cheap on this machine (Kokoro at ~5–30× realtime), but not free — a
5-minute response takes ~10 s to generate cold and ~2 s warm, plus
Python/venv startup. Replay should be instant.

The rewrite step is probabilistic: Claude's rewrite of the same source
text may vary between invocations. A cache keyed on rewrite text would
miss on unimportant wording differences. A cache keyed on source text
hits whenever the *input to the system* is identical.

## Decision

**Cache entries are keyed on a hash derived from (source_text, voice_id,
speed).** On a cache hit, the entire pipeline is bypassed — including
the rewrite pass — and the cached `full.wav` is played directly. On a
cache miss that completes successfully, the run's `full.wav` is promoted
from its tmpdir into the cache.

A separate slash command `/replay` plays the most-recently-created cache
entry *without consulting the transcript at all*.

## Key format

```
hash = sha256(source_text + "\x00" + voice_id + ":" + speed_str).hexdigest()
```

The cache directory name is the first 16 hex chars of `hash`
(64 bits — vastly more than enough for a single user's realistic entry
count). The full hash is stored in `meta.json` for verification and
debugging.

## Cache layout

```
config/cache/
├── a1b2c3d4e5f60123/
│   ├── full.wav
│   └── meta.json
├── 789a0b1c2d3e4f56/
│   ├── full.wav
│   └── meta.json
└── ...
```

`meta.json`:
```json
{
  "source_hash": "a1b2c3d4e5f6012389...",
  "voice_id": "af_heart",
  "speed": 1.0,
  "char_count": 3961,
  "duration_seconds": 294.7,
  "created_at": "2026-04-23T21:50:34Z",
  "chars_per_second_at_creation": 15.84
}
```

## Lookup / promotion / replay semantics

- **Lookup.** `speak.py` receives `--source-hash <hex>`. If the
  corresponding cache dir exists and contains a non-empty `full.wav`
  and a parseable `meta.json`, the run is a cache hit. Play
  `full.wav` via the existing playback consumer path (phase 12 later
  swaps this for mpv, and replay inherits that behavior automatically).
- **Promotion.** On a successful cache-miss run, after the concat step
  (ADR-007), the tmpdir's `full.wav` is atomically moved (rename
  within the same filesystem) into `config/cache/<hash_prefix>/`,
  and a `meta.json` is written alongside. Promotion preempts the
  orchestrator's default tmpdir rmtree.
- **Replay (`/replay` command).** The cache store lists entries sorted
  by `meta.json.created_at` (or by `full.wav` mtime if the JSON is
  missing, as a recovery mode). `/replay` plays the most recent;
  `/replay N` plays the Nth-most-recent.

## Rationale

1. **Rewrite-invariant.** Hashing the source protects us from Claude's
   rewrite-text variability. The same message always hits the same key.
2. **Voice-aware.** Including `voice_id` and `speed` in the hash input
   means a voice or speed change produces a different key, correctly
   forcing regeneration.
3. **No explicit eviction.** For a single user, the cache grows slowly
   (one entry per distinct-and-listened message). We document
   manual cleanup (`rm -rf config/cache`) and revisit if it ever matters.
4. **Decoupled from playback engine.** The cache stores a single
   `full.wav` — the same artifact the mpv controller (Phase 12) will
   operate on. Replay therefore automatically inherits any seek /
   pause capabilities added later.

## Alternatives considered

- **Cache by rewrite hash.** Rejected: misses trivial rewrite variations
  the user doesn't care about.
- **Full-run reproducibility seed.** Rejected: would require seeding
  Kokoro's generation (not straightforwardly exposed) and would not
  help anyway — the user wants the *same audio*, and the cache gives
  that directly.
- **Automatic LRU eviction at N entries.** Deferred: single-user,
  text-sized audio; not a real problem yet.
- **No cache; just `/replay` that keeps the last run.** Rejected:
  `/speak` on a different message between listens would discard the
  first. The keyed cache handles arbitrary interleaving.

## Consequences

- `speak.py` gains `--source-hash <hex>`. When present:
  - Cache hit → play the cached WAV via the normal playback path.
  - Cache miss → run the pipeline; promote on success.
  - Hash absent → treated as "always miss, never promote" (backwards-
    compatible with the previous behavior for ad-hoc `echo … | speak.py`).
- Two new data classes: `CacheEntry` (value) and `CacheStore` (service).
- A new slash command `/replay` with an argument for ordinal.
- The slash command `/speak` computes `source_hash` in bash from the
  extractor's stdout, plus the active `VoiceProfile` values, and passes
  `--source-hash <hex>` to `run_speak.sh`. The shell wrapper passes it
  through.
- No changes to the existing playback pipeline or to the short path —
  only the orchestrator's decision of "should we run the pipeline at
  all" and the post-success "where does `full.wav` go."

## Invariants introduced

- **I-11.1 Hash-determines-key.** A cache entry at `config/cache/<prefix>/`
  contains a `full.wav` produced from a run whose source+voice+speed
  hashed to `<prefix>…`.
- **I-11.2 Atomic promotion.** `full.wav` appears in a cache dir only
  after a successful run. A crashed run never promotes.
- **I-11.3 Replay-without-transcript.** `/replay` never reads the
  transcript — it only reads `config/cache/`. This makes replay work
  even in sessions where `CLAUDE_PROJECT_DIR` / `.jsonl` are missing.
- **I-11.4 Meta required.** Every cache entry has a parseable
  `meta.json`. Orphan `full.wav` files (from a failed promotion) are
  ignored on lookup and listed for manual cleanup by a future
  `auto-speech cache doctor` command (not in this phase).
