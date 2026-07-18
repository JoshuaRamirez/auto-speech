# Speak Selection — Chrome Extension

Right-click highlighted text on any page to have it read aloud. Uses the
browser's built-in Web Speech API (`speechSynthesis`) — no backend, works
offline.

## Features (v0.1.0)

- Context-menu **Speak "…"** on any text selection.
- Context-menu **Stop speaking** (also: click the toolbar icon to stop).
- Options page: choose voice, rate, pitch, volume; test button.
- Settings sync across your Chrome profile via `chrome.storage.sync`.

## Install (unpacked, for development)

1. Open `chrome://extensions`.
2. Enable **Developer mode** (top-right).
3. Click **Load unpacked** and select this `chrome-extension/` directory.
4. Highlight text on any page → right-click → **Speak "…"**.

Adjust the voice via the extension's **Options** (right-click the toolbar
icon → Options, or the entry on `chrome://extensions`).

## Architecture

MV3 service workers have no `speechSynthesis`, so `background.js` injects the
speak/stop calls into the active tab's page context via
`chrome.scripting.executeScript`. The options page is itself an extension
page, so it enumerates and previews voices directly.

## Roadmap

- Keyboard shortcut to speak the current selection.
- Highlight-follows-speech (word boundary events).
- Optional backend voices (Kokoro/MLX) via the parent `auto-speech` daemon
  for higher-quality synthesis.
- Per-site voice/rate overrides.
