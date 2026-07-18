// Speak Selection — background service worker (MV3).
//
// MV3 service workers have no `window`/`speechSynthesis`, so the actual
// speaking happens in the page context via chrome.scripting.executeScript.

const MENU_ID = "speak-selection";
const STOP_ID = "speak-selection-stop";

const DEFAULTS = { rate: 1.0, pitch: 1.0, volume: 1.0, voiceURI: "" };

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
  if (info.menuItemId === STOP_ID) {
    return runInPage(tab.id, stopSpeaking, []);
  }
  if (info.menuItemId === MENU_ID) {
    const text = (info.selectionText || "").trim();
    if (!text) return;
    const settings = await getSettings();
    return runInPage(tab.id, speakInPage, [text, settings]);
  }
});

// Toolbar-button click stops any in-progress speech.
chrome.action.onClicked.addListener((tab) => {
  if (tab?.id) runInPage(tab.id, stopSpeaking, []);
});

async function getSettings() {
  const stored = await chrome.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...stored };
}

function runInPage(tabId, func, args) {
  return chrome.scripting
    .executeScript({ target: { tabId }, func, args })
    .catch((err) => console.warn("[Speak Selection] inject failed:", err));
}

// --- Functions injected into the page ------------------------------------
// These run in the page's context, where speechSynthesis is available.

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

function stopSpeaking() {
  try {
    window.speechSynthesis.cancel();
  } catch (e) {
    console.error("[Speak Selection]", e);
  }
}
