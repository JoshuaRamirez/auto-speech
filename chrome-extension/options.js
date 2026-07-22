// AutoSpeech options page. Runs in an extension page, so speechSynthesis is
// available for browser-backend voice enumeration and previews. The local
// backend's voices come from the server's /api/voices; previews fetch a WAV
// from /api/synthesize.

const DEFAULTS = {
  backend: "local",
  serverUrl: "http://127.0.0.1:7860",
  speed: 1.0,
  voiceId: "",
  rate: 1.0,
  pitch: 1.0,
  volume: 1.0,
  voiceURI: "",
};

const $ = (id) => document.getElementById(id);
const els = {
  backend: $("backend"),
  localSection: $("local-section"),
  browserSection: $("browser-section"),
  serverUrl: $("serverUrl"),
  serverCheck: $("server-check"),
  voiceId: $("voiceId"),
  voiceCount: $("voice-count"),
  speed: $("speed"),
  speedVal: $("speed-val"),
  voice: $("voice"),
  rate: $("rate"),
  pitch: $("pitch"),
  volume: $("volume"),
  rateVal: $("rate-val"),
  pitchVal: $("pitch-val"),
  volumeVal: $("volume-val"),
  test: $("test"),
  save: $("save"),
  status: $("status"),
};

let current = { ...DEFAULTS };
let previewAudio = null;

// --- Backend visibility --------------------------------------------------

function applyBackendVisibility() {
  const local = els.backend.value === "local";
  els.localSection.classList.toggle("hidden", !local);
  els.browserSection.classList.toggle("hidden", local);
}

// --- Browser voices ------------------------------------------------------

function populateVoices() {
  const voices = window.speechSynthesis.getVoices();
  els.voice.innerHTML = "";
  const def = document.createElement("option");
  def.value = "";
  def.textContent = "Default (browser)";
  els.voice.appendChild(def);
  for (const v of voices) {
    const opt = document.createElement("option");
    opt.value = v.voiceURI;
    opt.textContent = `${v.name} (${v.lang})${v.default ? " — default" : ""}`;
    els.voice.appendChild(opt);
  }
  els.voice.value = current.voiceURI;
}

// --- Local (Kokoro) voices ----------------------------------------------

// Kokoro voice ids are <lang><gender>_<name>, e.g. "af_bella".
const LANG_NAMES = {
  a: "American English",
  b: "British English",
  e: "Spanish",
  f: "French",
  h: "Hindi",
  i: "Italian",
  j: "Japanese",
  p: "Brazilian Portuguese",
  z: "Mandarin Chinese",
};

function groupLabel(id) {
  const lang = LANG_NAMES[id[0]] || "Other";
  const gender = id[1] === "m" ? "Male" : id[1] === "f" ? "Female" : "";
  return gender ? `${lang} · ${gender}` : lang;
}

function prettyName(id) {
  const name = (id.split("_")[1] || id).replace(/^\w/, (c) => c.toUpperCase());
  return `${name} (${id})`;
}

async function populateLocalVoices() {
  const base = els.serverUrl.value.trim().replace(/\/+$/, "") || DEFAULTS.serverUrl;
  // Reset to just the default option.
  els.voiceId.innerHTML = "";
  const def = document.createElement("option");
  def.value = "";
  def.textContent = "Server default";
  els.voiceId.appendChild(def);

  let voices = [];
  let reached = false;
  try {
    const resp = await fetch(`${base}/api/voices`, { method: "GET" });
    if (resp.ok) {
      reached = true;
      voices = (await resp.json()).voices || [];
    }
  } catch {
    /* server down — leave just the default; text below reflects it */
  }

  if (!voices.length) {
    // A reachable server can legitimately report zero voices (voice
    // discovery degraded — e.g. model snapshot not cached yet); the
    // server default voice still synthesizes fine in that state.
    els.voiceCount.textContent = reached
      ? "(no voices reported — server default only)"
      : "(server unreachable — using default)";
    els.voiceId.value = "";
    return;
  }

  // Group by language·gender for a scannable menu.
  const groups = {};
  for (const id of voices) (groups[groupLabel(id)] ||= []).push(id);
  for (const label of Object.keys(groups).sort()) {
    const og = document.createElement("optgroup");
    og.label = label;
    for (const id of groups[label]) {
      const opt = document.createElement("option");
      opt.value = id;
      opt.textContent = prettyName(id);
      og.appendChild(opt);
    }
    els.voiceId.appendChild(og);
  }
  els.voiceCount.textContent = `(${voices.length} voices)`;
  // Restore the saved selection if it still exists.
  els.voiceId.value = current.voiceId;
  if (els.voiceId.value !== current.voiceId) els.voiceId.value = "";
}

