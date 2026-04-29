# ADR-010 — Localhost web UI for general-purpose paste-and-speak

**Status:** Accepted
**Date:** 2026-04-29
**Depends on:** ADR-007 (single `full.wav` artifact), ADR-008 (replay cache),
ADR-009 (mpv controller).

## Context
Today, `auto-speech` is reachable only through Claude Code slash commands.
That ties the system to a specific application. The user wants a
general-purpose surface — paste text into a web page, hear it spoken,
control playback the same way the slash commands do.

The TTS engine, cache, and mpv controller already exist as composable
services. The only thing missing is an HTTP/HTML surface that exposes them.

## Decision

**Add a Flask-based web server, bound to `127.0.0.1:7860`, that serves a
single static page and a small JSON API.** The server is a long-lived
process distinct from any one `/speak` invocation. It holds **one
`TTSEngine` instance** for the life of the process so the model loads once
and serves every subsequent request warm.

### Bind / port / auth
- Listen address: `127.0.0.1` only — no LAN exposure.
- Default port: `7860` (configurable via `--port`).
- No authentication. The localhost binding is the entire trust boundary.
  Documented as such; if the user ever wants remote access they must
  punt to an SSH tunnel or front it with their own reverse-proxy + auth.

### What the server does NOT do
- **No rewrite.** Text submitted via the web UI is spoken verbatim. The
  audio-friendly rewrite belongs to `/speak` (Claude-mediated, 12 rules)
  and is irrelevant to general-purpose paste-and-speak. If listeners
  want markdown-stripped speech later, that can be added behind a
  checkbox; v0.1 is deliberately minimal.
- **No multi-user state.** Single user, single mpv session, single
  TTS engine.

### What the server DOES do
| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | The single-page UI (HTML + inline JS + CSS). |
| `/api/speak` | POST | Take `{text}`; hash; check cache; on miss, run the pipeline; on hit or success, hand off to mpv. |
| `/api/replay` | POST | Take `{hash}`; play that cache entry via mpv. |
| `/api/cache` | GET | List cache entries by recency (hash, voice, char_count, duration_s, created_at). |
| `/api/pause` | POST | mpv pause. |
| `/api/resume` | POST | mpv resume. |
| `/api/seek` | POST | Take `{target}` (`+15` / `-30` / absolute / `end`). |
| `/api/restart` | POST | Seek to 0. |
| `/api/end` | POST | Send mpv quit. |
| `/api/status` | GET | Query mpv: `{active, paused, position, duration, wav}`. |

### Concurrency
- A single `threading.Lock` serializes pipeline-running endpoints
  (`/api/speak`, `/api/replay`). Read endpoints (`/api/status`,
  `/api/cache`) are concurrent. mpv playback is naturally singleton
  per ADR-009, so attempting concurrent speaks would conflict anyway —
  the lock just sequences them cleanly instead of letting mpv resolve
  the race by killing the prior session in the middle of generation.

## Rationale

1. **Hot model = fast first audio.** Loading Kokoro takes ~2.3 s. In the
   slash-command flow, every invocation pays this. In the web server,
   only the very first request after process start pays it; subsequent
   requests skip straight to synthesis.
2. **Same orchestrator, different entrypoint.** Reusing
   `PipelineOrchestrator` keeps cache promotion, concat, and mpv
   handoff identical between the slash command and the web UI. Only
   the entry surface changes.
3. **Pass-through text is honest.** A web textarea with TTS is a
   different product than "narrate Claude responses." Pretending
   otherwise (auto-rewrite for everything pasted) would degrade quality
   on inputs that didn't need rewriting (e.g., already-edited prose).
4. **Polling, not WebSockets.** mpv state changes infrequently (pause,
   seek, end). A 500 ms `/api/status` poll while playback is active is
   fine and avoids adding an async server stack.
5. **Localhost-only is the right trust model.** This is a single-user
   tool that drives the user's audio device. There is no scenario in
   which a remote caller should be able to make their machine speak.

## Alternatives considered

- **Stdlib `http.server`.** Workable but the routing boilerplate for ~9
  endpoints is ~3× the code of Flask. Not worth saving the dependency.
- **FastAPI + uvicorn.** Async + OpenAPI docs are overkill; mpv
  spawning is sync and the request rate is single-user.
- **Server-Sent Events / WebSocket for live status.** Overkill for
  pause/seek state. Polling at 500 ms is cheap and trivially debuggable.
- **One process holding mpv directly (not via subprocess).** Defeats
  ADR-009's detached-lifecycle invariant — restarting the server would
  kill audio. We deliberately keep mpv detached.

## Consequences

- Add a Flask dependency: `flask>=3.0` to `setup/install.sh`. ~2 MB.
- New module `web_server.py` introducing class `WebServer` (single class,
  one file per the standing rule). The Flask routes are registered as
  bound methods on the instance — preserves the lazy-loaded TTSEngine,
  CacheStore, and MpvController on instance attributes.
- One small refactor: `PipelineOrchestrator.__init__` gains an optional
  `tts_engine` parameter. If provided, the orchestrator uses it
  unchanged (no model reload). If absent, it instantiates one — fully
  backwards-compatible with every existing caller.
- New shell wrapper `run_server.sh` that activates the venv and starts
  the server in the foreground. The user runs it in a terminal tab; we
  do not auto-start.
- New static UI under `plugin/web/templates/index.html` (Flask default
  template path). Single HTML file with inline CSS and JS.

## Invariants

- **I-13.1 Localhost-only binding.** The Flask app starts with
  `host="127.0.0.1"` and refuses to bind elsewhere unless the user
  edits the script. The default in code matches the default in docs.
- **I-13.2 One TTSEngine per process.** The server creates exactly one
  `TTSEngine` and reuses it for every request. The reuse is enforced
  by storing it on `self._tts` and never reassigning.
- **I-13.3 Pipeline lock.** Only one of `/api/speak` and `/api/replay`
  runs at a time. The lock is held across the whole pipeline, not just
  the cache check.
- **I-13.4 Pass-through text contract.** The server **never** rewrites
  user text. What is sent to `/api/speak` is what the TTS receives.
- **I-13.5 Server-restart-doesn't-stop-playback.** mpv is detached;
  killing or restarting the Flask process must not interrupt audio
  that is already playing.

## Out of scope (explicit non-goals for v0.1)
- Authentication.
- Remote-network exposure.
- Markdown stripping (Phase 14 candidate; opt-in).
- Anthropic-API-mediated rewrite (separate ADR if it ships).
- Persistent UI preferences (volume, voice picker).
- WebSocket-driven live waveform / progress bar.
