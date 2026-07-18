// Options page logic. Runs in an extension page, so speechSynthesis is
// available here for voice enumeration and previewing.

const DEFAULTS = { rate: 1.0, pitch: 1.0, volume: 1.0, voiceURI: "" };

const els = {
  voice: document.getElementById("voice"),
  rate: document.getElementById("rate"),
  pitch: document.getElementById("pitch"),
  volume: document.getElementById("volume"),
  rateVal: document.getElementById("rate-val"),
  pitchVal: document.getElementById("pitch-val"),
  volumeVal: document.getElementById("volume-val"),
  test: document.getElementById("test"),
  save: document.getElementById("save"),
  status: document.getElementById("status"),
};

let current = { ...DEFAULTS };

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

function syncLabels() {
  els.rateVal.textContent = Number(els.rate.value).toFixed(1);
  els.pitchVal.textContent = Number(els.pitch.value).toFixed(1);
  els.volumeVal.textContent = Number(els.volume.value).toFixed(1);
}

async function load() {
  current = { ...DEFAULTS, ...(await chrome.storage.sync.get(DEFAULTS)) };
  els.rate.value = current.rate;
  els.pitch.value = current.pitch;
  els.volume.value = current.volume;
  syncLabels();
  populateVoices();
}

function readForm() {
  return {
    rate: parseFloat(els.rate.value),
    pitch: parseFloat(els.pitch.value),
    volume: parseFloat(els.volume.value),
    voiceURI: els.voice.value,
  };
}

els.rate.addEventListener("input", syncLabels);
els.pitch.addEventListener("input", syncLabels);
els.volume.addEventListener("input", syncLabels);

els.save.addEventListener("click", async () => {
  await chrome.storage.sync.set(readForm());
  els.status.textContent = "Saved.";
  setTimeout(() => (els.status.textContent = ""), 1500);
});

els.test.addEventListener("click", () => {
  const s = readForm();
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(
    "The quick brown fox jumps over the lazy dog."
  );
  u.rate = s.rate;
  u.pitch = s.pitch;
  u.volume = s.volume;
  if (s.voiceURI) {
    const voice = window.speechSynthesis
      .getVoices()
      .find((v) => v.voiceURI === s.voiceURI);
    if (voice) u.voice = voice;
  }
  window.speechSynthesis.speak(u);
});

// Voice list often loads asynchronously.
window.speechSynthesis.onvoiceschanged = populateVoices;
load();
