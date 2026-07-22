"""Unit tests for the /api/synthesize endpoint (Chrome-extension backend).

Verifies the browser-facing synthesize path WITHOUT loading MLX: the
TTSEngine is stubbed to emit a tiny valid WAV. Pins:
  - 400 on empty text, 413 on oversize text
  - 200 audio/wav on success, with the hash header
  - second identical request is served from cache (no re-synthesis)
  - CORS headers present; OPTIONS preflight returns 204

Runs under tests/run_all.sh (no pytest): full mode and the --web lane.
Needs Flask + numpy, so it is listed in NEEDS_DEPS (skipped hermetic).
"""
from __future__ import annotations

import sys
import tempfile
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


def test_empty_text_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        server, _ = _make_server(Path(td))
        client = server._app.test_client()  # noqa: SLF001
        resp = client.post("/api/synthesize", json={"text": "   "})
        assert resp.status_code == 400


def test_oversize_text_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        server, _ = _make_server(Path(td))
        client = server._app.test_client()  # noqa: SLF001
        resp = client.post("/api/synthesize", json={"text": "x" * 20001})
        assert resp.status_code == 413


def test_synthesize_returns_wav_and_caches() -> None:
    with tempfile.TemporaryDirectory() as td:
        server, synth_calls = _make_server(Path(td))
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


def test_no_speakable_content_returns_422() -> None:
    import tts_engine

    with tempfile.TemporaryDirectory() as td:
        server, _ = _make_server(Path(td))

        def raise_unspeakable(text, profile, out_path):  # noqa: ANN001
            raise tts_engine.TTSNoSpeakableContentError("no phonemes")

        server._tts.synthesize = raise_unspeakable  # noqa: SLF001
        client = server._app.test_client()  # noqa: SLF001
        resp = client.post("/api/synthesize", json={"text": "★ • #"})
        assert resp.status_code == 422
        assert resp.get_json()["error"] == "no speakable text"


def test_lang_code_for_voice() -> None:
    from tts_engine import _lang_code_for_voice

    assert _lang_code_for_voice("af_heart") == "a"
    assert _lang_code_for_voice("bm_george") == "b"
    assert _lang_code_for_voice("ef_dora") == "e"
    assert _lang_code_for_voice("zf_xiaoxiao") == "z"
    assert _lang_code_for_voice("") == "a"  # fallback
    assert _lang_code_for_voice("qq_unknown") == "a"  # unknown prefix → fallback


def test_split_span_granularity() -> None:
    from web_server import _split_span

    assert _split_span("One. Two. Three.") == ["One.", "Two.", "Three."]
    assert _split_span("alpha, beta; gamma") == ["alpha", "beta", "gamma"]
    assert _split_span("just four plain words") == ["just four", "plain words"]
    assert _split_span("single") == ["single"]


def test_resilient_split_recovers_from_generate_fault() -> None:
    """A span that trips the generate bug is split finer until it succeeds."""
    import web_server

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        server, _ = _make_server(tmp_path)

        # Simulate the mlx-audio bug: fail on any span longer than 3 words,
        # succeed (emit a tiny WAV) on shorter ones.
        def flaky(text, profile, out_path):  # noqa: ANN001
            if len(text.split()) > 3:
                raise web_server.TTSGenerationError("simulated broadcast bug")
            _write_tiny_wav(Path(out_path))

        server._tts.synthesize = flaky  # noqa: SLF001
        out = tmp_path / "chunk.wav"
        wavs = server._synth_span_resilient(  # noqa: SLF001
            "alpha beta gamma delta epsilon zeta eta", server._profile, out
        )
        assert len(wavs) >= 2
        for w in wavs:
            assert Path(w).exists()


def test_resilient_split_skips_unspeakable_leaf() -> None:
    """An unspeakable fragment is dropped, not fatal, when others speak."""
    import web_server

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        server, _ = _make_server(tmp_path)

        def synth(text, profile, out_path):  # noqa: ANN001
            t = text.strip()
            if t == "★":
                raise web_server.TTSNoSpeakableContentError("no phonemes")
            if len(t.split()) > 1:
                raise web_server.TTSGenerationError("simulated broadcast bug")
            _write_tiny_wav(Path(out_path))

        server._tts.synthesize = synth  # noqa: SLF001
        out = tmp_path / "chunk.wav"
        # Splits to words; "hello" and "world" speak, lone "★" is skipped.
        wavs = server._synth_span_resilient(  # noqa: SLF001
            "hello ★ world", server._profile, out
        )
        assert len(wavs) == 2


def test_options_preflight() -> None:
    with tempfile.TemporaryDirectory() as td:
        server, _ = _make_server(Path(td))
        client = server._app.test_client()  # noqa: SLF001
        resp = client.open("/api/synthesize", method="OPTIONS")
        assert resp.status_code == 204
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"


def main() -> int:
    tests = [
        test_empty_text_rejected,
        test_oversize_text_rejected,
        test_synthesize_returns_wav_and_caches,
        test_no_speakable_content_returns_422,
        test_lang_code_for_voice,
        test_split_span_granularity,
        test_resilient_split_recovers_from_generate_fault,
        test_resilient_split_skips_unspeakable_leaf,
        test_options_preflight,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"/api/synthesize endpoint: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
