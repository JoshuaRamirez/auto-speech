# AutoSpeech — Chrome Extension

Right-click highlighted text on any page to have it read aloud, using the
**local AutoSpeech (Kokoro) engine** for high-quality synthesis — with an
automatic fallback to the browser's built-in voices when the local server
isn't running. Appears as **AutoSpeech** in the right-click menu.

## Features (v0.3.0)

- Context-menu **AutoSpeech: speak "…"** on any text selection.
- Context-menu **AutoSpeech: stop speaking** (also: click the toolbar icon).
- Two voice engines, selectable in Options:
  - **Local AutoSpeech (Kokoro)** — best quality. The extension POSTs the
    selection to the local server's `/api/synthesize`, gets WAV bytes back,
    and plays them in the tab. Server URL, **voice**, and speed configurable.
    If the server is unreachable, it silently falls back to browser voices.
  - **Browser built-in voices** — offline; voice, rate, pitch, volume.
- **Voice picker** populated live from the server's `/api/voices` — the
  Kokoro voices this install can synthesize, grouped by language and gender
  (American/British English out of the box, plus Spanish/French/Hindi/
  Italian/Portuguese via espeak; Japanese/Mandarin appear only if the
  `misaki[ja]`/`misaki[zh]` extras are installed).
- Settings sync across your Chrome profile via `chrome.storage.sync`.

## Using the local Kokoro backend

1. Start the auto-speech web server (it holds a hot Kokoro model):
   `/auto-speech-app` in Claude Code, or
   `python plugin/scripts/python/web_server.py` from the repo. It binds
   `http://127.0.0.1:7860` (localhost-only).
2. In the extension Options, keep **Voice engine = Local auto-speech**; the
   server-URL hint shows a live reachability check.
3. Highlight text → right-click → **Speak "…"**. Audio plays in the tab.

The endpoint reads text verbatim (no LLM rewrite) and caches by
text+voice+speed, so repeats are instant.

## Install (unpacked, for development)

1. Open `chrome://extensions`.
2. Enable **Developer mode** (top-right).
3. Click **Load unpacked** and select this `chrome-extension/` directory.
4. Highlight text on any page → right-click → **Speak "…"**.

Adjust the voice via the extension's **Options** (right-click the toolbar
icon → Options, or the entry on `chrome://extensions`).

## Architecture

MV3 service workers have no `speechSynthesis` and cannot play audio, so all
playback is injected into the active tab via `chrome.scripting.executeScript`.

- **Local backend**: the service worker `fetch`es WAV bytes from
  `/api/synthesize` (extension origin + host permission ⇒ no CORS wall),
  base64-encodes them, and injects a `play()` on a shared `<audio>` element.
- **Browser backend / fallback**: injects a `SpeechSynthesisUtterance`.
- **Stop** cancels both the shared `<audio>` and any `speechSynthesis`.

The server side is `/api/synthesize` in `plugin/scripts/python/web_server.py`
— passthrough (verbatim), cached in its own key space, run on the TTS worker
thread. CORS headers are reflected only for Chrome-extension origins;
ordinary web origins get none (the server is also localhost-bound), so
arbitrary pages cannot drive the API even though the extension can.

## Robustness notes

- **Long selections** are chunked on sentence boundaries before synthesis (a
  single long Kokoro generate trips an mlx-audio broadcast-shape bug).
- **Every span is synthesized resiliently**: a span that still trips the bug
  is split finer (sentences → clauses → words) and retried; unspeakable
  fragments (e.g. a lone `★`) are skipped, not fatal.
- **Symbol-only selections** return HTTP 422 and the extension stays quiet.
- **Unknown voice** returns HTTP 400 with the valid list (not a silent
  fallback).

## Version manifest note

`background.js` corresponds to `manifest.json` `version` 0.3.0. Bump the
manifest version alongside behavioral changes.

## Roadmap

- Keyboard shortcut to speak the current selection.
- Highlight-follows-speech (word boundary events).
- Per-site voice/rate overrides.
