// Speak Selection — background service worker (MV3).
//
// Primary path: POST the selection to the local auto-speech server
// (Kokoro via mlx-audio) at /api/synthesize, receive WAV bytes, and play
// them in the active tab. If the server is unreachable (or the user chose
// the browser backend), fall back to the page's Web Speech API.
//
// MV3 service workers have no `window`/`speechSynthesis` and cannot play
// audio, so all playback is injected into the tab via chrome.scripting.

const MENU_ID = "speak-selection";
const STOP_ID = "speak-selection-stop";

const DEFAULTS = {
  backend: "local", // "local" (auto-speech server) | "browser" (Web Speech)
  serverUrl: "http://127.0.0.1:7860",
  speed: 1.0, // local backend
  voiceId: "", // local backend voice override (blank = server default)
  // Browser-backend voice params:
  rate: 1.0,
  pitch: 1.0,
  volume: 1.0,
  voiceURI: "",
};

// --- Context menu wiring -------------------------------------------------

chrome.runtime.onInstalled.addListener(createMenus);
chrome.runtime.onStartup.addListener(createMenus);

function createMenus() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: MENU_ID,
      title: 'Speak "%s"',
      contexts: ["selection"],
    });
    chrome.contextMenus.create({
      id: STOP_ID,
      title: "Stop speaking",
      contexts: ["all"],
    });
  });
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (!tab?.id) return;
  if (info.menuItemId === STOP_ID) return stopAll(tab.id);
  if (info.menuItemId === MENU_ID) {
    const text = (info.selectionText || "").trim();
    if (text) await speak(text, tab.id);
  }
});

// Toolbar-button click stops any in-progress speech.
chrome.action.onClicked.addListener((tab) => {
  if (tab?.id) stopAll(tab.id);
});

// --- Speak dispatch ------------------------------------------------------

async function getSettings() {
  const stored = await chrome.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...stored };
}

async function speak(text, tabId) {
  const settings = await getSettings();

  if (settings.backend === "browser") {
    return speakViaBrowser(text, tabId, settings);
  }

  // Local backend: fetch WAV in the worker (extension origin + host
  // permission ⇒ no CORS wall), hand a data URL to the page for playback.
  try {
    const dataUrl = await synthesizeToDataUrl(text, settings);
    await runInPage(tabId, playAudioInPage, [dataUrl]);
  } catch (err) {
    console.warn(
      "[Speak Selection] local backend failed, falling back to browser TTS:",
      err
    );
    await speakViaBrowser(text, tabId, settings);
  }
}

async function synthesizeToDataUrl(text, settings) {
  const base = settings.serverUrl.replace(/\/+$/, "");
  const resp = await fetch(`${base}/api/synthesize`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      speed: settings.speed,
      ...(settings.voiceId ? { voice: settings.voiceId } : {}),
    }),
  });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => "");
    throw new Error(`server ${resp.status}: ${detail.slice(0, 200)}`);
  }
  const buf = await resp.arrayBuffer();
  return `data:audio/wav;base64,${arrayBufferToBase64(buf)}`;
}

function arrayBufferToBase64(buf) {
  const bytes = new Uint8Array(buf);
  let binary = "";
  const CHUNK = 0x8000; // avoid arg-count limits on String.fromCharCode
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

function speakViaBrowser(text, tabId, settings) {
  return runInPage(tabId, speakInPage, [text, settings]);
}

function stopAll(tabId) {
  return runInPage(tabId, stopSpeakingInPage, []);
}

function runInPage(tabId, func, args) {
  return chrome.scripting
    .executeScript({ target: { tabId }, func, args })
    .catch((err) => console.warn("[Speak Selection] inject failed:", err));
}

// --- Functions injected into the page ------------------------------------
// These run in the page's context. A single shared <audio> element (keyed
// on the window) lets "Stop speaking" cancel local-backend playback too.

function playAudioInPage(dataUrl) {
  try {
    if (window.__autoSpeechAudio) {
      window.__autoSpeechAudio.pause();
    }
    window.speechSynthesis?.cancel();
    const audio = new Audio(dataUrl);
    window.__autoSpeechAudio = audio;
    audio.play().catch((e) => console.error("[Speak Selection] play:", e));
  } catch (e) {
    console.error("[Speak Selection]", e);
  }
}

function speakInPage(text, settings) {
  try {
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(text);
    utter.rate = settings.rate;
    utter.pitch = settings.pitch;
    utter.volume = settings.volume;
    if (settings.voiceURI) {
      const voice = window.speechSynthesis
        .getVoices()
        .find((v) => v.voiceURI === settings.voiceURI);
      if (voice) utter.voice = voice;
    }
    window.speechSynthesis.speak(utter);
  } catch (e) {
    console.error("[Speak Selection]", e);
  }
}

function stopSpeakingInPage() {
  try {
    window.speechSynthesis?.cancel();
    if (window.__autoSpeechAudio) {
      window.__autoSpeechAudio.pause();
      window.__autoSpeechAudio = null;
    }
  } catch (e) {
    console.error("[Speak Selection]", e);
  }
}
