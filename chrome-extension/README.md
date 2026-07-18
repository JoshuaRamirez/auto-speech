# Speak Selection — Chrome Extension

Right-click highlighted text on any page to have it read aloud, using the
**local auto-speech (Kokoro) engine** for high-quality synthesis — with an
automatic fallback to the browser's built-in voices when the local server
isn't running.

## Features (v0.2.0)

- Context-menu **Speak "…"** on any text selection.
- Context-menu **Stop speaking** (also: click the toolbar icon to stop).
- Two voice engines, selectable in Options:
  - **Local auto-speech (Kokoro)** — best quality. The extension POSTs the
    selection to the local server's `/api/synthesize`, gets WAV bytes back,
    and plays them in the tab. Server URL, voice id, and speed configurable.
    If the server is unreachable, it silently falls back to browser voices.
  - **Browser built-in voices** — offline; voice, rate, pitch, volume.
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
thread, with permissive CORS (the server stays localhost-bound).

## Version manifest note

`background.js` corresponds to `manifest.json` `version` 0.2.0. Bump the
manifest version alongside behavioral changes.

## Roadmap

- Keyboard shortcut to speak the current selection.
- Highlight-follows-speech (word boundary events).
- Enumerate available Kokoro voice ids from the server in Options.
- Per-site voice/rate overrides.
