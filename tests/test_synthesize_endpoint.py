"""Unit tests for the /api/synthesize endpoint (Chrome-extension backend).

Verifies the browser-facing synthesize path WITHOUT loading MLX: the
TTSEngine is stubbed to emit a tiny valid WAV. Pins:
  - 400 on empty text, 413 on oversize text
  - 200 audio/wav on success, with the hash header
  - second identical request is served from cache (no re-synthesis)
  - CORS headers present; OPTIONS preflight returns 204
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "plugin" / "scripts" / "python"
sys.path.insert(0, str(SRC))


def _write_tiny_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(b"\x00\x00" * 2400)  # 0.1 s of silence


def _make_server(tmp_path: Path):
    """Build a WebServer with MLX fully stubbed and cache under tmp_path."""
    import web_server

    synth_calls = {"n": 0}

    def fake_synthesize(text, profile, out_path):  # noqa: ANN001 — instance attr, no self
        synth_calls["n"] += 1
        _write_tiny_wav(Path(out_path))

    # Patch only construction (cache location, model prewarm, mpv). The
    # instance's synthesize is then replaced so it persists for the whole
    # test — the request path runs after this function returns.
    with mock.patch.object(web_server, "_cache_root", lambda: tmp_path / "cache"), \
        mock.patch("tts_engine.TTSEngine._ensure_loaded", lambda self: None), \
        mock.patch("web_server.MpvController"):
        server = web_server.WebServer()
    server._tts.synthesize = fake_synthesize  # noqa: SLF001
    return server, synth_calls


def test_empty_text_rejected(tmp_path) -> None:
    server, _ = _make_server(tmp_path)
    client = server._app.test_client()  # noqa: SLF001
    resp = client.post("/api/synthesize", json={"text": "   "})
    assert resp.status_code == 400


def test_oversize_text_rejected(tmp_path) -> None:
    server, _ = _make_server(tmp_path)
    client = server._app.test_client()  # noqa: SLF001
    resp = client.post("/api/synthesize", json={"text": "x" * 20001})
    assert resp.status_code == 413


def test_synthesize_returns_wav_and_caches(tmp_path) -> None:
    server, synth_calls = _make_server(tmp_path)
    client = server._app.test_client()  # noqa: SLF001

    r1 = client.post("/api/synthesize", json={"text": "hello world"})
    assert r1.status_code == 200
    assert r1.mimetype == "audio/wav"
    assert r1.data[:4] == b"RIFF"
    assert r1.headers.get("X-Auto-Speech-Hash")
    assert r1.headers.get("Access-Control-Allow-Origin") == "*"
    assert synth_calls["n"] == 1

    # Identical request → served from cache, no second synthesis.
    r2 = client.post("/api/synthesize", json={"text": "hello world"})
    assert r2.status_code == 200
    assert r2.data == r1.data
    assert synth_calls["n"] == 1


def test_no_speakable_content_returns_422(tmp_path) -> None:
    import tts_engine

    server, _ = _make_server(tmp_path)

    def raise_unspeakable(text, profile, out_path):  # noqa: ANN001
        raise tts_engine.TTSNoSpeakableContentError("no phonemes")

    server._tts.synthesize = raise_unspeakable  # noqa: SLF001
    client = server._app.test_client()  # noqa: SLF001
    resp = client.post("/api/synthesize", json={"text": "★ • #"})
    assert resp.status_code == 422
    assert resp.get_json()["error"] == "no speakable text"


def test_options_preflight(tmp_path) -> None:
    server, _ = _make_server(tmp_path)
    client = server._app.test_client()  # noqa: SLF001
    resp = client.open("/api/synthesize", method="OPTIONS")
    assert resp.status_code == 204
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"
