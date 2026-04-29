# Phase 13 Micro-Design — Localhost web server

Implements [ADR-010](../decisions/ADR-010-localhost-web-ui.md).

## Scope
A localhost web server that exposes the existing TTS + cache + mpv
services through HTTP and serves a single-page UI. Pass-through text
(no rewrite). Hot TTSEngine reused across requests.

## M1 — Classes at this level

- **`WebServer`** (L4 service, new) — owns the Flask app, the hot
  `TTSEngine`, the `CacheStore`, the `MpvController`, and a
  pipeline-serializing lock. Registers the route handlers as methods.
- **No new domain classes.** Cache, controller, planner, producer,
  concatenator, voice profile — all reused.

Edit:
- **`PipelineOrchestrator`** — gains an optional `tts_engine` parameter.

## M2 — Semantics

### `WebServer`
Constructed once per process. Holds:
- `self._app: Flask` — the Flask instance, with a custom `template_folder`
  pointing to `plugin/web/templates/`.
- `self._tts: TTSEngine` — created once; pre-warmed at startup so the
  first request doesn't pay the model-load cost.
- `self._cache: CacheStore` — points at `config/cache/`.
- `self._mpv: MpvController` — invoked per request.
- `self._profile: VoiceProfile` — read once; reread on demand if
  `config/voice_calibration.json` changes (mtime check).
- `self._lock: threading.Lock` — held across the entire `/api/speak`
  or `/api/replay` body so concurrent requests serialize.

Lifecycle:
- `__init__` builds the Flask app, registers all routes, instantiates
  the services, and pre-warms the TTSEngine.
- `run(host, port)` calls `self._app.run(host=host, port=port,
  threaded=True)`.

### Routes (registered as `WebServer` methods)
| Method | Path | Handler |
|---|---|---|
| GET | `/` | `_index` — `render_template("index.html", port=...)` |
| POST | `/api/speak` | `_handle_speak` |
| POST | `/api/replay` | `_handle_replay` |
| GET | `/api/cache` | `_handle_cache_list` |
| POST | `/api/pause` | `_handle_pause` |
| POST | `/api/resume` | `_handle_resume` |
| POST | `/api/seek` | `_handle_seek` |
| POST | `/api/restart` | `_handle_restart` |
| POST | `/api/end` | `_handle_end` |
| GET | `/api/status` | `_handle_status` |

### Pre-warm
At construction, `self._tts._ensure_loaded()` is called inside a
`try/except` that logs a warning and proceeds on failure. If TTS is
broken, cache-only paths (replay, controls, status) still work; only
`/api/speak` will fail later with a clear error.

### `_handle_speak` body shape
```
POST /api/speak
Content-Type: application/json
{ "text": "..." }
```
Returns `200`:
```
{ "status": "started" | "cache_hit",
  "hash": "<64 hex>",
  "char_count": <int>,
  "estimated_duration_s": <float> }
```
or `400` on empty/missing text, `500` on TTS or mpv failure.

### `_handle_replay` body shape
```
POST /api/replay
{ "hash": "<full or 16-char prefix>" }
```
If the hash is a 16-char prefix, the server treats it as a `path_for`
lookup and finds the matching dir. Returns 404 if absent.

### `_handle_status`
- If `SessionDir.is_mpv_running()` is False → `{"active": false}`.
- Else issue three IPC `get_property` calls (`time-pos`, `duration`,
  `pause`) and return them. Wrap each in try/except so a transiently
  failing IPC returns `{"active": false}` rather than 502.

### `_handle_cache_list`
Calls `self._cache.list_by_recency()`; transforms each `CacheEntry`
into a JSON-friendly dict; returns the list.

## M3 — Relationships

```
HTTP client (browser)
   │
   ▼
WebServer.<route handler>
   │
   ├── /api/speak ──► hash() ─► CacheStore.lookup ─► hit?
   │                                  yes → MpvController.start(cached_wav)
   │                                  no  → PipelineOrchestrator(tts_engine=self._tts).run
   ├── /api/replay ─► CacheStore.path_for ─► MpvController.start
   ├── /api/cache  ─► CacheStore.list_by_recency
   ├── /api/{pause|resume|restart|end} ─► MpvIpc.send
   ├── /api/seek   ─► (parse target) ─► MpvIpc.send
   └── /api/status ─► SessionDir + MpvIpc.send (3 props)
```

Frontend (the static page) calls these with `fetch()`; uses a 500 ms
`setInterval` poll on `/api/status` while playback is active.

## M4 — Implementation notes

### Refactor of `PipelineOrchestrator`
```python
class PipelineOrchestrator:
    def __init__(
        self,
        keep_artifacts: bool = False,
        source_hash: str | None = None,
        cache_root: Path | None = None,
        tts_engine: TTSEngine | None = None,
    ) -> None:
        ...
        self._injected_tts = tts_engine

    def run(self, transcript_text, turn_ordinal=1):
        ...
        tts_engine = self._injected_tts or TTSEngine()
        ...
```
No other code paths change. Backwards compatible.

### Hash computation in the server
The slash command shells out to `compute_hash.sh`. The web server can
compute the hash directly with `hashlib`. Same key shape:
`sha256(text_bytes || 0x00 || voice_id || ":" || speed_str)`.

### Concurrency lock
```python
with self._lock:
    # cache lookup, possibly run pipeline, hand off to mpv
    ...
```
The lock is `threading.Lock`, not `RLock`. The handler does not
re-enter itself.

### Front-end polling cadence
- Idle: poll every 2000 ms.
- Active (playing or paused): poll every 500 ms.

The page also re-fetches `/api/cache` after every `/api/speak` returns
so the cache list always reflects the latest entry.

## Failure modes

| Mode | Handling |
|---|---|
| TTS load fails at startup | Logged warning. Server still starts; `/api/speak` returns 500. |
| mpv not installed | `MpvController.start` raises; handler returns 500 with the install hint. |
| Stale session at startup | `MpvController.start` already kills any prior session — no special handling needed. |
| User-supplied bad seek target | 400 with the parser's message. |
| Cache lookup error (corrupt meta.json) | Treated as miss, logged as orphan (CacheStore already handles this). |
| Two `/api/speak` requests in flight | Second one blocks on the lock until the first finishes (then runs). |

## Files to add

```
plugin/scripts/python/web_server.py        # WebServer class + main()
plugin/web/templates/index.html            # full UI in one file (HTML+CSS+JS inline)
plugin/scripts/shell/run_server.sh         # venv activator + start
```

Edits:
```
plugin/scripts/python/pipeline.py          # accept tts_engine param
setup/install.sh                           # uv pip install flask
docs/plan/{artifact-map,phase-breakdown,implementation-plan}.md
```

## Check gate

1. `setup/install.sh` installs flask.
2. `setup/run-server.sh` starts the server; the page is reachable at
   `http://127.0.0.1:7860/`.
3. Pasting "Hello, this is a test." into the textarea and clicking
   Speak triggers a cache-miss run; audio plays.
4. Clicking Speak again on the same text triggers a cache hit; audio
   plays without TTS overhead (visible in the server log).
5. While audio plays, the Pause/Resume buttons work, Seek bar +/-
   buttons jump correctly, Restart goes to 0, End stops mpv.
6. The cache list shows the new entry; clicking it triggers a replay
   without re-generation.
7. Restarting the Flask server while audio is playing does **not**
   interrupt the audio (mpv is detached).

## Out of scope
- Markdown stripping (future Phase 14).
- Volume control via UI (mpv supports it but UI noise > value for v1).
- Voice/speed switching from UI (read-only display of active profile).
- Persistent client preferences.