// --- Labels --------------------------------------------------------------

function syncLabels() {
  els.speedVal.textContent = Number(els.speed.value).toFixed(1);
  els.rateVal.textContent = Number(els.rate.value).toFixed(1);
  els.pitchVal.textContent = Number(els.pitch.value).toFixed(1);
  els.volumeVal.textContent = Number(els.volume.value).toFixed(1);
}

// --- Load / read ---------------------------------------------------------

async function load() {
  current = { ...DEFAULTS, ...(await chrome.storage.sync.get(DEFAULTS)) };
  els.backend.value = current.backend;
  els.serverUrl.value = current.serverUrl;
  els.speed.value = current.speed;
  els.rate.value = current.rate;
  els.pitch.value = current.pitch;
  els.volume.value = current.volume;
  syncLabels();
  applyBackendVisibility();
  populateVoices(); // browser voices
  populateLocalVoices(); // Kokoro voices (sets voiceId selection)
  checkServer();
}

function readForm() {
  return {
    backend: els.backend.value,
    serverUrl: els.serverUrl.value.trim() || DEFAULTS.serverUrl,
    voiceId: els.voiceId.value.trim(),
    speed: parseFloat(els.speed.value),
    rate: parseFloat(els.rate.value),
    pitch: parseFloat(els.pitch.value),
    volume: parseFloat(els.volume.value),
    voiceURI: els.voice.value,
  };
}

// --- Server reachability -------------------------------------------------

async function checkServer() {
  const base = els.serverUrl.value.trim().replace(/\/+$/, "") || DEFAULTS.serverUrl;
  els.serverCheck.textContent = "checking…";
  try {
    const resp = await fetch(`${base}/api/status`, { method: "GET" });
    els.serverCheck.textContent = resp.ok ? "✓ reachable" : `✗ HTTP ${resp.status}`;
  } catch {
    els.serverCheck.textContent = "✗ not reachable — is the app running?";
  }
}

// --- Preview -------------------------------------------------------------

const SAMPLE = "The quick brown fox jumps over the lazy dog.";

async function testVoice() {
  const s = readForm();
  stopPreview();
  if (s.backend === "browser") {
    const u = new SpeechSynthesisUtterance(SAMPLE);
    u.rate = s.rate;
    u.pitch = s.pitch;
    u.volume = s.volume;
    if (s.voiceURI) {
      const v = window.speechSynthesis.getVoices().find((x) => x.voiceURI === s.voiceURI);
      if (v) u.voice = v;
    }
    window.speechSynthesis.speak(u);
    return;
  }
  // Local backend preview.
  setStatus("synthesizing…", "");
  const base = s.serverUrl.replace(/\/+$/, "");
  try {
    const resp = await fetch(`${base}/api/synthesize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: SAMPLE,
        speed: s.speed,
        ...(s.voiceId ? { voice: s.voiceId } : {}),
      }),
    });
    if (!resp.ok) {
      setStatus(`server ${resp.status}`, "err");
      return;
    }
    const blob = await resp.blob();
    previewAudio = new Audio(URL.createObjectURL(blob));
    previewAudio.play();
    setStatus("", "");
  } catch (e) {
    setStatus("could not reach server", "err");
  }
}

function stopPreview() {
  window.speechSynthesis.cancel();
  if (previewAudio) {
    previewAudio.pause();
    previewAudio = null;
  }
}

function setStatus(msg, cls) {
  els.status.textContent = msg;
  els.status.className = cls;
  if (msg && cls === "ok") setTimeout(() => setStatus("", ""), 1500);
}

// --- Wiring --------------------------------------------------------------

els.backend.addEventListener("change", () => {
  applyBackendVisibility();
  if (els.backend.value === "local") populateLocalVoices();
});
els.serverUrl.addEventListener("change", () => {
  checkServer();
  populateLocalVoices();
});
["speed", "rate", "pitch", "volume"].forEach((k) =>
  els[k].addEventListener("input", syncLabels)
);

els.save.addEventListener("click", async () => {
  await chrome.storage.sync.set(readForm());
  setStatus("Saved.", "ok");
});
els.test.addEventListener("click", testVoice);

window.speechSynthesis.onvoiceschanged = populateVoices;
load();
